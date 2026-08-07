from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tenodx_config.config_file import (
    CONFIG_FILE_FORMAT,
    CONFIG_FILE_VERSION,
    ConfigFileError,
    document_to_snapshot,
    read_config_file,
    snapshot_to_document,
    write_config_file,
)
from tenodx_config.device_config import (
    LAYOUT_2P,
    TOUCH_CDC_MODE_RAW,
    TOUCH_ZONE_NAMES,
    DeviceConfigSnapshot,
    KeyboardConfig,
    LedConfig,
    TouchConfig,
    TouchMapEntry,
)


def make_snapshot() -> DeviceConfigSnapshot:
    return DeviceConfigSnapshot(
        touch=TouchConfig(
            entries=tuple(
                TouchMapEntry(zone=TOUCH_ZONE_NAMES[index]) for index in range(34)
            ),
            cdc_mode=TOUCH_CDC_MODE_RAW,
        ),
        led=LedConfig(led_per_bit=4, rainbow_enabled=True),
        keyboard=KeyboardConfig(
            main_layout=LAYOUT_2P,
            ek_keycodes=(0x00, 0x04, 0xFE, 0xFF),
        ),
    )


class ConfigDocumentTests(unittest.TestCase):
    def test_complete_document_round_trips_unknown_hid_bytes(self) -> None:
        snapshot = make_snapshot()
        document = snapshot_to_document(snapshot)

        self.assertEqual(document["format"], CONFIG_FILE_FORMAT)
        self.assertEqual(document["version"], CONFIG_FILE_VERSION)
        self.assertEqual(document["touch"]["cdc_mode"], "raw")
        self.assertEqual(document["touch"]["channels"][33]["channel"], 33)
        self.assertEqual(document["keyboard"]["ek_keycodes"], [0, 4, 254, 255])
        self.assertEqual(document_to_snapshot(document), snapshot)

    def test_rejects_partial_extra_and_out_of_order_documents(self) -> None:
        document = snapshot_to_document(make_snapshot())

        missing = copy.deepcopy(document)
        del missing["led"]
        with self.assertRaisesRegex(ConfigFileError, "缺少字段：led"):
            document_to_snapshot(missing)

        extra = copy.deepcopy(document)
        extra["checksum"] = "not-supported"
        with self.assertRaisesRegex(ConfigFileError, "未知字段：checksum"):
            document_to_snapshot(extra)

        out_of_order = copy.deepcopy(document)
        out_of_order["touch"]["channels"][3]["channel"] = 4
        with self.assertRaisesRegex(ConfigFileError, "必须按 0–33 顺序"):
            document_to_snapshot(out_of_order)

    def test_rejects_schema_and_value_type_drift(self) -> None:
        document = snapshot_to_document(make_snapshot())

        bad_version = copy.deepcopy(document)
        bad_version["version"] = True
        with self.assertRaisesRegex(ConfigFileError, "version 必须是整数"):
            document_to_snapshot(bad_version)

        bad_mode = copy.deepcopy(document)
        bad_mode["touch"]["cdc_mode"] = ["raw"]
        with self.assertRaisesRegex(ConfigFileError, "必须是 raw 或 mai2touch"):
            document_to_snapshot(bad_mode)

        bad_rainbow = copy.deepcopy(document)
        bad_rainbow["led"]["rainbow_enabled"] = 1
        with self.assertRaisesRegex(ConfigFileError, "必须是布尔值"):
            document_to_snapshot(bad_rainbow)

        bad_keycode = copy.deepcopy(document)
        bad_keycode["keyboard"]["ek_keycodes"][2] = 256
        with self.assertRaisesRegex(ConfigFileError, "必须在 0–255"):
            document_to_snapshot(bad_keycode)

    def test_utf8_json_file_round_trip_and_invalid_json(self) -> None:
        snapshot = make_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.tenodx.json")
            write_config_file(path, snapshot)
            self.assertEqual(read_config_file(path), snapshot)
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))

            path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(ConfigFileError, "不是有效的 UTF-8 JSON"):
                read_config_file(path)

            path.write_text(json.dumps({"format": CONFIG_FILE_FORMAT}), encoding="utf-8")
            with self.assertRaisesRegex(ConfigFileError, "缺少字段"):
                read_config_file(path)


if __name__ == "__main__":
    unittest.main()
