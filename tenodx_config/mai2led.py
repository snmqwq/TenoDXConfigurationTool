"""Portable Mai2LED protocol and eight-logical-light controller.

This module implements the 837-15070 framing used by the controller's
Mai2LED CDC interface.  It deliberately contains no GUI, firmware
configuration, or physical-pixel assumptions.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Self

import serial

SYNC = 0xE0
MARKER = 0xD0

BAUDRATE = 115200
DEFAULT_DST_NODE = 0x11
DEFAULT_SRC_NODE = 0x01
MAX_REQUEST_PAYLOAD = 34

SET_LED_GS_8BIT_MULTI = 0x32
SET_LED_GS_8BIT_MULTI_FADE = 0x33
SET_LED_GS_UPDATE = 0x3C
GET_BOARD_INFO = 0xF0

ACK_STATUS_OK = 0x01
ACK_REPORT_OK = 0x01
SUPPORTED_BOARD_NUMBER = "15070-04"

LOGICAL_LIGHT_COUNT = 8
BLACK = (0, 0, 0)

SERIAL_READ_TIMEOUT_SECONDS = 0.02
SERIAL_WRITE_TIMEOUT_SECONDS = 0.5
SERIAL_SETTLE_SECONDS = 0.05
ACK_TIMEOUT_SECONDS = 0.5
EMPTY_READ_SLEEP_SECONDS = 0.001

RGB = tuple[int, int, int]
SerialFactory = Callable[..., Any]


class Mai2LedError(RuntimeError):
    """Base class for Mai2LED connection and protocol failures."""


class Mai2LedConnectionError(Mai2LedError):
    """Raised when the serial transport cannot be opened or used."""


class Mai2LedProtocolError(Mai2LedError):
    """Raised when a Mai2LED frame or acknowledgement is invalid."""


class Mai2LedTimeoutError(Mai2LedProtocolError):
    """Raised when the controller does not acknowledge a command in time."""


@dataclass(frozen=True)
class Mai2LedAck:
    """One decoded Mai2LED acknowledgement."""

    dst_node: int
    src_node: int
    status: int
    command: int
    report: int
    payload: bytes


@dataclass(frozen=True)
class Mai2LedBoardInfo:
    """Board identity returned by ``GetBoardInfo``."""

    board_number: str
    firmware_revision: int


def validate_rgb(color: Iterable[int]) -> RGB:
    """Return a validated RGB triple whose channels are integers in 0..255."""

    try:
        values = tuple(color)
    except TypeError as error:
        raise ValueError("RGB must contain exactly three integer channels") from error
    if len(values) != 3 or any(
        isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255
        for value in values
    ):
        raise ValueError("RGB must contain exactly three integers from 0 to 255")
    return values  # type: ignore[return-value]


def fade_speed_for_duration(duration_ms: int) -> int:
    """Convert a requested fade duration to the protocol's one-byte speed."""

    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
        raise ValueError("fade duration must be a positive integer in milliseconds")
    if duration_ms <= 0:
        raise ValueError("fade duration must be a positive integer in milliseconds")
    return max(1, min(255, round((4095 * 8) / duration_ms)))


def _validate_byte(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be an integer byte")


def _escape_body(body: bytes) -> bytes:
    encoded = bytearray()
    for value in body:
        if value in (SYNC, MARKER):
            encoded.extend((MARKER, (value - 1) & 0xFF))
        else:
            encoded.append(value)
    return bytes(encoded)


def build_request(
    command: int,
    payload: bytes = b"",
    *,
    dst_node: int = DEFAULT_DST_NODE,
    src_node: int = DEFAULT_SRC_NODE,
) -> bytes:
    """Build one E0/D0-framed Mai2LED request.

    The firmware does not escape the checksum and interprets a checksum equal
    to E0 or D0 as framing.  Node IDs are ignored by the supported controller,
    so the source node is adjusted only when needed to produce a safe checksum.
    """

    _validate_byte(command, "command")
    _validate_byte(dst_node, "destination node")
    _validate_byte(src_node, "source node")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload must be bytes-like")
    payload_bytes = bytes(payload)
    if len(payload_bytes) > MAX_REQUEST_PAYLOAD:
        raise ValueError(
            f"payload is too long: {len(payload_bytes)} > {MAX_REQUEST_PAYLOAD}"
        )

    candidate_nodes = (
        src_node,
        *(value for value in range(1, 0x100) if value != src_node),
    )
    for candidate_src in candidate_nodes:
        body = (
            bytes((dst_node, candidate_src, len(payload_bytes) + 1, command))
            + payload_bytes
        )
        checksum = sum(body) & 0xFF
        if checksum not in (SYNC, MARKER):
            return bytes((SYNC,)) + _escape_body(body) + bytes((checksum,))

    raise Mai2LedProtocolError("unable to construct a frame with a safe checksum")


def _try_extract_ack(buffer: bytearray) -> Mai2LedAck | None:
    """Remove and return one complete acknowledgement from a byte stream."""

    while True:
        try:
            sync_index = buffer.index(SYNC)
        except ValueError:
            buffer.clear()
            return None

        if sync_index:
            del buffer[:sync_index]

        decoded = bytearray()
        raw_index = 1
        expected_body_length: int | None = None
        restart = False

        while raw_index < len(buffer):
            # ACK checksums are raw bytes, including E0/D0.  Once the declared
            # body is complete, consume the next raw byte before framing rules.
            if (
                expected_body_length is not None
                and len(decoded) == expected_body_length
            ):
                checksum = buffer[raw_index]
                del buffer[: raw_index + 1]
                body = bytes(decoded)
                expected_checksum = sum(body) & 0xFF
                if checksum != expected_checksum:
                    raise Mai2LedProtocolError(
                        "Mai2LED acknowledgement checksum mismatch: "
                        f"0x{checksum:02X} != 0x{expected_checksum:02X}"
                    )
                return Mai2LedAck(
                    dst_node=body[0],
                    src_node=body[1],
                    status=body[3],
                    command=body[4],
                    report=body[5],
                    payload=body[6:],
                )

            value = buffer[raw_index]
            if value == SYNC:
                del buffer[:raw_index]
                restart = True
                break
            if value == MARKER:
                if raw_index + 1 >= len(buffer):
                    return None
                value = (buffer[raw_index + 1] + 1) & 0xFF
                raw_index += 2
            else:
                raw_index += 1

            decoded.append(value)
            if len(decoded) == 3:
                declared_length = decoded[2]
                expected_body_length = declared_length + 3
                if declared_length < 3 or expected_body_length > 64:
                    del buffer[0]
                    raise Mai2LedProtocolError(
                        f"invalid Mai2LED acknowledgement length: {declared_length}"
                    )
            elif (
                expected_body_length is not None and len(decoded) > expected_body_length
            ):
                del buffer[0]
                raise Mai2LedProtocolError(
                    "Mai2LED acknowledgement exceeds its declared length"
                )

        if restart:
            continue
        return None


def parse_ack(frame: bytes) -> Mai2LedAck:
    """Decode one complete acknowledgement, allowing noise before its E0 sync."""

    if not isinstance(frame, (bytes, bytearray, memoryview)):
        raise TypeError("frame must be bytes-like")
    buffer = bytearray(frame)
    ack = _try_extract_ack(buffer)
    if ack is None:
        raise Mai2LedProtocolError("incomplete Mai2LED acknowledgement")
    if buffer:
        raise Mai2LedProtocolError("unexpected data after Mai2LED acknowledgement")
    return ack


class Mai2LedController:
    """Synchronous controller for one Mai2LED serial interface."""

    def __init__(
        self,
        port: str,
        *,
        timeout: float = ACK_TIMEOUT_SECONDS,
        serial_factory: SerialFactory = serial.Serial,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not port or not isinstance(port, str):
            raise ValueError("port must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self.port = port
        self.timeout = timeout
        self._sleeper = sleeper
        self._clock = clock
        self._rx_buffer = bytearray()
        self._io_lock = threading.Lock()
        try:
            self.serial = serial_factory(
                port=port,
                baudrate=BAUDRATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=SERIAL_READ_TIMEOUT_SECONDS,
                write_timeout=SERIAL_WRITE_TIMEOUT_SECONDS,
            )
        except Exception as error:
            raise Mai2LedConnectionError(
                f"unable to open Mai2LED serial port {port}: {error}"
            ) from error

        self._sleeper(SERIAL_SETTLE_SECONDS)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return bool(getattr(self.serial, "is_open", True))

    def close(self) -> None:
        """Close the serial transport; calling this repeatedly is safe."""

        with self._io_lock:
            if not self.is_open:
                return
            try:
                self.serial.close()
            except Exception as error:
                raise Mai2LedConnectionError(
                    f"unable to close Mai2LED serial port {self.port}: {error}"
                ) from error

    def _read_ack(self) -> Mai2LedAck:
        deadline = self._clock() + self.timeout
        while self._clock() < deadline:
            ack = _try_extract_ack(self._rx_buffer)
            if ack is not None:
                return ack

            try:
                waiting = int(getattr(self.serial, "in_waiting", 0))
                chunk = self.serial.read(waiting if waiting > 0 else 1)
            except Exception as error:
                raise Mai2LedConnectionError(
                    f"failed to read Mai2LED serial port {self.port}: {error}"
                ) from error
            if chunk:
                self._rx_buffer.extend(chunk)
            else:
                remaining = deadline - self._clock()
                if remaining > 0:
                    self._sleeper(min(EMPTY_READ_SLEEP_SECONDS, remaining))

        raise Mai2LedTimeoutError(
            f"Mai2LED command acknowledgement timed out on {self.port}"
        )

    def command(self, command: int, payload: bytes = b"") -> Mai2LedAck:
        """Send one command and require a matching successful ACK."""

        frame = build_request(command, payload)
        with self._io_lock:
            if not self.is_open:
                raise Mai2LedConnectionError(
                    f"Mai2LED serial port is closed: {self.port}"
                )
            self._rx_buffer.clear()
            try:
                self.serial.reset_input_buffer()
                written = self.serial.write(frame)
                self.serial.flush()
            except Exception as error:
                raise Mai2LedConnectionError(
                    f"failed to write Mai2LED serial port {self.port}: {error}"
                ) from error
            if written != len(frame):
                raise Mai2LedConnectionError(
                    "Mai2LED serial short write: "
                    f"expected {len(frame)} bytes, wrote {written}"
                )
            ack = self._read_ack()

        if ack.command != command:
            raise Mai2LedProtocolError(
                "Mai2LED acknowledgement command mismatch: "
                f"sent 0x{command:02X}, received 0x{ack.command:02X}"
            )
        if ack.status != ACK_STATUS_OK or ack.report != ACK_REPORT_OK:
            raise Mai2LedProtocolError(
                "Mai2LED command rejected: "
                f"status=0x{ack.status:02X}, report=0x{ack.report:02X}"
            )
        return ack

    def probe(self) -> Mai2LedBoardInfo:
        """Verify that the port is a supported 15070-04 Mai2LED board."""

        ack = self.command(GET_BOARD_INFO)
        try:
            terminator = ack.payload.index(0xFF)
        except ValueError as error:
            raise Mai2LedProtocolError(
                "Mai2LED board information is missing its terminator"
            ) from error
        if terminator == 0 or terminator + 1 >= len(ack.payload):
            raise Mai2LedProtocolError("Mai2LED board information is incomplete")
        try:
            board_number = ack.payload[:terminator].decode("ascii")
        except UnicodeDecodeError as error:
            raise Mai2LedProtocolError(
                "Mai2LED board number is not valid ASCII"
            ) from error
        if board_number != SUPPORTED_BOARD_NUMBER:
            raise Mai2LedProtocolError(
                f"unsupported Mai2LED board: {board_number or '<empty>'}"
            )
        return Mai2LedBoardInfo(
            board_number=board_number,
            firmware_revision=ack.payload[terminator + 1],
        )

    def set_all(self, color: Iterable[int]) -> None:
        """Set all eight logical lights and commit one frame."""

        red, green, blue = validate_rgb(color)
        self.command(
            SET_LED_GS_8BIT_MULTI,
            bytes((0, LOGICAL_LIGHT_COUNT, 0, red, green, blue, 0)),
        )
        self.command(SET_LED_GS_UPDATE)

    def set_chase_frame(self, index: int, color: Iterable[int]) -> None:
        """Stage black plus one logical light, then commit exactly once."""

        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("logical light index must be an integer from 0 to 7")
        if not 0 <= index < LOGICAL_LIGHT_COUNT:
            raise ValueError("logical light index must be from 0 to 7")
        red, green, blue = validate_rgb(color)
        self.command(
            SET_LED_GS_8BIT_MULTI,
            bytes((0, LOGICAL_LIGHT_COUNT, 0, 0, 0, 0, 0)),
        )
        self.command(
            SET_LED_GS_8BIT_MULTI,
            bytes((index, index + 1, 0, red, green, blue, 0)),
        )
        self.command(SET_LED_GS_UPDATE)

    def fade_all(
        self,
        start_color: Iterable[int],
        end_color: Iterable[int],
        duration_ms: int,
    ) -> None:
        """Stage a start and end color for all lights, then start one fade."""

        start_red, start_green, start_blue = validate_rgb(start_color)
        end_red, end_green, end_blue = validate_rgb(end_color)
        speed = fade_speed_for_duration(duration_ms)
        self.command(
            SET_LED_GS_8BIT_MULTI,
            bytes(
                (
                    0,
                    LOGICAL_LIGHT_COUNT,
                    0,
                    start_red,
                    start_green,
                    start_blue,
                    0,
                )
            ),
        )
        self.command(
            SET_LED_GS_8BIT_MULTI_FADE,
            bytes(
                (
                    0,
                    LOGICAL_LIGHT_COUNT,
                    0,
                    end_red,
                    end_green,
                    end_blue,
                    speed,
                )
            ),
        )
        self.command(SET_LED_GS_UPDATE)
