from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

from tenodx_config.cli import run_dfu_update
from tenodx_config.dfu_devices import DfuDevice
from tenodx_config.magic import MagicPort


class DfuWorkflowTests(unittest.TestCase):
    def test_application_passes_selected_device_and_firmware_to_component(self) -> None:
        args = argparse.Namespace(
            firmware=None,
            port=None,
            device_id="0483:DF11",
            dfu_timeout=20.0,
            app_timeout=30.0,
        )
        firmware = Path("firmware/maimai_controller_H503_20260802_120000.bin")
        magic = MagicPort(device="COM7", usb_serial="APP-UID")
        dfu = DfuDevice(
            device_id="0483:DF11",
            serial_number="DFU-UID",
            usb_path="2-2",
            devnum=1,
        )
        returned = MagicPort(device="COM7", usb_serial="APP-UID")
        with (
            patch("tenodx_config.cli.resolve_firmware", return_value=firmware),
            patch("tenodx_config.cli.discover_magic_ports", return_value=[magic]),
            patch("tenodx_config.cli.list_dfu_devices", return_value=([], "")),
            patch("tenodx_config.cli.send_enter_dfu") as enter,
            patch("tenodx_config.cli.wait_for_new_dfu_devices", return_value=[dfu]),
            patch("tenodx_config.cli.select_dfu_device", return_value=dfu),
            patch("tenodx_config.cli.flash_firmware") as flash,
            patch("tenodx_config.cli.wait_for_magic_return", return_value=returned),
            patch("builtins.print"),
        ):
            result = run_dfu_update(args)

        self.assertEqual(result, 0)
        enter.assert_called_once_with(magic)
        flash.assert_called_once()
        self.assertEqual(
            flash.call_args.kwargs,
            {
                "device_id": "0483:DF11",
                "serial_number": "DFU-UID",
                "firmware_path": firmware,
                "on_output": flash.call_args.kwargs["on_output"],
            },
        )


if __name__ == "__main__":
    unittest.main()
