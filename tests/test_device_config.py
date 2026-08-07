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
    LOAD_DEFAULT_COMMAND,
    READ_COMMAND,
    SAVE_COMMAND,
    TOUCH_BATCH_PARAM,
    TOUCH_CDC_MODE_MAI2TOUCH,
    TOUCH_CDC_MODE_PARAM,
    TOUCH_CDC_MODE_RAW,
    TOUCH_CHANNEL_COUNT,
    TOUCH_MAPPING_LENGTH,
    TOUCH_MAPPING_PARAM,
    TOUCH_MODULE,
    TOUCH_PSOC_STATUS_PARAM,
    TOUCH_STATUS_PAYLOAD_LENGTH,
    TOUCH_ZONE_NAMES,
    WRITE_COMMAND,
    DeviceConfigController,
    DeviceConfigError,
    KeyboardConfig,
    LedConfig,
    PsocRuntimeStatus,
    TouchConfig,
    TouchMapEntry,
    TouchRuntimeStatus,
    decode_touch_batch,
    decode_touch_cdc_mode,
    decode_touch_entry,
    decode_touch_mapping,
    decode_touch_runtime_status,
    encode_touch_batch,
    encode_touch_cdc_mode,
    encode_touch_entry,
    encode_touch_mapping,
    hid_key_name,
    main_keycodes_for_layout,
    parse_hid_key_name,
    touch_zone_index,
    touch_zone_name,
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


def sample_touch_config(
    cdc_mode: int = TOUCH_CDC_MODE_MAI2TOUCH,
) -> TouchConfig:
    return TouchConfig(
        entries=tuple(
            TouchMapEntry(zone=TOUCH_ZONE_NAMES[channel])
            for channel in range(TOUCH_CHANNEL_COUNT)
        ),
        cdc_mode=cdc_mode,
    )


class TouchConfigCodecTests(unittest.TestCase):
    def test_zone_order_matches_mai2touch_bit_layout(self) -> None:
        self.assertEqual(len(TOUCH_ZONE_NAMES), 34)
        self.assertEqual(TOUCH_ZONE_NAMES[:3], ("A1", "A2", "A3"))
        self.assertEqual(TOUCH_ZONE_NAMES[15:19], ("B8", "C1", "C2", "D1"))
        self.assertEqual(TOUCH_ZONE_NAMES[-1], "E8")

        boundaries = (
            ("A1", 0, "A"),
            ("A8", 7, "A"),
            ("B1", 8, "B"),
            ("B8", 15, "B"),
            ("C1", 16, "C"),
            ("C2", 17, "C"),
            ("D1", 18, "D"),
            ("D8", 25, "D"),
            ("E1", 26, "E"),
            ("E8", 33, "E"),
        )
        for zone, index, block in boundaries:
            with self.subTest(zone=zone):
                self.assertEqual(touch_zone_index(zone), index)
                self.assertEqual(touch_zone_name(index), zone)
                self.assertEqual(TouchMapEntry(zone).block, block)

        self.assertEqual(touch_zone_index(" c2 "), 17)

    def test_unknown_zone_and_out_of_range_index_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown touch zone"):
            touch_zone_index("C3")
        with self.assertRaisesRegex(ValueError, "between 0 and 33"):
            touch_zone_name(34)

    def test_entry_is_region_index_then_derived_block(self) -> None:
        entry = TouchMapEntry(zone="E8")
        encoded = encode_touch_entry(entry)
        self.assertEqual(encoded, bytes((33, ord("E"))))
        self.assertEqual(decode_touch_entry(encoded), entry)
        with self.assertRaisesRegex(DeviceConfigError, "does not match"):
            decode_touch_entry(bytes((33, ord("A"))))
        with self.assertRaisesRegex(DeviceConfigError, "between 0 and 33"):
            decode_touch_entry(bytes((34, ord("E"))))

    def test_complete_mapping_is_exactly_68_bytes_and_round_trips(self) -> None:
        config = sample_touch_config()
        encoded = encode_touch_mapping(config)
        self.assertEqual(len(encoded), TOUCH_MAPPING_LENGTH)
        self.assertEqual(decode_touch_mapping(encoded), config)

        with self.assertRaisesRegex(DeviceConfigError, "68"):
            decode_touch_mapping(encoded[:-1])

    def test_cdc_mode_codec_accepts_only_raw_and_mai2touch(self) -> None:
        self.assertEqual(encode_touch_cdc_mode(TOUCH_CDC_MODE_RAW), b"\x00")
        self.assertEqual(
            decode_touch_cdc_mode(b"\x01"), TOUCH_CDC_MODE_MAI2TOUCH
        )
        with self.assertRaisesRegex(ValueError, "RAW or Mai2Touch"):
            encode_touch_cdc_mode(2)
        with self.assertRaisesRegex(DeviceConfigError, "invalid Touch CDC mode"):
            decode_touch_cdc_mode(b"\x02")

    def test_runtime_status_codec_decodes_two_independent_psocs(self) -> None:
        payload = bytes(
            (
                1, 6, 0x02, 2,
                0x08, 0x02, 0x1B, 0, 5, 0,
                0x09, 0x15, 0x0D, 2, 0x34, 0x12,
            )
        )

        status = decode_touch_runtime_status(payload)

        self.assertEqual(
            status,
            TouchRuntimeStatus(
                state=6,
                flags=0x02,
                devices=(
                    PsocRuntimeStatus(0x08, 0x02, 0x1B, 0, 5),
                    PsocRuntimeStatus(0x09, 0x15, 0x0D, 2, 0x1234),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceConfigError, "length mismatch"):
            decode_touch_runtime_status(payload[:-1])
        with self.assertRaisesRegex(DeviceConfigError, "unsupported.*version"):
            decode_touch_runtime_status(bytes((2,)) + payload[1:])
        with self.assertRaisesRegex(DeviceConfigError, "device count"):
            decode_touch_runtime_status(payload[:3] + bytes((1,)) + payload[4:])

    def test_batch_allows_shared_region_and_round_trips_channel_records(self) -> None:
        shared = TouchMapEntry("C2")
        encoded = encode_touch_batch({33: shared, 0: shared})

        self.assertEqual(encoded[0], 0)
        self.assertEqual(encoded[3], 33)
        self.assertEqual(decode_touch_batch(encoded), {0: shared, 33: shared})

    def test_invalid_batch_records_are_rejected(self) -> None:
        entry = encode_touch_entry(TouchMapEntry("A1"))
        with self.assertRaisesRegex(DeviceConfigError, "one or more"):
            decode_touch_batch(b"")
        with self.assertRaisesRegex(DeviceConfigError, "invalid.*34"):
            decode_touch_batch(bytes((34,)) + entry)
        with self.assertRaisesRegex(DeviceConfigError, "duplicate"):
            decode_touch_batch(bytes((1,)) + entry + bytes((1,)) + entry)
        with self.assertRaisesRegex(ValueError, "between 0 and 33"):
            encode_touch_batch({34: TouchMapEntry("A1")})

    def test_configuration_models_validate_firmware_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "34 entries"):
            TouchConfig(entries=(TouchMapEntry("A1"),))
        with self.assertRaisesRegex(ValueError, "RAW or Mai2Touch"):
            sample_touch_config(cdc_mode=2)
        with self.assertRaisesRegex(ValueError, "unknown touch zone"):
            TouchMapEntry("none")
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
                    bytes((1, 2, 3, 34, 2, 3, 2)),
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
        touch = sample_touch_config(cdc_mode=TOUCH_CDC_MODE_RAW)
        self.client.responses.extend(
            (
                response(
                    TOUCH_MODULE,
                    READ_COMMAND,
                    TOUCH_MAPPING_PARAM,
                    encode_touch_mapping(touch),
                ),
                response(
                    TOUCH_MODULE,
                    READ_COMMAND,
                    TOUCH_CDC_MODE_PARAM,
                    bytes((TOUCH_CDC_MODE_RAW,)),
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

    def test_read_touch_runtime_status_uses_dedicated_parameter(self) -> None:
        assert self.client is not None
        payload = bytes(
            (
                1, 6, 0, 2,
                0x08, 0x02, 0x0B, 0, 1, 0,
                0x09, 0xFF, 0, 3, 0xFF, 0xFF,
            )
        )
        self.assertEqual(len(payload), TOUCH_STATUS_PAYLOAD_LENGTH)
        self.client.responses.append(
            response(
                TOUCH_MODULE,
                READ_COMMAND,
                TOUCH_PSOC_STATUS_PARAM,
                payload,
            )
        )

        status = self.controller.read_touch_runtime_status()

        self.assertEqual(status.state, 6)
        self.assertEqual(status.devices[0].address, 0x08)
        self.assertEqual(status.devices[1].status_age_ms, 0xFFFF)
        self.assertEqual(
            self.client.requests[-1],
            (TOUCH_MODULE, READ_COMMAND, TOUCH_PSOC_STATUS_PARAM, b""),
        )

    def test_apply_touch_uses_batch_and_cdc_mode_and_empty_change_is_noop(
        self,
    ) -> None:
        assert self.client is not None
        self.controller.apply_touch({})
        self.assertEqual(self.client.requests, [])

        entry = TouchMapEntry("E8")
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

        self.client.responses.append(
            response(TOUCH_MODULE, WRITE_COMMAND, TOUCH_CDC_MODE_PARAM)
        )
        self.controller.apply_touch({}, cdc_mode=TOUCH_CDC_MODE_RAW)
        self.assertEqual(
            self.client.requests[-1],
            (
                TOUCH_MODULE,
                WRITE_COMMAND,
                TOUCH_CDC_MODE_PARAM,
                bytes((TOUCH_CDC_MODE_RAW,)),
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

    def test_restore_defaults_uses_firmware_command_then_reads_back(self) -> None:
        assert self.client is not None
        touch = sample_touch_config()
        self.client.responses.extend(
            (
                response(TOUCH_MODULE, LOAD_DEFAULT_COMMAND, 0),
                response(
                    TOUCH_MODULE,
                    READ_COMMAND,
                    TOUCH_MAPPING_PARAM,
                    encode_touch_mapping(touch),
                ),
                response(
                    TOUCH_MODULE,
                    READ_COMMAND,
                    TOUCH_CDC_MODE_PARAM,
                    bytes((TOUCH_CDC_MODE_MAI2TOUCH,)),
                ),
                response(LED_MODULE, LOAD_DEFAULT_COMMAND, 0),
                response(LED_MODULE, READ_COMMAND, LED_PER_BIT_PARAM, b"\x02"),
                response(LED_MODULE, READ_COMMAND, LED_RAINBOW_PARAM, b"\x01"),
                response(KEYBOARD_MODULE, LOAD_DEFAULT_COMMAND, 0),
                response(
                    KEYBOARD_MODULE,
                    READ_COMMAND,
                    KEYBOARD_EK_PARAM,
                    bytes((0x20, 0x55, 0x25, 0x26)),
                ),
                response(
                    KEYBOARD_MODULE,
                    READ_COMMAND,
                    KEYBOARD_LAYOUT_PARAM,
                    bytes((LAYOUT_1P,)),
                ),
            )
        )

        self.assertEqual(self.controller.restore_touch_defaults(), touch)
        self.assertEqual(
            self.controller.restore_led_defaults(),
            LedConfig(led_per_bit=2, rainbow_enabled=True),
        )
        self.assertEqual(
            self.controller.restore_keyboard_defaults(),
            KeyboardConfig(LAYOUT_1P, (0x20, 0x55, 0x25, 0x26)),
        )
        self.assertEqual(
            [request for request in self.client.requests if request[1] == LOAD_DEFAULT_COMMAND],
            [
                (TOUCH_MODULE, LOAD_DEFAULT_COMMAND, 0, b""),
                (LED_MODULE, LOAD_DEFAULT_COMMAND, 0, b""),
                (KEYBOARD_MODULE, LOAD_DEFAULT_COMMAND, 0, b""),
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
