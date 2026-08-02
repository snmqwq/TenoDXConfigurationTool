from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from DFU.flasher import DfuError, build_flash_command, flash_firmware


class FlasherTests(unittest.TestCase):
    def test_builds_serial_scoped_leave_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            firmware = Path(temporary) / "maimai_controller_H503_20260802_120000.bin"
            firmware.write_bytes(b"firmware")
            fake_executable = Path(temporary) / "dfu-util.exe"
            with patch("DFU.flasher.get_dfu_util_path", return_value=fake_executable):
                command = build_flash_command("0483:df11", "ABC123", firmware)
            self.assertEqual(
                command,
                [
                    str(fake_executable),
                    "-d",
                    "0483:DF11",
                    "-S",
                    "ABC123",
                    "-a",
                    "0",
                    "-s",
                    "0x08000000:leave",
                    "-D",
                    str(firmware.resolve()),
                ],
            )

    def test_rejects_untimestamped_firmware(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            firmware = Path(temporary) / "maimai_controller_H503.bin"
            firmware.write_bytes(b"firmware")
            with (
                patch(
                    "DFU.flasher.get_dfu_util_path", return_value=Path("dfu-util.exe")
                ),
                self.assertRaises(DfuError),
            ):
                build_flash_command("0483:DF11", "ABC123", firmware)

    def test_rejects_missing_serial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            firmware = Path(temporary) / "maimai_controller_H503_20260802_120000.bin"
            firmware.write_bytes(b"firmware")
            with (
                patch(
                    "DFU.flasher.get_dfu_util_path", return_value=Path("dfu-util.exe")
                ),
                self.assertRaises(DfuError),
            ):
                build_flash_command("0483:DF11", "", firmware)

    def test_streams_output_and_returns_combined_log(self) -> None:
        process = MagicMock()
        process.stdout = iter(("line one\n", "line two\r\n"))
        process.wait.return_value = 0
        received: list[str] = []
        with (
            patch(
                "DFU.flasher.build_flash_command",
                return_value=[str(Path("C:/DFU/dfu-util.exe"))],
            ),
            patch("DFU.flasher.subprocess.Popen", return_value=process),
        ):
            output = flash_firmware(
                "0483:DF11",
                "ABC123",
                Path("maimai_controller_H503_20260802_120000.bin"),
                on_output=received.append,
            )
        self.assertEqual(received, ["line one", "line two"])
        self.assertEqual(output, "line one\nline two")

    def test_allows_get_status_error_after_successful_download_and_leave(self) -> None:
        process = MagicMock()
        process.stdout = iter(
            (
                "File downloaded successfully\n",
                "Warning: Invalid DFU suffix signature\n",
                "Submitting leave request...\n",
                "Error during download get_status\n",
            )
        )
        process.wait.return_value = 74
        received: list[str] = []
        with (
            patch(
                "DFU.flasher.build_flash_command",
                return_value=[str(Path("C:/DFU/dfu-util.exe"))],
            ),
            patch("DFU.flasher.subprocess.Popen", return_value=process),
        ):
            output = flash_firmware(
                "0483:DF11",
                "ABC123",
                Path("maimai_controller_H503_20260802_120000.bin"),
                on_output=received.append,
            )
        self.assertIn("Invalid DFU suffix signature", output)
        self.assertIn("固件数据已写入完成", received[-1])

    def test_rejects_get_status_error_without_completed_download(self) -> None:
        process = MagicMock()
        process.stdout = iter(
            (
                "Submitting leave request...\n",
                "Error during download get_status\n",
            )
        )
        process.wait.return_value = 74
        with (
            patch(
                "DFU.flasher.build_flash_command",
                return_value=[str(Path("C:/DFU/dfu-util.exe"))],
            ),
            patch("DFU.flasher.subprocess.Popen", return_value=process),
            self.assertRaises(DfuError),
        ):
            flash_firmware(
                "0483:DF11",
                "ABC123",
                Path("maimai_controller_H503_20260802_120000.bin"),
            )


if __name__ == "__main__":
    unittest.main()
