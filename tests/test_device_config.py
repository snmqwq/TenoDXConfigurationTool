from __future__ import annotations

import unittest
from dataclasses import replace

from tenodx_config.device_config import (
    GET_INFO_COMMAND,
    HID_KEY_CHOICES,
    KEYBOARD_EK_PARAM,
    KEYBOARD_LAYOUT_PARAM,
    KEYBOARD_MODULE,
    LAYOUT_1P,
    LAYOUT_2P,
    LED_MODULE,
    LED_PER_BIT_PARAM,
    LED_RAINBOW_PARAM,
    READ_COMMAND,
    SAVE_COMMAND,
    TOUCH_BATCH_PARAM,
    TOUCH_CHANNEL_COUNT,
    TOUCH_MAPPING_LENGTH,
    TOUCH_MAPPING_PARAM,
    TOUCH_MODULE,
    TOUCH_ZONE_NAMES,
    WRITE_COMMAND,
    DeviceConfigController,
    DeviceConfigError,
    KeyboardConfig,
    LedConfig,
    TouchConfig,
    TouchMapEntry,
    decode_touch_batch,
    decode_touch_entry,
    decode_touch_mapping,
    encode_touch_batch,
    encode_touch_entry,
    encode_touch_mapping,
    hid_key_name,
    main_keycodes_for_layout,
    parse_hid_key_name,
    touch_zone_mask,
    touch_zone_names,
)
from tenodx_config.magic import MagicResponse


def response(
    module: int,
    command: int,
    param: int,
    payload: bytes = b"",
    *,
    status: int = 0,
) -> MagicResponse:
    return MagicResponse(
        status=status,
        module=module,
        command=command,
        param=param,
        payload=payload,
    )


class FakeMagicClient:
    def __init__(self, port: str, *, timeout: float) -> None:
        self.port = port
        self.timeout = timeout
        self.responses: list[MagicResponse] = []
        self.requests: list[tuple[int, int, int, bytes]] = []
        self.closed = False

    def request(
        self, module: int, command: int, param: int = 0, payload: bytes = b""
    ) -> MagicResponse:
        self.requests.append((module, command, param, payload))
        if not self.responses:
            raise AssertionError("unexpected Magic request")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def sample_touch_config() -> TouchConfig:
    blocks = "ABCDE"
    return TouchConfig(
        entries=tuple(
            TouchMapEntry(zone_mask=1 << channel, block=blocks[channel % 5])
            for channel in range(TOUCH_CHANNEL_COUNT)
        )
    )


class TouchConfigCodecTests(unittest.TestCase):
    def test_zone_order_matches_mai2touch_bit_layout(self) -> None:
        self.assertEqual(len(TOUCH_ZONE_NAMES), 34)
        self.assertEqual(TOUCH_ZONE_NAMES[:3], ("A1", "A2", "A3"))
        self.assertEqual(TOUCH_ZONE_NAMES[15:19], ("B8", "C1", "C2", "D1"))
        self.assertEqual(TOUCH_ZONE_NAMES[-1], "E8")

        mask = touch_zone_mask(("A1", "C2", "E8", "A1"))
        self.assertEqual(mask, (1 << 0) | (1 << 17) | (1 << 33))
        self.assertEqual(touch_zone_names(mask), ("A1", "C2", "E8"))
        self.assertEqual(touch_zone_mask("none"), 0)
        self.assertEqual(touch_zone_names(0), ())

    def test_unknown_zone_and_out_of_range_mask_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown touch zone"):
            touch_zone_mask(("C3",))
        with self.assertRaisesRegex(ValueError, "outside"):
            touch_zone_names(1 << 34)

    def test_entry_is_five_little_endian_mask_bytes_then_block(self) -> None:
        entry = TouchMapEntry(zone_mask=(1 << 33) | 1, block="D")
        encoded = encode_touch_entry(entry)
        self.assertEqual(encoded, bytes((1, 0, 0, 0, 2, ord("D"))))
        self.assertEqual(decode_touch_entry(encoded), entry)

    def test_complete_mapping_is_exactly_204_bytes_and_round_trips(self) -> None:
        config = sample_touch_config()
        encoded = encode_touch_mapping(config)
        self.assertEqual(len(encoded), TOUCH_MAPPING_LENGTH)
        self.assertEqual(decode_touch_mapping(encoded), config)

        with self.assertRaisesRegex(DeviceConfigError, "204"):
            decode_touch_mapping(encoded[:-1])

    def test_batch_is_sorted_and_round_trips_channel_records(self) -> None:
        first = TouchMapEntry(touch_zone_mask(("A1", "B8")), "B")
        last = TouchMapEntry(0, "E")
        encoded = encode_touch_batch({33: last, 0: first})

        self.assertEqual(encoded[0], 0)
        self.assertEqual(encoded[7], 33)
        self.assertEqual(decode_touch_batch(encoded), {0: first, 33: last})

    def test_invalid_batch_records_are_rejected(self) -> None:
        entry = encode_touch_entry(TouchMapEntry(0, "A"))
        with self.assertRaisesRegex(DeviceConfigError, "one or more"):
            decode_touch_batch(b"")
        with self.assertRaisesRegex(DeviceConfigError, "invalid.*34"):
            decode_touch_batch(bytes((34,)) + entry)
        with self.assertRaisesRegex(DeviceConfigError, "duplicate"):
            decode_touch_batch(bytes((1,)) + entry + bytes((1,)) + entry)
        with self.assertRaisesRegex(ValueError, "between 0 and 33"):
            encode_touch_batch({34: TouchMapEntry(0, "A")})

    def test_configuration_models_validate_firmware_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "34 entries"):
            TouchConfig(entries=(TouchMapEntry(0, "A"),))
        with self.assertRaisesRegex(ValueError, "block"):
            TouchMapEntry(0, "F")
        with self.assertRaisesRegex(ValueError, "1 and 4"):
            LedConfig(0, False)
        with self.assertRaisesRegex(TypeError, "bool"):
            LedConfig(2, 1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "four"):
            KeyboardConfig(LAYOUT_1P, (1, 2, 3))  # type: ignore[arg-type]


class KeyboardChoiceTests(unittest.TestCase):
    def test_safe_choices_cover_defaults_and_main_layouts(self) -> None:
        choice_codes = {code for _name, code in HID_KEY_CHOICES}
        self.assertIn(0, choice_codes)
        self.assertIn(0x20, choice_codes)  # 3
        self.assertIn(0x55, choice_codes)  # Keypad *
        self.assertEqual(parse_hid_key_name("3"), 0x20)
        self.assertEqual(parse_hid_key_name("Keypad *"), 0x55)
        self.assertEqual(parse_hid_key_name("none"), 0)

        self.assertEqual(
            main_keycodes_for_layout(LAYOUT_1P),
            (0x1A, 0x08, 0x07, 0x06, 0x1B, 0x1D, 0x04, 0x14),
        )
        self.assertEqual(
            main_keycodes_for_layout(LAYOUT_2P),
            (0x60, 0x61, 0x5E, 0x5B, 0x5A, 0x59, 0x5C, 0x5F),
        )

    def test_unknown_device_keycode_is_displayed_but_cannot_be_parsed(self) -> None:
        config = KeyboardConfig(LAYOUT_1P, (0x20, 0xAB, 0x25, 0))
        self.assertEqual(config.ek_keycodes[1], 0xAB)
        self.assertEqual(hid_key_name(0xAB), "未知 (0xAB)")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            parse_hid_key_name("未知 (0xAB)")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            parse_hid_key_name("0xAB")


class DeviceConfigControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client: FakeMagicClient | None = None

        def factory(port: str, *, timeout: float) -> FakeMagicClient:
            self.client = FakeMagicClient(port, timeout=timeout)
            return self.client

        self.controller = DeviceConfigController(
            "COM7", client_factory=factory, timeout=0.75
        )
        assert self.client is not None

    def test_factory_arguments_context_and_close(self) -> None:
        assert self.client is not None
        self.assertEqual(self.client.port, "COM7")
        self.assertEqual(self.client.timeout, 0.75)
        self.controller.close()
        self.assertTrue(self.client.closed)

    def test_probe_requires_exact_capabilities_for_all_modules(self) -> None:
        assert self.client is not None
        self.client.responses.extend(
            (
                response(
                    TOUCH_MODULE,
                    GET_INFO_COMMAND,
                    0,
                    bytes((1, 2, 3, 34, 6, 7, 1)),
                ),
                response(LED_MODULE, GET_INFO_COMMAND, 0, bytes((1, 2))),
                response(
                    KEYBOARD_MODULE,
                    GET_INFO_COMMAND,
                    0,
                    bytes((12, 8, 4, 0x80, 0x81, 2)),
                ),
            )
        )

        self.controller.probe()

        self.assertEqual(
            self.client.requests,
            [
                (TOUCH_MODULE, GET_INFO_COMMAND, 0, b""),
                (LED_MODULE, GET_INFO_COMMAND, 0, b""),
                (KEYBOARD_MODULE, GET_INFO_COMMAND, 0, b""),
            ],
        )

    def test_probe_rejects_capability_payload_drift(self) -> None:
        assert self.client is not None
        self.client.responses.append(
            response(TOUCH_MODULE, GET_INFO_COMMAND, 0, bytes((1, 2, 3)))
        )
        with self.assertRaisesRegex(DeviceConfigError, "payload mismatch"):
            self.controller.probe()

    def test_response_status_module_command_param_and_length_are_strict(self) -> None:
        assert self.client is not None
        base = response(LED_MODULE, READ_COMMAND, LED_PER_BIT_PARAM, b"\x02")
        cases = (
            (replace(base, status=4), "status"),
            (replace(base, module=TOUCH_MODULE), "module"),
            (replace(base, command=WRITE_COMMAND), "command"),
            (replace(base, param=LED_RAINBOW_PARAM), "param"),
            (replace(base, payload=b"\x02\x03"), "length"),
        )
        for bad_response, message in cases:
            with self.subTest(message=message):
                self.client.responses.append(bad_response)
                with self.assertRaisesRegex(DeviceConfigError, message):
                    self.controller.read_led()

    def test_read_snapshot_decodes_all_modules_and_unknown_ek_value(self) -> None:
        assert self.client is not None
        touch = sample_touch_config()
        self.client.responses.extend(
            (
                response(
                    TOUCH_MODULE,
                    READ_COMMAND,
                    TOUCH_MAPPING_PARAM,
                    encode_touch_mapping(touch),
                ),
                response(LED_MODULE, READ_COMMAND, LED_PER_BIT_PARAM, b"\x03"),
                response(LED_MODULE, READ_COMMAND, LED_RAINBOW_PARAM, b"\x01"),
                response(
                    KEYBOARD_MODULE,
                    READ_COMMAND,
                    KEYBOARD_EK_PARAM,
                    bytes((0x20, 0xAB, 0x25, 0)),
                ),
                response(
                    KEYBOARD_MODULE,
                    READ_COMMAND,
                    KEYBOARD_LAYOUT_PARAM,
                    bytes((LAYOUT_2P,)),
                ),
            )
        )

        snapshot = self.controller.read_snapshot()

        self.assertEqual(snapshot.touch, touch)
        self.assertEqual(snapshot.led, LedConfig(3, True))
        self.assertEqual(
            snapshot.keyboard,
            KeyboardConfig(LAYOUT_2P, (0x20, 0xAB, 0x25, 0)),
        )

    def test_apply_touch_uses_batch_and_empty_change_is_noop(self) -> None:
        assert self.client is not None
        self.controller.apply_touch({})
        self.assertEqual(self.client.requests, [])

        entry = TouchMapEntry(touch_zone_mask(("A1", "E8")), "E")
        self.client.responses.append(
            response(TOUCH_MODULE, WRITE_COMMAND, TOUCH_BATCH_PARAM)
        )
        self.controller.apply_touch({12: entry})
        self.assertEqual(
            self.client.requests[-1],
            (
                TOUCH_MODULE,
                WRITE_COMMAND,
                TOUCH_BATCH_PARAM,
                bytes((12,)) + encode_touch_entry(entry),
            ),
        )

    def test_apply_and_save_led_use_individual_parameters(self) -> None:
        assert self.client is not None
        self.client.responses.extend(
            (
                response(LED_MODULE, WRITE_COMMAND, LED_PER_BIT_PARAM),
                response(LED_MODULE, WRITE_COMMAND, LED_RAINBOW_PARAM),
                response(LED_MODULE, SAVE_COMMAND, 0),
            )
        )

        self.controller.apply_led(LedConfig(4, False))
        self.controller.save_led()

        self.assertEqual(
            self.client.requests,
            [
                (LED_MODULE, WRITE_COMMAND, LED_PER_BIT_PARAM, b"\x04"),
                (LED_MODULE, WRITE_COMMAND, LED_RAINBOW_PARAM, b"\x00"),
                (LED_MODULE, SAVE_COMMAND, 0, b""),
            ],
        )

    def test_apply_and_save_keyboard_preserve_unknown_existing_byte(self) -> None:
        assert self.client is not None
        self.client.responses.extend(
            (
                response(KEYBOARD_MODULE, WRITE_COMMAND, KEYBOARD_EK_PARAM),
                response(KEYBOARD_MODULE, WRITE_COMMAND, KEYBOARD_LAYOUT_PARAM),
                response(KEYBOARD_MODULE, SAVE_COMMAND, 0),
            )
        )
        config = KeyboardConfig(LAYOUT_1P, (0x20, 0xAB, 0x25, 0))

        self.controller.apply_keyboard(config)
        self.controller.save_keyboard()

        self.assertEqual(
            self.client.requests,
            [
                (
                    KEYBOARD_MODULE,
                    WRITE_COMMAND,
                    KEYBOARD_EK_PARAM,
                    bytes((0x20, 0xAB, 0x25, 0)),
                ),
                (
                    KEYBOARD_MODULE,
                    WRITE_COMMAND,
                    KEYBOARD_LAYOUT_PARAM,
                    bytes((LAYOUT_1P,)),
                ),
                (KEYBOARD_MODULE, SAVE_COMMAND, 0, b""),
            ],
        )

    def test_nonempty_write_response_is_rejected(self) -> None:
        assert self.client is not None
        self.client.responses.append(
            response(LED_MODULE, WRITE_COMMAND, LED_PER_BIT_PARAM, b"\x00")
        )
        with self.assertRaisesRegex(DeviceConfigError, "payload mismatch"):
            self.controller.apply_led(LedConfig(2, True))


if __name__ == "__main__":
    unittest.main()
