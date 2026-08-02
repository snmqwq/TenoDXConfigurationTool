from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from DFU.flasher import (
    DfuError,
    build_flash_command,
    build_leave_command,
    flash_firmware,
)


class FlasherTests(unittest.TestCase):
    def test_builds_serial_scoped_flash_command_without_leave(self) -> None:
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
                    "0x08000000",
                    "-D",
                    str(firmware.resolve()),
                ],
            )

    def test_builds_serial_scoped_leave_command(self) -> None:
        fake_executable = Path("C:/DFU/dfu-util.exe")
        with patch("DFU.flasher.get_dfu_util_path", return_value=fake_executable):
            command = build_leave_command("0483:df11", "ABC123")
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
                ":leave",
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
        flash_process = MagicMock()
        flash_process.stdout = iter(("line one\n", "line two\r\n"))
        flash_process.wait.return_value = 0
        leave_process = MagicMock()
        leave_process.stdout = iter(("Submitting leave request...\n",))
        leave_process.wait.return_value = 0
        received: list[str] = []
        with (
            patch(
                "DFU.flasher.build_flash_command",
                return_value=[str(Path("C:/DFU/dfu-util.exe")), "flash"],
            ),
            patch(
                "DFU.flasher.build_leave_command",
                return_value=[str(Path("C:/DFU/dfu-util.exe")), "leave"],
            ),
            patch(
                "DFU.flasher.subprocess.Popen",
                side_effect=(flash_process, leave_process),
            ),
            patch("DFU.flasher.time.sleep") as sleep,
        ):
            output = flash_firmware(
                "0483:DF11",
                "ABC123",
                Path("maimai_controller_H503_20260802_120000.bin"),
                on_output=received.append,
            )
        self.assertEqual(received, ["line one", "line two", "Submitting leave request..."])
        self.assertEqual(output, "line one\nline two\nSubmitting leave request...")
        sleep.assert_called_once_with(0.5)

    def test_allows_known_disconnect_during_leave_status(self) -> None:
        outputs = [
            (0, "File downloaded successfully"),
            (74, "Submitting leave request...\nError during download get_status"),
        ]
        received: list[str] = []
        with (
            patch("DFU.flasher.build_flash_command", return_value=["dfu-util", "flash"]),
            patch("DFU.flasher.build_leave_command", return_value=["dfu-util", "leave"]),
            patch("DFU.flasher._run_dfu_util", side_effect=outputs),
            patch("DFU.flasher.time.sleep"),
        ):
            output = flash_firmware(
                "0483:DF11",
                "ABC123",
                Path("maimai_controller_H503_20260802_120000.bin"),
                on_output=received.append,
            )
        self.assertIn("File downloaded successfully", output)
        self.assertIn("继续等待应用设备重新枚举", received[-1])

    def test_does_not_leave_after_failed_flash(self) -> None:
        with (
            patch("DFU.flasher.build_flash_command", return_value=["dfu-util", "flash"]),
            patch("DFU.flasher.build_leave_command", return_value=["dfu-util", "leave"]),
            patch(
                "DFU.flasher._run_dfu_util",
                return_value=(1, "download failed"),
            ) as run,
            patch("DFU.flasher.time.sleep") as sleep,
            self.assertRaises(DfuError),
        ):
            flash_firmware(
                "0483:DF11",
                "ABC123",
                Path("maimai_controller_H503_20260802_120000.bin"),
            )
        run.assert_called_once()
        sleep.assert_not_called()

    def test_rejects_unexpected_leave_failure(self) -> None:
        outputs = [
            (0, "File downloaded successfully"),
            (1, "No DFU capable USB device available"),
        ]
        with (
            patch(
                "DFU.flasher.build_flash_command",
                return_value=["dfu-util", "flash"],
            ),
            patch(
                "DFU.flasher.build_leave_command",
                return_value=["dfu-util", "leave"],
            ),
            patch("DFU.flasher._run_dfu_util", side_effect=outputs),
            patch("DFU.flasher.time.sleep"),
            self.assertRaisesRegex(DfuError, "退出 DFU 失败"),
        ):
            flash_firmware(
                "0483:DF11",
                "ABC123",
                Path("maimai_controller_H503_20260802_120000.bin"),
            )


if __name__ == "__main__":
    unittest.main()
