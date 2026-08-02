from __future__ import annotations

import unittest

from tenodx_config.magic import build_magic_request, parse_magic_response


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


if __name__ == "__main__":
    unittest.main()
