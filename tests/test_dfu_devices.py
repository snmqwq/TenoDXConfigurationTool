from __future__ import annotations

import unittest

from tenodx_config.dfu_devices import parse_dfu_devices

SAMPLE = """
dfu-util 0.11
Found DFU: [0483:df11] ver=2200, devnum=8, cfg=1, intf=0, path="1-2", alt=1, name="Option Bytes", serial="ABC123"
Found DFU: [0483:df11] ver=2200, devnum=8, cfg=1, intf=0, path="1-2", alt=0, name="Internal Flash", serial="ABC123"
Found DFU: [0483:df11] ver=2200, devnum=9, cfg=1, intf=0, path="1-3", alt=0, name="Internal Flash", serial="DEF456"
Found DFU: [1234:5678] ver=0100, devnum=4, cfg=1, intf=0, path="1-4", alt=0, name="Other", serial="OTHER"
"""


class ParseDfuDevicesTests(unittest.TestCase):
    def test_deduplicates_alt_settings_by_serial(self) -> None:
        devices = parse_dfu_devices(SAMPLE, "0483:DF11")
        self.assertEqual(
            [device.serial_number for device in devices], ["ABC123", "DEF456"]
        )
        self.assertEqual(devices[0].usb_path, "1-2")
        self.assertEqual(devices[0].devnum, 8)

    def test_ignores_entries_without_serial(self) -> None:
        output = 'Found DFU: [0483:df11] devnum=1, path="1-1", alt=0, serial=""'
        self.assertEqual(parse_dfu_devices(output, "0483:DF11"), [])


if __name__ == "__main__":
    unittest.main()
