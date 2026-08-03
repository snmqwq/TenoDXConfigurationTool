from __future__ import annotations

import unittest
from unittest.mock import patch

from tenodx_config.raw_keyboard import (
    GIDC_REMOVAL,
    NO_BUS_DESCRIPTION,
    RawKeyboardMonitor,
    _raw_path_to_instance_id,
    enumerate_raw_keyboards,
    is_hid_keyboard_path,
    keyboard_device_label,
    list_raw_keyboard_devices,
    list_serial_bus_descriptions,
    make_keyboard_device,
)

KEYBOARD_PATH = (
    r"\\?\HID#VID_0483&PID_5740&MI_03#7&2A1B3C4D&0&0000"
    r"#{4D1E55B2-F16F-11CF-88CB-001111000030}"
)


class RawKeyboardIdentityTests(unittest.TestCase):
    def test_parses_full_hid_identity(self) -> None:
        device = make_keyboard_device(KEYBOARD_PATH, "TenoDX Controller")

        self.assertEqual(device.path, KEYBOARD_PATH)
        self.assertEqual(device.bus_description, "TenoDX Controller")
        self.assertEqual(device.vid, 0x0483)
        self.assertEqual(device.pid, 0x5740)
        self.assertEqual(device.interface, "MI_03")
        self.assertEqual(device.instance_tail, "7&2A1B3C4D&0&0000")
        self.assertIn("TenoDX Controller", device.label)
        label = device.display_label(2)
        self.assertIn("键盘 2", label)
        self.assertIn("TenoDX Controller", label)
        self.assertNotIn("总线已报告设备描述：", label)
        self.assertIn("VID 0483", label)
        self.assertIn("PID 5740", label)
        self.assertIn("MI_03", label)
        self.assertIn("实例 7&2A1B3C4D&0&0000", label)

    def test_label_keeps_all_fields_when_metadata_is_absent(self) -> None:
        path = r"\\?\HID#DEVICE_WITHOUT_USB_ID#INSTANCE_A#{GUID}"
        label = keyboard_device_label(path, 1, None)

        self.assertIn(NO_BUS_DESCRIPTION, label)
        self.assertNotIn("总线已报告设备描述：", label)
        self.assertIn("VID 未报告", label)
        self.assertIn("PID 未报告", label)
        self.assertIn("MI_未报告", label)
        self.assertIn("实例 INSTANCE_A", label)

    def test_converts_raw_path_and_rejects_non_hid_enumerators(self) -> None:
        self.assertEqual(
            _raw_path_to_instance_id(KEYBOARD_PATH),
            r"HID\VID_0483&PID_5740&MI_03\7&2A1B3C4D&0&0000",
        )
        self.assertTrue(is_hid_keyboard_path(KEYBOARD_PATH))
        self.assertFalse(is_hid_keyboard_path(r"\\?\ACPI#PNP0303#4&1234&0#{GUID}"))


class RawKeyboardMonitorTests(unittest.TestCase):
    def test_filters_by_the_complete_path_case_insensitively(self) -> None:
        monitor = RawKeyboardMonitor(start=False)
        monitor.set_target(KEYBOARD_PATH)

        self.assertTrue(monitor.accepts_device_path(KEYBOARD_PATH.lower()))
        self.assertFalse(
            monitor.accepts_device_path(KEYBOARD_PATH.replace("MI_03", "MI_04"))
        )
        self.assertFalse(
            monitor.accepts_device_path(KEYBOARD_PATH.replace("2A1B3C4D", "99999999"))
        )

    def test_reports_hotplug_removal_with_cached_full_path(self) -> None:
        monitor = RawKeyboardMonitor(start=False)
        monitor._device_name_cache[123] = KEYBOARD_PATH

        monitor._handle_device_change(GIDC_REMOVAL, 123)

        event = monitor.events.get_nowait()
        self.assertEqual(event.kind, "device-change")
        self.assertEqual(event.change, "removal")
        self.assertEqual(event.device_path, KEYBOARD_PATH)
        self.assertNotIn(123, monitor._device_name_cache)

    @patch("tenodx_config.raw_keyboard.IS_WINDOWS", False)
    def test_non_windows_fallback_is_safe(self) -> None:
        self.assertEqual(enumerate_raw_keyboards(), [])
        self.assertEqual(list_raw_keyboard_devices(), [])
        self.assertEqual(list_serial_bus_descriptions(), {})
        monitor = RawKeyboardMonitor()
        self.assertIsNotNone(monitor.error)
        monitor.close()


if __name__ == "__main__":
    unittest.main()
