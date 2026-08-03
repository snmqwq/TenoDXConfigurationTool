from __future__ import annotations

import unittest

from tenodx_config.aime_protocol import (
    CMD_CARD_DETECT,
    CMD_GET_FW_VERSION,
    CMD_READ_BLOCK2,
    ESCAPE,
    STATUS_OK,
    SYNC,
    AimeProtocolError,
    AimeResponse,
    AimeResponseParser,
    build_request,
    escape_frame_bytes,
    parse_block2,
    parse_card_presence,
)


def build_response(
    command: int,
    payload: bytes = b"",
    *,
    sequence: int = 0,
    status: int = STATUS_OK,
) -> bytes:
    frame_length = 6 + len(payload)
    body = (
        bytes(
            (
                frame_length,
                0,
                sequence,
                command,
                status,
                len(payload),
            )
        )
        + payload
    )
    checksum = sum(body) & 0xFF
    return bytes((SYNC,)) + escape_frame_bytes(body + bytes((checksum,)))


def response_for(
    command: int,
    payload: bytes,
    *,
    sequence: int = 0,
    status: int = STATUS_OK,
) -> AimeResponse:
    return AimeResponse(
        frame_length=6 + len(payload),
        address=0,
        sequence=sequence,
        command=command,
        status=status,
        payload=payload,
    )


class AimeFrameTests(unittest.TestCase):
    def test_legacy_request_vectors(self) -> None:
        self.assertEqual(
            build_request(CMD_GET_FW_VERSION, sequence=9),
            bytes.fromhex("E0 05 00 09 30 00 3E"),
        )
        self.assertEqual(
            build_request(
                0x50,
                bytes((ESCAPE, SYNC)),
                sequence=1,
            ),
            bytes.fromhex("E0 07 00 01 50 02 D0 CF D0 DF 0A"),
        )

    def test_stream_parser_handles_fragmentation_noise_and_escaping(self) -> None:
        parser = AimeResponseParser()
        firmware = build_response(
            CMD_GET_FW_VERSION,
            b"TN32MSEC003S F/W Ver1.2",
            sequence=9,
        )
        escaped = build_response(
            CMD_GET_FW_VERSION,
            bytes((0x94, SYNC, ESCAPE)),
            sequence=10,
        )

        self.assertEqual(parser.feed(b"noise" + firmware[:5]), [])
        parsed = parser.feed(firmware[5:] + escaped)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].sequence, 9)
        self.assertEqual(parsed[0].payload, b"TN32MSEC003S F/W Ver1.2")
        self.assertEqual(parsed[1].sequence, 10)
        self.assertEqual(parsed[1].payload, bytes((0x94, SYNC, ESCAPE)))

    def test_stream_parser_rejects_bad_checksum(self) -> None:
        parser = AimeResponseParser()
        frame = bytearray(build_response(CMD_GET_FW_VERSION, b"version", sequence=1))
        frame[-1] ^= 0x01
        with self.assertRaisesRegex(AimeProtocolError, "校验和"):
            parser.feed(bytes(frame))


class AimeCardTests(unittest.TestCase):
    def test_card_presence_absent_and_mifare(self) -> None:
        absent = parse_card_presence(response_for(CMD_CARD_DETECT, b"\x00"))
        self.assertFalse(absent.present)

        present = parse_card_presence(
            response_for(
                CMD_CARD_DETECT,
                bytes.fromhex("01 10 04 04 A1 B2 C3"),
            )
        )
        self.assertTrue(present.present)
        self.assertEqual(present.card_type, "MIFARE")
        self.assertEqual(present.identifier, bytes.fromhex("04 A1 B2 C3"))

    def test_card_presence_felica(self) -> None:
        payload = bytes.fromhex(
            "01 20 10 01 02 03 04 05 06 07 08 11 12 13 14 15 16 17 18"
        )
        card = parse_card_presence(response_for(CMD_CARD_DETECT, payload))
        self.assertEqual(card.card_type, "FeliCa")
        self.assertEqual(card.identifier, bytes.fromhex("01 02 03 04 05 06 07 08"))
        self.assertEqual(card.pmm, bytes.fromhex("11 12 13 14 15 16 17 18"))

    def test_block2_decodes_strict_bcd_and_preserves_raw(self) -> None:
        raw = bytes.fromhex("DE AD BE EF 00 01 12 34 56 78 90 12 34 56 78 90")
        block = parse_block2(response_for(CMD_READ_BLOCK2, raw))
        self.assertEqual(block.raw_block, raw)
        self.assertEqual(block.access_code, "12345678901234567890")
        self.assertTrue(block.parseable)

        invalid = raw[:-1] + b"\x9a"
        invalid_block = parse_block2(response_for(CMD_READ_BLOCK2, invalid))
        self.assertEqual(invalid_block.raw_block, invalid)
        self.assertIsNone(invalid_block.access_code)
        self.assertFalse(invalid_block.parseable)

    def test_felica_derived_block_decodes_like_firmware(self) -> None:
        idm = bytes.fromhex("01 23 45 67 89 AB CD EF")
        access_code = f"{int.from_bytes(idm, 'big'):020d}"
        packed_bcd = bytes(
            int(access_code[index : index + 2], 16) for index in range(0, 20, 2)
        )
        raw = bytes(6) + packed_bcd

        block = parse_block2(response_for(CMD_READ_BLOCK2, raw))
        self.assertEqual(block.access_code, access_code)
        self.assertEqual(block.raw_block, raw)

    def test_block2_requires_exactly_sixteen_bytes(self) -> None:
        with self.assertRaisesRegex(AimeProtocolError, "Block 2 长度"):
            parse_block2(response_for(CMD_READ_BLOCK2, bytes(15)))

    def test_card_and_block_status_errors_are_reported(self) -> None:
        with self.assertRaisesRegex(AimeProtocolError, "0x42"):
            parse_card_presence(response_for(CMD_CARD_DETECT, b"", status=0x04))
        with self.assertRaisesRegex(AimeProtocolError, "0x52"):
            parse_block2(response_for(CMD_READ_BLOCK2, bytes(16), status=0x01))


if __name__ == "__main__":
    unittest.main()
