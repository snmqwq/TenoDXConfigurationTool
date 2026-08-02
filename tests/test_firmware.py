from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tenodx_config.firmware import discover_firmware, select_firmware


class FirmwareTests(unittest.TestCase):
    def test_discovery_is_strict_and_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "maimai_controller_H503.bin").write_bytes(b"ignored")
            (root / "maimai_controller_H503_20260230_120000.bin").write_bytes(
                b"bad date"
            )
            older = root / "maimai_controller_H503_20260801_120000.bin"
            newer = root / "maimai_controller_H503_20260802_120000.bin"
            older.write_bytes(b"older")
            newer.write_bytes(b"newer")

            candidates = discover_firmware(root)

            self.assertEqual(
                [item.path.name for item in candidates], [newer.name, older.name]
            )

    def test_single_firmware_is_selected_without_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            firmware = root / "maimai_controller_H503_20260802_120000.bin"
            firmware.write_bytes(b"firmware")
            selected = select_firmware(
                discover_firmware(root),
                input_fn=lambda _prompt: self.fail("input should not be called"),
                output_fn=lambda _line: None,
            )
            self.assertEqual(selected, firmware.resolve())

    def test_multiple_firmware_requires_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            older = root / "maimai_controller_H503_20260801_120000.bin"
            newer = root / "maimai_controller_H503_20260802_120000.bin"
            older.write_bytes(b"older")
            newer.write_bytes(b"newer")
            answers = iter(("bad", "2"))
            selected = select_firmware(
                discover_firmware(root),
                input_fn=lambda _prompt: next(answers),
                output_fn=lambda _line: None,
            )
            self.assertEqual(selected, older.resolve())


if __name__ == "__main__":
    unittest.main()
