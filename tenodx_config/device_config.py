"""Typed configuration access over the controller's Magic protocol.

The firmware exposes touch, light, and keyboard settings as three independent
Magic modules.  This module keeps their wire formats and validation separate
from the Tk UI so configuration changes can be tested without hardware.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from .magic import MagicClient, MagicError, MagicResponse

TOUCH_MODULE = 0x10
LED_MODULE = 0x20
KEYBOARD_MODULE = 0x40

READ_COMMAND = 0x01
WRITE_COMMAND = 0x02
SAVE_COMMAND = 0x03
GET_INFO_COMMAND = 0x05

TOUCH_MAPPING_PARAM = 0x01
TOUCH_BATCH_PARAM = 0x03
LED_PER_BIT_PARAM = 0x01
LED_RAINBOW_PARAM = 0x02
KEYBOARD_EK_PARAM = 0x80
KEYBOARD_LAYOUT_PARAM = 0x81
SAVE_ALL_PARAM = 0x00

TOUCH_CHANNEL_COUNT = 34
TOUCH_ENTRY_LENGTH = 2
TOUCH_MAPPING_LENGTH = TOUCH_CHANNEL_COUNT * TOUCH_ENTRY_LENGTH
TOUCH_BATCH_RECORD_LENGTH = TOUCH_ENTRY_LENGTH + 1
TOUCH_PAYLOAD_VERSION = 2

LAYOUT_1P = 0
LAYOUT_2P = 1

TOUCH_ZONE_NAMES = (
    *(f"A{number}" for number in range(1, 9)),
    *(f"B{number}" for number in range(1, 9)),
    "C1",
    "C2",
    *(f"D{number}" for number in range(1, 9)),
    *(f"E{number}" for number in range(1, 9)),
)
_TOUCH_ZONE_INDICES = {
    name: index for index, name in enumerate(TOUCH_ZONE_NAMES)
}


def _hid_choices() -> tuple[tuple[str, int], ...]:
    choices: list[tuple[str, int]] = [("None（禁用）", 0x00)]
    choices.extend((chr(ord("A") + index), 0x04 + index) for index in range(26))
    choices.extend((str(number), 0x1D + number) for number in range(1, 10))
    choices.append(("0", 0x27))
    choices.extend(
        (
            ("Enter", 0x28),
            ("Esc", 0x29),
            ("Backspace", 0x2A),
            ("Tab", 0x2B),
            ("Space", 0x2C),
            ("-", 0x2D),
            ("=", 0x2E),
            ("[", 0x2F),
            ("]", 0x30),
            ("\\", 0x31),
            (";", 0x33),
            ("'", 0x34),
            ("`", 0x35),
            (",", 0x36),
            (".", 0x37),
            ("/", 0x38),
            ("Caps Lock", 0x39),
        )
    )
    choices.extend((f"F{number}", 0x39 + number) for number in range(1, 13))
    choices.extend(
        (
            ("Print Screen", 0x46),
            ("Scroll Lock", 0x47),
            ("Pause", 0x48),
            ("Insert", 0x49),
            ("Home", 0x4A),
            ("Page Up", 0x4B),
            ("Delete", 0x4C),
            ("End", 0x4D),
            ("Page Down", 0x4E),
            ("Right", 0x4F),
            ("Left", 0x50),
            ("Down", 0x51),
            ("Up", 0x52),
            ("Keypad Num Lock", 0x53),
            ("Keypad /", 0x54),
            ("Keypad *", 0x55),
            ("Keypad -", 0x56),
            ("Keypad +", 0x57),
            ("Keypad Enter", 0x58),
        )
    )
    choices.extend((f"Keypad {number}", 0x58 + number) for number in range(1, 10))
    choices.extend((("Keypad 0", 0x62), ("Keypad .", 0x63)))
    return tuple(choices)


HID_KEY_CHOICES = _hid_choices()
_HID_NAMES_BY_CODE = {code: name for name, code in HID_KEY_CHOICES}
_HID_CODES_BY_NAME = {name.casefold(): code for name, code in HID_KEY_CHOICES}
_HID_CODES_BY_NAME.update({"none": 0, "disabled": 0, "禁用": 0})

_MAIN_KEYCODES = {
    LAYOUT_1P: (0x1A, 0x08, 0x07, 0x06, 0x1B, 0x1D, 0x04, 0x14),
    LAYOUT_2P: (0x60, 0x61, 0x5E, 0x5B, 0x5A, 0x59, 0x5C, 0x5F),
}


class DeviceConfigError(MagicError):
    """Raised when a configuration response or value is incompatible."""


def _require_plain_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _require_byte(value: object, name: str) -> int:
    integer = _require_plain_int(value, name)
    if not 0 <= integer <= 0xFF:
        raise ValueError(f"{name} must be between 0 and 255")
    return integer


def _payload_bytes(payload: object, name: str = "payload") -> bytes:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    return bytes(payload)


@dataclass(frozen=True)
class TouchMapEntry:
    """One physical channel's single Mai2Touch region and derived block."""

    zone: str

    def __post_init__(self) -> None:
        if not isinstance(self.zone, str):
            raise TypeError("zone must be a string")
        zone = self.zone.strip().upper()
        if zone not in _TOUCH_ZONE_INDICES:
            raise ValueError(f"unknown touch zone: {self.zone}")
        object.__setattr__(self, "zone", zone)

    @property
    def zone_index(self) -> int:
        return _TOUCH_ZONE_INDICES[self.zone]

    @property
    def block(self) -> str:
        return self.zone[0]


@dataclass(frozen=True)
class TouchConfig:
    """The complete mapping for all 34 physical touch channels."""

    entries: tuple[TouchMapEntry, ...]

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if len(entries) != TOUCH_CHANNEL_COUNT:
            raise ValueError(
                f"touch configuration must contain {TOUCH_CHANNEL_COUNT} entries"
            )
        if any(not isinstance(entry, TouchMapEntry) for entry in entries):
            raise TypeError("touch configuration entries must be TouchMapEntry values")
        object.__setattr__(self, "entries", entries)


@dataclass(frozen=True)
class LedConfig:
    """Configurable Mai2LED physical layout and idle rainbow setting."""

    led_per_bit: int
    rainbow_enabled: bool

    def __post_init__(self) -> None:
        led_per_bit = _require_plain_int(self.led_per_bit, "led_per_bit")
        if not 1 <= led_per_bit <= 4:
            raise ValueError("led_per_bit must be between 1 and 4")
        if not isinstance(self.rainbow_enabled, bool):
            raise TypeError("rainbow_enabled must be a bool")


@dataclass(frozen=True)
class KeyboardConfig:
    """Main-player layout and the four configurable EK keycodes.

    Unknown byte values read from an older or externally configured device are
    intentionally retained.  User-facing parsing only accepts
    :data:`HID_KEY_CHOICES`, so the UI cannot create arbitrary raw values.
    """

    main_layout: int
    ek_keycodes: tuple[int, int, int, int]

    def __post_init__(self) -> None:
        layout = _require_plain_int(self.main_layout, "main_layout")
        if layout not in _MAIN_KEYCODES:
            raise ValueError("main_layout must be LAYOUT_1P or LAYOUT_2P")
        keycodes = tuple(self.ek_keycodes)
        if len(keycodes) != 4:
            raise ValueError("ek_keycodes must contain exactly four values")
        for index, keycode in enumerate(keycodes):
            _require_byte(keycode, f"ek_keycodes[{index}]")
        object.__setattr__(self, "ek_keycodes", keycodes)


@dataclass(frozen=True)
class DeviceConfigSnapshot:
    touch: TouchConfig
    led: LedConfig
    keyboard: KeyboardConfig

    def __post_init__(self) -> None:
        if not isinstance(self.touch, TouchConfig):
            raise TypeError("touch must be a TouchConfig")
        if not isinstance(self.led, LedConfig):
            raise TypeError("led must be a LedConfig")
        if not isinstance(self.keyboard, KeyboardConfig):
            raise TypeError("keyboard must be a KeyboardConfig")


def touch_zone_index(name: str) -> int:
    """Return the firmware region index for one validated zone name."""

    return TouchMapEntry(name).zone_index


def touch_zone_name(index: int) -> str:
    """Return the zone name for one firmware region index."""

    value = _require_plain_int(index, "touch zone index")
    if not 0 <= value < len(TOUCH_ZONE_NAMES):
        raise ValueError("touch zone index must be between 0 and 33")
    return TOUCH_ZONE_NAMES[value]


def hid_key_name(keycode: int) -> str:
    """Return a safe display name, retaining unknown device bytes read-only."""

    value = _require_byte(keycode, "keycode")
    return _HID_NAMES_BY_CODE.get(value, f"未知 (0x{value:02X})")


def parse_hid_key_name(name: str) -> int:
    """Resolve a listed HID name; hexadecimal/raw keycode input is rejected."""

    if not isinstance(name, str):
        raise TypeError("HID key name must be a string")
    try:
        return _HID_CODES_BY_NAME[name.strip().casefold()]
    except KeyError as error:
        raise ValueError(f"unknown or unsupported HID key name: {name}") from error


def main_keycodes_for_layout(layout: int) -> tuple[int, ...]:
    """Return the fixed BTN1-BTN8 keycodes for a 1P or 2P layout."""

    value = _require_plain_int(layout, "layout")
    try:
        return _MAIN_KEYCODES[value]
    except KeyError as error:
        raise ValueError("layout must be LAYOUT_1P or LAYOUT_2P") from error


def encode_touch_entry(entry: TouchMapEntry) -> bytes:
    if not isinstance(entry, TouchMapEntry):
        raise TypeError("entry must be a TouchMapEntry")
    return bytes((entry.zone_index, ord(entry.block)))


def decode_touch_entry(payload: bytes) -> TouchMapEntry:
    raw = _payload_bytes(payload)
    if len(raw) != TOUCH_ENTRY_LENGTH:
        raise DeviceConfigError(
            f"touch entry length mismatch: {len(raw)} != {TOUCH_ENTRY_LENGTH}"
        )
    try:
        zone = touch_zone_name(raw[0])
    except ValueError as error:
        raise DeviceConfigError(f"invalid touch entry: {error}") from error
    if raw[1] != ord(zone[0]):
        raise DeviceConfigError(
            f"touch entry block {raw[1]:#04x} does not match zone {zone}"
        )
    return TouchMapEntry(zone)


def encode_touch_mapping(config: TouchConfig) -> bytes:
    if not isinstance(config, TouchConfig):
        raise TypeError("config must be a TouchConfig")
    return b"".join(encode_touch_entry(entry) for entry in config.entries)


def decode_touch_mapping(payload: bytes) -> TouchConfig:
    raw = _payload_bytes(payload)
    if len(raw) != TOUCH_MAPPING_LENGTH:
        raise DeviceConfigError(
            f"touch mapping length mismatch: {len(raw)} != {TOUCH_MAPPING_LENGTH}"
        )
    return TouchConfig(
        entries=tuple(
            decode_touch_entry(raw[offset : offset + TOUCH_ENTRY_LENGTH])
            for offset in range(0, len(raw), TOUCH_ENTRY_LENGTH)
        )
    )


def encode_touch_batch(changes: Mapping[int, TouchMapEntry]) -> bytes:
    if not isinstance(changes, Mapping):
        raise TypeError("changes must be a channel-to-entry mapping")
    if len(changes) > TOUCH_CHANNEL_COUNT:
        raise ValueError("touch batch contains more than 34 channels")

    validated: list[tuple[int, TouchMapEntry]] = []
    for raw_channel, entry in changes.items():
        channel = _require_plain_int(raw_channel, "touch channel")
        if not 0 <= channel < TOUCH_CHANNEL_COUNT:
            raise ValueError("touch channel must be between 0 and 33")
        if not isinstance(entry, TouchMapEntry):
            raise TypeError("touch batch entries must be TouchMapEntry values")
        validated.append((channel, entry))

    records = bytearray()
    for channel, entry in sorted(validated):
        records.append(channel)
        records.extend(encode_touch_entry(entry))
    return bytes(records)


def decode_touch_batch(payload: bytes) -> dict[int, TouchMapEntry]:
    raw = _payload_bytes(payload)
    if not raw or len(raw) % TOUCH_BATCH_RECORD_LENGTH:
        raise DeviceConfigError(
            f"touch batch must contain one or more {TOUCH_BATCH_RECORD_LENGTH}-byte records"
        )
    if len(raw) > TOUCH_CHANNEL_COUNT * TOUCH_BATCH_RECORD_LENGTH:
        raise DeviceConfigError("touch batch contains more than 34 records")

    changes: dict[int, TouchMapEntry] = {}
    for offset in range(0, len(raw), TOUCH_BATCH_RECORD_LENGTH):
        channel = raw[offset]
        if channel >= TOUCH_CHANNEL_COUNT:
            raise DeviceConfigError(f"invalid touch batch channel: {channel}")
        if channel in changes:
            raise DeviceConfigError(f"duplicate touch batch channel: {channel}")
        changes[channel] = decode_touch_entry(
            raw[offset + 1 : offset + TOUCH_BATCH_RECORD_LENGTH]
        )
    return changes


class DeviceConfigController:
    """Synchronous owner of one Magic serial client used for configuration."""

    _PROBE_PAYLOADS = (
        (
            TOUCH_MODULE,
            bytes(
                (
                    TOUCH_MAPPING_PARAM,
                    0x02,
                    TOUCH_BATCH_PARAM,
                    TOUCH_CHANNEL_COUNT,
                    TOUCH_ENTRY_LENGTH,
                    TOUCH_BATCH_RECORD_LENGTH,
                    TOUCH_PAYLOAD_VERSION,
                )
            ),
        ),
        (LED_MODULE, bytes((0x01, 0x02))),
        (KEYBOARD_MODULE, bytes((12, 8, 4, 0x80, 0x81, 2))),
    )

    def __init__(
        self,
        port: str,
        *,
        client_factory: Any = MagicClient,
        timeout: float = 1.0,
    ) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("port must be a non-empty string")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a positive number")
        if timeout <= 0:
            raise ValueError("timeout must be a positive number")
        self.port = port
        self.client = client_factory(port, timeout=float(timeout))

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def _request(
        self,
        module: int,
        command: int,
        param: int,
        payload: bytes = b"",
        *,
        expected_payload: bytes | None = None,
        expected_length: int | None = None,
    ) -> MagicResponse:
        response = self.client.request(module, command, param, payload)
        for field, expected in (
            ("module", module),
            ("command", command),
            ("param", param),
        ):
            actual = getattr(response, field, None)
            if (
                isinstance(actual, bool)
                or not isinstance(actual, int)
                or actual != expected
            ):
                actual_text = (
                    f"0x{actual:02X}"
                    if isinstance(actual, int) and not isinstance(actual, bool)
                    else repr(actual)
                )
                raise DeviceConfigError(
                    f"Magic response {field} mismatch: "
                    f"{actual_text} != 0x{expected:02X}"
                )
        status = getattr(response, "status", None)
        if status != 0 or isinstance(status, bool):
            detail = f"0x{status:02X}" if isinstance(status, int) else repr(status)
            raise DeviceConfigError(
                f"Magic configuration command was rejected with status {detail}"
            )
        response_payload = getattr(response, "payload", None)
        if not isinstance(response_payload, bytes):
            raise DeviceConfigError("Magic response payload is not bytes")
        if expected_payload is not None and response_payload != expected_payload:
            raise DeviceConfigError(
                "Magic response payload mismatch: "
                f"{response_payload.hex(' ')} != {expected_payload.hex(' ')}"
            )
        if expected_length is not None and len(response_payload) != expected_length:
            raise DeviceConfigError(
                "Magic response payload length mismatch: "
                f"{len(response_payload)} != {expected_length}"
            )
        return response

    def probe(self) -> None:
        """Require exact capability descriptions from all three modules."""

        for module, expected in self._PROBE_PAYLOADS:
            self._request(
                module,
                GET_INFO_COMMAND,
                SAVE_ALL_PARAM,
                expected_payload=expected,
            )

    def read_snapshot(self) -> DeviceConfigSnapshot:
        return DeviceConfigSnapshot(
            touch=self.read_touch(),
            led=self.read_led(),
            keyboard=self.read_keyboard(),
        )

    def read_touch(self) -> TouchConfig:
        response = self._request(
            TOUCH_MODULE,
            READ_COMMAND,
            TOUCH_MAPPING_PARAM,
            expected_length=TOUCH_MAPPING_LENGTH,
        )
        return decode_touch_mapping(response.payload)

    def apply_touch(self, changes: Mapping[int, TouchMapEntry]) -> None:
        payload = encode_touch_batch(changes)
        if not payload:
            return
        self._request(
            TOUCH_MODULE,
            WRITE_COMMAND,
            TOUCH_BATCH_PARAM,
            payload,
            expected_payload=b"",
        )

    def save_touch(self) -> None:
        self._save(TOUCH_MODULE)

    def read_led(self) -> LedConfig:
        led_per_bit = self._request(
            LED_MODULE,
            READ_COMMAND,
            LED_PER_BIT_PARAM,
            expected_length=1,
        ).payload[0]
        rainbow = self._request(
            LED_MODULE,
            READ_COMMAND,
            LED_RAINBOW_PARAM,
            expected_length=1,
        ).payload[0]
        if rainbow not in (0, 1):
            raise DeviceConfigError(f"invalid LED rainbow value: {rainbow}")
        try:
            return LedConfig(led_per_bit=led_per_bit, rainbow_enabled=bool(rainbow))
        except (TypeError, ValueError) as error:
            raise DeviceConfigError(f"invalid LED configuration: {error}") from error

    def apply_led(self, config: LedConfig) -> None:
        if not isinstance(config, LedConfig):
            raise TypeError("config must be a LedConfig")
        self._write_byte(LED_MODULE, LED_PER_BIT_PARAM, config.led_per_bit)
        self._write_byte(LED_MODULE, LED_RAINBOW_PARAM, int(config.rainbow_enabled))

    def save_led(self) -> None:
        self._save(LED_MODULE)

    def read_keyboard(self) -> KeyboardConfig:
        keycodes = self._request(
            KEYBOARD_MODULE,
            READ_COMMAND,
            KEYBOARD_EK_PARAM,
            expected_length=4,
        ).payload
        layout = self._request(
            KEYBOARD_MODULE,
            READ_COMMAND,
            KEYBOARD_LAYOUT_PARAM,
            expected_length=1,
        ).payload[0]
        try:
            return KeyboardConfig(main_layout=layout, ek_keycodes=tuple(keycodes))  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise DeviceConfigError(
                f"invalid keyboard configuration: {error}"
            ) from error

    def apply_keyboard(self, config: KeyboardConfig) -> None:
        if not isinstance(config, KeyboardConfig):
            raise TypeError("config must be a KeyboardConfig")
        self._request(
            KEYBOARD_MODULE,
            WRITE_COMMAND,
            KEYBOARD_EK_PARAM,
            bytes(config.ek_keycodes),
            expected_payload=b"",
        )
        self._write_byte(KEYBOARD_MODULE, KEYBOARD_LAYOUT_PARAM, config.main_layout)

    def save_keyboard(self) -> None:
        self._save(KEYBOARD_MODULE)

    def _write_byte(self, module: int, param: int, value: int) -> None:
        self._request(
            module,
            WRITE_COMMAND,
            param,
            bytes((_require_byte(value, "configuration value"),)),
            expected_payload=b"",
        )

    def _save(self, module: int) -> None:
        self._request(
            module,
            SAVE_COMMAND,
            SAVE_ALL_PARAM,
            expected_payload=b"",
        )
