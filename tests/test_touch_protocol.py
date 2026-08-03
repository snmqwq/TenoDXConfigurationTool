from __future__ import annotations

import unittest

from tenodx_config.touch_protocol import (
    VALID_TOUCH_MASK,
    ZONE_NAMES,
    TouchFrameParser,
    encode_touch_frame,
)


class TouchFrameParserTests(unittest.TestCase):
    def test_zone_order_matches_mai2touch(self) -> None:
        self.assertEqual(len(ZONE_NAMES), 34)
        self.assertEqual(ZONE_NAMES[:8], tuple(f"A{i}" for i in range(1, 9)))
        self.assertEqual(ZONE_NAMES[8:16], tuple(f"B{i}" for i in range(1, 9)))
        self.assertEqual(ZONE_NAMES[16:18], ("C1", "C2"))
        self.assertEqual(ZONE_NAMES[18:26], tuple(f"D{i}" for i in range(1, 9)))
        self.assertEqual(ZONE_NAMES[26:], tuple(f"E{i}" for i in range(1, 9)))

    def test_low_five_bit_chunks_are_little_endian(self) -> None:
        parser = TouchFrameParser()
        expected = (1 << 0) | (1 << 16) | (1 << 33)
        self.assertEqual(parser.feed(encode_touch_frame(expected)), [expected])
        self.assertEqual(encode_touch_frame(1 << 0), b"(\x01\0\0\0\0\0\0)")
        self.assertEqual(encode_touch_frame(1 << 16), b"(\0\0\0\x02\0\0\0)")
        self.assertEqual(encode_touch_frame(1 << 33), b"(\0\0\0\0\0\0\x08)")

    def test_parser_handles_fragmentation_noise_and_multiple_frames(self) -> None:
        parser = TouchFrameParser()
        first = encode_touch_frame(1)
        second = encode_touch_frame(1 << 33)
        self.assertEqual(parser.feed(b"noise" + first[:4]), [])
        self.assertEqual(parser.feed(first[4:] + second), [1, 1 << 33])

    def test_parser_rejects_bad_data_and_resynchronizes(self) -> None:
        parser = TouchFrameParser()
        malformed = b"(\0\0\x20\0\0\0\0)"
        self.assertEqual(
            parser.feed(malformed + encode_touch_frame(1 << 17)),
            [1 << 17],
        )

    def test_reserved_bit_is_masked(self) -> None:
        parser = TouchFrameParser()
        reserved_frame = bytearray(encode_touch_frame(0))
        reserved_frame[7] = 0x10
        self.assertEqual(parser.feed(bytes(reserved_frame)), [0])
        self.assertEqual(VALID_TOUCH_MASK.bit_length(), 34)

    def test_encoder_rejects_out_of_range_states(self) -> None:
        with self.assertRaises(ValueError):
            encode_touch_frame(-1)
        with self.assertRaises(ValueError):
            encode_touch_frame(1 << 34)


if __name__ == "__main__":
    unittest.main()
