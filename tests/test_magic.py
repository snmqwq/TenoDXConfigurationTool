from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tenodx_config.magic import (
    MagicError,
    build_magic_request,
    list_serial_ports,
    parse_magic_response,
)


class MagicProtocolTests(unittest.TestCase):
    def test_enter_dfu_request_matches_firmware_protocol(self) -> None:
        frame = build_magic_request(0x00, 0x84, 0x00, bytes((0xA5,)))
        self.assertEqual(
            frame.hex(" ").upper(), "91 3E ED 20 7C 99 58 AC 00 84 00 01 A5 2A"
        )

    def test_parse_response(self) -> None:
        header_and_payload = bytes((0xAC, 0x00, 0x20, 0x05, 0x00, 0x02, 0x01, 0x02))
        frame = header_and_payload + bytes((sum(header_and_payload) & 0xFF,))
        response = parse_magic_response(frame)
        self.assertTrue(response.ok)
        self.assertEqual(response.payload, bytes((0x01, 0x02)))

    def test_request_accepts_the_firmware_payload_limit(self) -> None:
        payload = bytes(range(248))
        frame = build_magic_request(0x10, 0x02, 0x03, payload)

        self.assertEqual(frame[8:12], bytes((0x10, 0x02, 0x03, 248)))
        self.assertEqual(frame[12:-1], payload)
        with self.assertRaisesRegex(MagicError, "248"):
            build_magic_request(0x10, 0x02, 0x03, payload + b"\x00")

    def test_debug_port_is_probed_before_other_serial_functions(self) -> None:
        ports = [
            SimpleNamespace(
                device="COM3",
                description="TenoDX Aime Port",
                serial_number="UID",
                hwid="USB VID:PID=0483:5740",
            ),
            SimpleNamespace(
                device="COM20",
                description="TenoDX Debug Port",
                serial_number="UID",
                hwid="USB VID:PID=0483:5740",
            ),
        ]

        with patch("tenodx_config.magic.list_ports.comports", return_value=ports):
            discovered = list_serial_ports()

        self.assertEqual([port.device for port in discovered], ["COM20", "COM3"])


if __name__ == "__main__":
    unittest.main()
