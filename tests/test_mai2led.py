from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import Any

from tenodx_config.mai2led import (
    ACK_REPORT_OK,
    ACK_STATUS_OK,
    BAUDRATE,
    BLACK,
    GET_BOARD_INFO,
    LOGICAL_LIGHT_COUNT,
    MARKER,
    SET_LED_GS_8BIT_MULTI,
    SET_LED_GS_8BIT_MULTI_FADE,
    SET_LED_GS_UPDATE,
    SUPPORTED_BOARD_NUMBER,
    SYNC,
    Mai2LedController,
    Mai2LedProtocolError,
    Mai2LedTimeoutError,
    build_request,
    fade_speed_for_duration,
    parse_ack,
    validate_rgb,
)


def escape_body(body: bytes) -> bytes:
    encoded = bytearray()
    for value in body:
        if value in (SYNC, MARKER):
            encoded.extend((MARKER, value - 1))
        else:
            encoded.append(value)
    return bytes(encoded)


def build_ack(
    command: int,
    payload: bytes = b"",
    *,
    status: int = ACK_STATUS_OK,
    report: int = ACK_REPORT_OK,
) -> bytes:
    body = bytes((0x01, 0x11, len(payload) + 3, status, command, report)) + payload
    return bytes((SYNC,)) + escape_body(body) + bytes((sum(body) & 0xFF,))


def decode_request(frame: bytes) -> tuple[int, int, bytes]:
    if not frame or frame[0] != SYNC:
        raise AssertionError("request has no sync byte")
    decoded = bytearray()
    index = 1
    expected_length: int | None = None
    while index < len(frame):
        if expected_length is not None and len(decoded) == expected_length:
            checksum = frame[index]
            if checksum != (sum(decoded) & 0xFF):
                raise AssertionError("bad request checksum")
            return decoded[1], decoded[3], bytes(decoded[4:])
        value = frame[index]
        if value == MARKER:
            index += 1
            if index >= len(frame):
                raise AssertionError("incomplete request escape")
            value = (frame[index] + 1) & 0xFF
        decoded.append(value)
        index += 1
        if len(decoded) == 3:
            expected_length = decoded[2] + 3
    raise AssertionError("incomplete request")


class FakeSerial:
    def __init__(
        self,
        responder: Callable[[int, bytes], bytes | None] | None = None,
    ) -> None:
        self.responder = responder or (lambda command, _payload: build_ack(command))
        self.is_open = True
        self.writes: list[bytes] = []
        self.incoming = bytearray()
        self.close_count = 0

    @property
    def in_waiting(self) -> int:
        return len(self.incoming)

    def reset_input_buffer(self) -> None:
        self.incoming.clear()

    def write(self, frame: bytes) -> int:
        self.writes.append(frame)
        _src_node, command, payload = decode_request(frame)
        response = self.responder(command, payload)
        if response:
            self.incoming.extend(response)
        return len(frame)

    def flush(self) -> None:
        pass

    def read(self, size: int) -> bytes:
        chunk = bytes(self.incoming[:size])
        del self.incoming[:size]
        return chunk

    def close(self) -> None:
        self.close_count += 1
        self.is_open = False


class Mai2LedProtocolTests(unittest.TestCase):
    def test_request_escapes_e0_and_d0_body_bytes(self) -> None:
        frame = build_request(0x39, bytes((MARKER, SYNC, 0xFF)))
        self.assertIn(bytes((MARKER, MARKER - 1)), frame)
        self.assertIn(bytes((MARKER, SYNC - 1)), frame)
        _src_node, command, payload = decode_request(frame)
        self.assertEqual(command, 0x39)
        self.assertEqual(payload, bytes((MARKER, SYNC, 0xFF)))

    def test_request_avoids_unescaped_framing_checksum(self) -> None:
        # Source node 01 would make this request's checksum E0.
        frame = build_request(0x31, bytes((0, 0x98, 0, 0)))
        source_node, command, payload = decode_request(frame)
        self.assertNotEqual(source_node, 0x01)
        self.assertEqual(command, 0x31)
        self.assertEqual(payload, bytes((0, 0x98, 0, 0)))
        self.assertNotIn(frame[-1], (SYNC, MARKER))

    def test_ack_parser_unescapes_body_and_accepts_raw_e0_checksum(self) -> None:
        escaped_ack = parse_ack(build_ack(0x31, bytes((MARKER, SYNC))))
        self.assertEqual(escaped_ack.payload, bytes((MARKER, SYNC)))

        # This body's checksum is exactly E0; ACK checksums are not escaped.
        raw_checksum_ack = build_ack(0x31, bytes((0x97,)))
        self.assertEqual(raw_checksum_ack[-1], SYNC)
        self.assertEqual(parse_ack(raw_checksum_ack).payload, bytes((0x97,)))

    def test_ack_parser_rejects_bad_checksum(self) -> None:
        frame = bytearray(build_ack(0x31))
        frame[-1] ^= 0x01
        with self.assertRaisesRegex(Mai2LedProtocolError, "checksum mismatch"):
            parse_ack(frame)


class Mai2LedControllerTests(unittest.TestCase):
    def make_controller(
        self,
        responder: Callable[[int, bytes], bytes | None] | None = None,
    ) -> tuple[Mai2LedController, FakeSerial, dict[str, Any], list[float]]:
        device = FakeSerial(responder)
        serial_options: dict[str, Any] = {}
        sleeps: list[float] = []

        def serial_factory(**options: Any) -> FakeSerial:
            serial_options.update(options)
            return device

        controller = Mai2LedController(
            "COM42",
            serial_factory=serial_factory,
            sleeper=sleeps.append,
        )
        self.addCleanup(controller.close)
        return controller, device, serial_options, sleeps

    def test_probe_verifies_15070_04_and_serial_settings(self) -> None:
        def responder(command: int, _payload: bytes) -> bytes:
            if command == GET_BOARD_INFO:
                return build_ack(command, b"15070-04\xff\x90")
            return build_ack(command)

        controller, _device, options, sleeps = self.make_controller(responder)
        board = controller.probe()
        self.assertEqual(board.board_number, SUPPORTED_BOARD_NUMBER)
        self.assertEqual(board.firmware_revision, 0x90)
        self.assertEqual(options["port"], "COM42")
        self.assertEqual(options["baudrate"], BAUDRATE)
        self.assertEqual(sleeps, [0.05])

    def test_probe_rejects_another_board(self) -> None:
        controller, _device, _options, _sleeps = self.make_controller(
            lambda command, _payload: build_ack(command, b"837-00000\xff\x01")
        )
        with self.assertRaisesRegex(Mai2LedProtocolError, "unsupported"):
            controller.probe()

    def test_rgb_validation_is_strict_and_precedes_writes(self) -> None:
        controller, device, _options, _sleeps = self.make_controller()
        self.assertEqual(validate_rgb([0, 127, 255]), (0, 127, 255))
        invalid_colors: tuple[Any, ...] = (
            (0, 0),
            (0, 0, 0, 0),
            (-1, 0, 0),
            (0, 256, 0),
            (0, 0.5, 0),
            (True, 0, 0),
        )
        for color in invalid_colors:
            with self.subTest(color=color), self.assertRaises(ValueError):
                controller.set_all(color)
        self.assertEqual(device.writes, [])

    def test_set_all_stages_all_eight_lights_then_updates(self) -> None:
        controller, device, _options, _sleeps = self.make_controller()
        controller.set_all((12, 34, 56))
        commands = [decode_request(frame)[1:] for frame in device.writes]
        self.assertEqual(
            commands,
            [
                (
                    SET_LED_GS_8BIT_MULTI,
                    bytes((0, LOGICAL_LIGHT_COUNT, 0, 12, 34, 56, 0)),
                ),
                (SET_LED_GS_UPDATE, b""),
            ],
        )

    def test_chase_frame_has_two_stages_and_exactly_one_update(self) -> None:
        controller, device, _options, _sleeps = self.make_controller()
        controller.set_chase_frame(3, (12, 34, 56))
        commands = [decode_request(frame)[1:] for frame in device.writes]
        self.assertEqual(
            commands,
            [
                (
                    SET_LED_GS_8BIT_MULTI,
                    bytes((0, LOGICAL_LIGHT_COUNT, 0, *BLACK, 0)),
                ),
                (
                    SET_LED_GS_8BIT_MULTI,
                    bytes((3, 4, 0, 12, 34, 56, 0)),
                ),
                (SET_LED_GS_UPDATE, b""),
            ],
        )

    def test_fade_speed_and_command_sequence(self) -> None:
        self.assertEqual(fade_speed_for_duration(600), 55)
        self.assertEqual(fade_speed_for_duration(1), 255)
        self.assertEqual(fade_speed_for_duration(100_000), 1)
        for invalid in (0, -1, True, 1.5):
            with self.subTest(duration=invalid), self.assertRaises(ValueError):
                fade_speed_for_duration(invalid)  # type: ignore[arg-type]

        controller, device, _options, _sleeps = self.make_controller()
        controller.fade_all((1, 2, 3), (4, 5, 6), 600)
        commands = [decode_request(frame)[1:] for frame in device.writes]
        self.assertEqual(
            commands,
            [
                (
                    SET_LED_GS_8BIT_MULTI,
                    bytes((0, LOGICAL_LIGHT_COUNT, 0, 1, 2, 3, 0)),
                ),
                (
                    SET_LED_GS_8BIT_MULTI_FADE,
                    bytes((0, LOGICAL_LIGHT_COUNT, 0, 4, 5, 6, 55)),
                ),
                (SET_LED_GS_UPDATE, b""),
            ],
        )

    def test_injected_clock_and_sleeper_drive_timeout(self) -> None:
        device = FakeSerial(lambda _command, _payload: None)
        now = [0.0]

        def sleeper(duration: float) -> None:
            now[0] += duration

        controller = Mai2LedController(
            "COM42",
            timeout=0.003,
            serial_factory=lambda **_options: device,
            sleeper=sleeper,
            clock=lambda: now[0],
        )
        self.addCleanup(controller.close)
        with self.assertRaises(Mai2LedTimeoutError):
            controller.command(SET_LED_GS_UPDATE)

    def test_close_is_idempotent(self) -> None:
        controller, device, _options, _sleeps = self.make_controller()
        controller.close()
        controller.close()
        self.assertFalse(controller.is_open)
        self.assertEqual(device.close_count, 1)


if __name__ == "__main__":
    unittest.main()
