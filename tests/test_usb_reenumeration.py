from __future__ import annotations

import unittest
from unittest.mock import call, patch

from tenodx_config.usb_reenumeration import (
    PnpDevice,
    UsbReenumerationError,
    _parse_connected_devices,
    _select_target_node,
    ensure_usb_reenumeration_available,
    remove_dfu_device_and_rescan,
)

HARDWARE_ID = r"USB\VID_0483&PID_DF11"
FIRST = PnpDevice(
    instance_id=rf"{HARDWARE_ID}\356234433234",
    location_paths="PCIROOT(0)#USBROOT(0)#USB(2)",
)
SECOND = PnpDevice(
    instance_id=rf"{HARDWARE_ID}\FFFFFFFEFFFF",
    location_paths="PCIROOT(0)#USBROOT(0)#USB(1)",
)


class UsbReenumerationTests(unittest.TestCase):
    def test_preflight_requires_administrator(self) -> None:
        with (
            patch("tenodx_config.usb_reenumeration.get_pnputil_path"),
            patch(
                "tenodx_config.usb_reenumeration.ctypes.windll.shell32.IsUserAnAdmin",
                return_value=0,
            ),
            self.assertRaisesRegex(UsbReenumerationError, "管理员权限"),
        ):
            ensure_usb_reenumeration_available()

    def test_parses_pnputil_csv(self) -> None:
        output = (
            "InstanceId,DeviceDescription,LocationPaths\n"
            f'"{FIRST.instance_id}","STM32 Bootloader",'
            f'"{FIRST.location_paths}"\n'
        )
        self.assertEqual(_parse_connected_devices(output, HARDWARE_ID), [FIRST])

    def test_selects_node_by_usb_serial(self) -> None:
        selected = _select_target_node(
            [FIRST, SECOND],
            serial_number="356234433234",
            usb_path="2-1",
        )
        self.assertEqual(selected, FIRST)

    def test_selects_unknown_serial_by_dfu_port_path(self) -> None:
        selected = _select_target_node(
            [FIRST, SECOND],
            serial_number="UNKNOWN",
            usb_path="2-2",
        )
        self.assertEqual(selected, FIRST)

    def test_refuses_to_remove_an_ambiguous_device(self) -> None:
        with self.assertRaises(UsbReenumerationError):
            _select_target_node(
                [FIRST, SECOND],
                serial_number="UNKNOWN",
                usb_path="",
            )

    def test_removes_selected_node_then_scans_devices(self) -> None:
        received: list[str] = []
        with (
            patch(
                "tenodx_config.usb_reenumeration.list_connected_dfu_nodes",
                return_value=[FIRST],
            ),
            patch(
                "tenodx_config.usb_reenumeration._run_pnputil",
                side_effect=((0, "removed"), (0, "scanned")),
            ) as run,
        ):
            remove_dfu_device_and_rescan(
                device_id="0483:DF11",
                serial_number="UNKNOWN",
                usb_path="2-2",
                on_output=received.append,
            )
        self.assertEqual(
            run.call_args_list,
            [
                call(["/remove-device", FIRST.instance_id]),
                call(["/scan-devices"]),
            ],
        )
        self.assertIn("已卸载", received[0])
        self.assertIn("重新枚举", received[1])

    def test_scans_when_dfu_node_is_already_offline(self) -> None:
        with (
            patch(
                "tenodx_config.usb_reenumeration.list_connected_dfu_nodes",
                return_value=[],
            ),
            patch(
                "tenodx_config.usb_reenumeration._run_pnputil",
                return_value=(0, "scanned"),
            ) as run,
        ):
            remove_dfu_device_and_rescan(
                device_id="0483:DF11",
                serial_number="UNKNOWN",
                usb_path="2-2",
            )
        run.assert_called_once_with(["/scan-devices"])


if __name__ == "__main__":
    unittest.main()
