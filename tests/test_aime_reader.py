from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import Any

from tenodx_config.aime_protocol import (
    CMD_CARD_DETECT,
    CMD_GET_FW_VERSION,
    CMD_GET_HW_VERSION,
    CMD_READ_BLOCK2,
    CMD_START_POLLING,
    CMD_STOP_POLLING,
    ESCAPE,
    SYNC,
    escape_frame_bytes,
)
from tenodx_config.aime_reader import (
    BLOCK2_REQUEST_PAYLOAD,
    AimeReaderController,
    AimeResponseTimeout,
    AimeSerialError,
)


def build_response(
    command: int,
    payload: bytes,
    *,
    sequence: int,
    address: int,
    status: int = 0,
) -> bytes:
    body = (
        bytes(
            (
                6 + len(payload),
                address,
                sequence,
                command,
                status,
                len(payload),
            )
        )
        + payload
    )
    return bytes((SYNC,)) + escape_frame_bytes(body + bytes((sum(body) & 0xFF,)))


def decode_request(frame: bytes) -> tuple[int, int, int, bytes]:
    if not frame or frame[0] != SYNC:
        raise AssertionError("request has no sync byte")
    decoded = bytearray()
    index = 1
    while index < len(frame):
        value = frame[index]
        if value == ESCAPE:
            value = (frame[index + 1] + 1) & 0xFF
            index += 2
        else:
            index += 1
        decoded.append(value)
    body = decoded[:-1]
    if (sum(body) & 0xFF) != decoded[-1]:
        raise AssertionError("request checksum mismatch")
    payload_length = body[4]
    return body[1], body[2], body[3], bytes(body[5 : 5 + payload_length])


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.now += duration


Responder = Callable[[int, bytes], tuple[bytes, int]]


class ScriptedSerial:
    def __init__(
        self,
        responder: Responder,
        events: list[str],
        kwargs: dict[str, Any],
    ) -> None:
        self.responder = responder
        self.events = events
        self.kwargs = kwargs
        self.is_open = True
        self.dtr = False
        self.rts = False
        self.received: list[tuple[int, bytes]] = []
        self._read_buffer = bytearray()
        events.append("open")

    @property
    def in_waiting(self) -> int:
        return len(self._read_buffer)

    def reset_input_buffer(self) -> None:
        self.events.append("reset")
        self._read_buffer.clear()

    def write(self, frame: bytes) -> int:
        address, sequence, command, payload = decode_request(frame)
        self.received.append((command, payload))
        response_payload, status = self.responder(command, payload)
        if status >= 0:
            self._read_buffer.extend(
                build_response(
                    command,
                    response_payload,
                    sequence=sequence,
                    address=address,
                    status=status,
                )
            )
        self.events.append(f"write:{command:02X}")
        return len(frame)

    def flush(self) -> None:
        self.events.append("flush")

    def read(self, size: int) -> bytes:
        size = min(size, 3, len(self._read_buffer))
        data = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return data

    def close(self) -> None:
        self.events.append("close")
        self.is_open = False


class SerialFactory:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.events: list[str] = []
        self.device: ScriptedSerial | None = None

    def __call__(self, **kwargs: Any) -> ScriptedSerial:
        self.device = ScriptedSerial(self.responder, self.events, kwargs)
        return self.device


class AimeReaderControllerTests(unittest.TestCase):
    def make_controller(
        self,
        responder: Responder,
        *,
        baudrate: int = 115200,
    ) -> tuple[AimeReaderController, SerialFactory, FakeTime]:
        factory = SerialFactory(responder)
        fake_time = FakeTime()
        controller = AimeReaderController(
            "COM9",
            baudrate,
            serial_factory=factory,
            sleeper=fake_time.sleep,
            clock=fake_time.clock,
        )
        return controller, factory, fake_time

    def test_probe_poll_detect_read_and_stop_serial_sequence(self) -> None:
        block = bytes.fromhex("00 00 00 00 00 00 12 34 56 78 90 12 34 56 78 90")

        def responder(command: int, _payload: bytes) -> tuple[bytes, int]:
            responses = {
                CMD_GET_FW_VERSION: b"\x94",
                CMD_GET_HW_VERSION: b"837-15396",
                CMD_START_POLLING: b"",
                CMD_STOP_POLLING: b"",
                CMD_CARD_DETECT: bytes.fromhex("01 10 04 01 02 03 04"),
                CMD_READ_BLOCK2: block,
            }
            return responses[command], 0

        controller, factory, fake_time = self.make_controller(responder)
        self.addCleanup(controller.close)
        assert factory.device is not None
        self.assertEqual(factory.device.kwargs["port"], "COM9")
        self.assertEqual(factory.device.kwargs["baudrate"], 115200)
        self.assertEqual(factory.device.kwargs["bytesize"], 8)
        self.assertEqual(factory.device.kwargs["parity"], "N")
        self.assertEqual(factory.device.kwargs["stopbits"], 1)
        self.assertTrue(factory.device.dtr)
        self.assertTrue(factory.device.rts)
        self.assertEqual(fake_time.sleeps, [1.5])

        info = controller.probe()
        self.assertEqual(info.firmware, b"\x94")
        self.assertEqual(info.hardware, b"837-15396")
        controller.start_polling()
        self.assertTrue(controller.detect_card().present)
        read = controller.read_block2()
        self.assertEqual(read.access_code, "12345678901234567890")
        controller.stop_polling()

        self.assertEqual(
            factory.device.received,
            [
                (CMD_GET_FW_VERSION, b""),
                (CMD_GET_HW_VERSION, b""),
                (CMD_START_POLLING, b""),
                (CMD_CARD_DETECT, b""),
                (CMD_READ_BLOCK2, BLOCK2_REQUEST_PAYLOAD),
                (CMD_STOP_POLLING, b""),
            ],
        )
        self.assertEqual(len(BLOCK2_REQUEST_PAYLOAD), 5)
        self.assertEqual(BLOCK2_REQUEST_PAYLOAD[4], 0x02)

    def test_read_card_aggregates_present_and_invalid_bcd(self) -> None:
        invalid_block = bytes(6) + bytes.fromhex("12 34 56 78 90 12 34 56 78 9A")

        def responder(command: int, _payload: bytes) -> tuple[bytes, int]:
            if command == CMD_CARD_DETECT:
                return bytes.fromhex("01 10 04 01 02 03 04"), 0
            if command == CMD_READ_BLOCK2:
                return invalid_block, 0
            raise AssertionError(f"unexpected command {command:02X}")

        controller, factory, _fake_time = self.make_controller(responder)
        self.addCleanup(controller.close)
        result = controller.read_card()
        self.assertTrue(result.present)
        self.assertIsNone(result.access_code)
        self.assertEqual(result.raw_block, invalid_block)
        assert factory.device is not None
        self.assertEqual(
            [command for command, _payload in factory.device.received],
            [CMD_CARD_DETECT, CMD_READ_BLOCK2],
        )

    def test_read_card_absent_does_not_request_block(self) -> None:
        def responder(command: int, _payload: bytes) -> tuple[bytes, int]:
            self.assertEqual(command, CMD_CARD_DETECT)
            return b"\x00", 0

        controller, factory, _fake_time = self.make_controller(
            responder,
            baudrate=38400,
        )
        self.addCleanup(controller.close)
        result = controller.read_card()
        self.assertFalse(result.present)
        self.assertIsNone(result.access_code)
        self.assertEqual(result.raw_block, b"")
        assert factory.device is not None
        self.assertEqual(factory.device.kwargs["baudrate"], 38400)
        self.assertEqual(len(factory.device.received), 1)

    def test_read_block2_rejects_invalid_request_payload(self) -> None:
        controller, factory, _fake_time = self.make_controller(
            lambda _command, _payload: (bytes(16), 0)
        )
        self.addCleanup(controller.close)
        with self.assertRaisesRegex(ValueError, "至少需要 5 字节"):
            controller.read_block2(b"\x00\x00\x02")
        with self.assertRaisesRegex(ValueError, "第 5 字节"):
            controller.read_block2(bytes(5))
        assert factory.device is not None
        self.assertEqual(factory.device.received, [])

    def test_timeout_uses_injected_clock_and_sleeper(self) -> None:
        controller, _factory, fake_time = self.make_controller(
            lambda _command, _payload: (b"", -1)
        )
        self.addCleanup(controller.close)
        with self.assertRaisesRegex(AimeResponseTimeout, "0x30"):
            controller.command(CMD_GET_FW_VERSION, timeout=0.005)
        self.assertGreaterEqual(fake_time.now, 1.505)

    def test_invalid_baudrate_and_open_failure_are_clear(self) -> None:
        with self.assertRaisesRegex(ValueError, "115200/38400"):
            AimeReaderController(
                "COM9",
                9600,
                serial_factory=lambda **_kwargs: None,
            )

        def fail_factory(**_kwargs: Any) -> Any:
            raise OSError("port busy")

        with self.assertRaisesRegex(AimeSerialError, "COM9.*port busy"):
            AimeReaderController(
                "COM9",
                serial_factory=fail_factory,
                sleeper=lambda _duration: None,
            )


if __name__ == "__main__":
    unittest.main()
