from __future__ import annotations

import argparse
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tenodx_config.cli import (
    build_parser,
    get_project_root,
    main,
    run_device_config,
    run_dfu_update,
    run_live_test,
)
from tenodx_config.dfu_devices import DfuDevice
from tenodx_config.magic import MagicPort


class DfuWorkflowTests(unittest.TestCase):
    def test_frozen_project_root_is_executable_directory(self) -> None:
        executable = Path("C:/Release/TenoDXDFU.exe")
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(executable)),
        ):
            self.assertEqual(get_project_root(), executable.resolve().parent)

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
            patch("tenodx_config.cli.ensure_usb_reenumeration_available") as preflight,
            patch("tenodx_config.cli.discover_magic_ports", return_value=[magic]),
            patch("tenodx_config.cli.list_dfu_devices", return_value=([], "")),
            patch("tenodx_config.cli.send_enter_dfu") as enter,
            patch("tenodx_config.cli.wait_for_new_dfu_devices", return_value=[dfu]),
            patch("tenodx_config.cli.select_dfu_device", return_value=dfu),
            patch("tenodx_config.cli.flash_firmware") as flash,
            patch("tenodx_config.cli.remove_dfu_device_and_rescan") as remove,
            patch("tenodx_config.cli.wait_for_magic_return", return_value=returned),
            patch("builtins.print"),
        ):
            result = run_dfu_update(args)

        self.assertEqual(result, 0)
        preflight.assert_called_once_with()
        enter.assert_called_once_with(magic)
        flash.assert_called_once()
        remove.assert_called_once()
        self.assertEqual(
            flash.call_args.kwargs,
            {
                "device_id": "0483:DF11",
                "serial_number": "DFU-UID",
                "firmware_path": firmware,
                "on_output": flash.call_args.kwargs["on_output"],
            },
        )
        self.assertEqual(
            remove.call_args.kwargs,
            {
                "device_id": "0483:DF11",
                "serial_number": "DFU-UID",
                "usb_path": "2-2",
                "on_output": remove.call_args.kwargs["on_output"],
            },
        )


class LiveTestCommandTests(unittest.TestCase):
    @staticmethod
    def fake_ui_module(
        launcher: Mock,
    ) -> tuple[types.ModuleType, type[RuntimeError]]:
        class FakeControllerTestError(RuntimeError):
            pass

        module = types.ModuleType("tenodx_config.controller_test_ui")
        module.ControllerTestError = FakeControllerTestError
        module.launch_controller_test = launcher
        return module, FakeControllerTestError

    def test_test_command_launches_ui_without_hardware(self) -> None:
        launcher = Mock(return_value=0)
        module, _ = self.fake_ui_module(launcher)

        with patch.dict(sys.modules, {module.__name__: module}):
            result = main(["test"])

        self.assertEqual(result, 0)
        launcher.assert_called_once_with()

    def test_ui_error_is_reported_as_cli_error(self) -> None:
        launcher = Mock()
        module, ui_error = self.fake_ui_module(launcher)
        launcher.side_effect = ui_error("测试界面初始化失败")

        with (
            patch.dict(sys.modules, {module.__name__: module}),
            patch("builtins.print") as print_mock,
        ):
            result = main(["test"])

        self.assertEqual(result, 1)
        print_mock.assert_called_once_with("错误: 测试界面初始化失败")

    def test_no_command_still_prints_help_and_succeeds(self) -> None:
        with patch("argparse.ArgumentParser.print_help") as print_help:
            result = main([])

        self.assertEqual(result, 0)
        print_help.assert_called_once_with()

    def test_dfu_handler_binding_is_unchanged(self) -> None:
        args = build_parser().parse_args(["dfu"])

        self.assertIs(args.handler, run_dfu_update)
        self.assertIs(build_parser().parse_args(["test"]).handler, run_live_test)


class DeviceConfigCommandTests(unittest.TestCase):
    @staticmethod
    def fake_ui_module(
        launcher: Mock,
    ) -> tuple[types.ModuleType, type[RuntimeError]]:
        class FakeDeviceConfigUiError(RuntimeError):
            pass

        module = types.ModuleType("tenodx_config.device_config_ui")
        module.DeviceConfigUiError = FakeDeviceConfigUiError
        module.launch_device_config = launcher
        return module, FakeDeviceConfigUiError

    def test_config_command_launches_ui_without_hardware(self) -> None:
        launcher = Mock(return_value=0)
        module, _ = self.fake_ui_module(launcher)

        with patch.dict(sys.modules, {module.__name__: module}):
            result = main(["config"])

        self.assertEqual(result, 0)
        launcher.assert_called_once_with()

    def test_config_ui_error_is_reported_as_cli_error(self) -> None:
        launcher = Mock()
        module, ui_error = self.fake_ui_module(launcher)
        launcher.side_effect = ui_error("配置界面初始化失败")

        with (
            patch.dict(sys.modules, {module.__name__: module}),
            patch("builtins.print") as print_mock,
        ):
            result = main(["config"])

        self.assertEqual(result, 1)
        print_mock.assert_called_once_with("错误: 配置界面初始化失败")

    def test_config_handler_is_bound(self) -> None:
        args = build_parser().parse_args(["config"])

        self.assertIs(args.handler, run_device_config)


if __name__ == "__main__":
    unittest.main()
