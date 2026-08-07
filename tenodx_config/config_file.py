"""Strict JSON import and export for complete TenoDX configurations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .device_config import (
    LAYOUT_1P,
    LAYOUT_2P,
    TOUCH_CDC_MODE_MAI2TOUCH,
    TOUCH_CDC_MODE_RAW,
    DeviceConfigSnapshot,
    KeyboardConfig,
    LedConfig,
    TouchConfig,
    TouchMapEntry,
)

CONFIG_FILE_FORMAT = "tenodx-device-config"
CONFIG_FILE_VERSION = 1
CONFIG_FILE_EXTENSION = ".tenodx.json"

_TOP_LEVEL_FIELDS = {"format", "version", "touch", "led", "keyboard"}
_TOUCH_FIELDS = {"cdc_mode", "channels"}
_TOUCH_CHANNEL_FIELDS = {"channel", "zone"}
_LED_FIELDS = {"led_per_bit", "rainbow_enabled"}
_KEYBOARD_FIELDS = {"main_layout", "ek_keycodes"}


class ConfigFileError(ValueError):
    """Raised when a configuration file does not match the supported schema."""


def _require_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ConfigFileError(f"{name} 必须是 JSON 对象")
    return value


def _require_exact_fields(
    value: dict[str, Any], expected: set[str], name: str
) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        raise ConfigFileError(f"{name} 缺少字段：{', '.join(missing)}")
    if extra:
        raise ConfigFileError(f"{name} 包含未知字段：{', '.join(extra)}")


def _require_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigFileError(f"{name} 必须是整数")
    return value


def snapshot_to_document(snapshot: DeviceConfigSnapshot) -> dict[str, Any]:
    """Return one deterministic, human-readable JSON document."""

    if not isinstance(snapshot, DeviceConfigSnapshot):
        raise TypeError("snapshot must be a DeviceConfigSnapshot")
    touch_mode = {
        TOUCH_CDC_MODE_RAW: "raw",
        TOUCH_CDC_MODE_MAI2TOUCH: "mai2touch",
    }[snapshot.touch.cdc_mode]
    layout = {LAYOUT_1P: "1P", LAYOUT_2P: "2P"}[snapshot.keyboard.main_layout]
    return {
        "format": CONFIG_FILE_FORMAT,
        "version": CONFIG_FILE_VERSION,
        "touch": {
            "cdc_mode": touch_mode,
            "channels": [
                {"channel": channel, "zone": entry.zone}
                for channel, entry in enumerate(snapshot.touch.entries)
            ],
        },
        "led": {
            "led_per_bit": snapshot.led.led_per_bit,
            "rainbow_enabled": snapshot.led.rainbow_enabled,
        },
        "keyboard": {
            "main_layout": layout,
            "ek_keycodes": list(snapshot.keyboard.ek_keycodes),
        },
    }


def document_to_snapshot(document: Any) -> DeviceConfigSnapshot:
    """Validate and decode one complete configuration document."""

    root = _require_object(document, "配置文件")
    _require_exact_fields(root, _TOP_LEVEL_FIELDS, "配置文件")
    if root["format"] != CONFIG_FILE_FORMAT:
        raise ConfigFileError("配置文件格式标识不受支持")
    version = _require_integer(root["version"], "version")
    if version != CONFIG_FILE_VERSION:
        raise ConfigFileError(f"配置文件版本不受支持：{version}")

    touch_data = _require_object(root["touch"], "touch")
    _require_exact_fields(touch_data, _TOUCH_FIELDS, "touch")
    mode_name = touch_data["cdc_mode"]
    mode_by_name = {
        "raw": TOUCH_CDC_MODE_RAW,
        "mai2touch": TOUCH_CDC_MODE_MAI2TOUCH,
    }
    if not isinstance(mode_name, str) or mode_name not in mode_by_name:
        raise ConfigFileError("touch.cdc_mode 必须是 raw 或 mai2touch")
    channels = touch_data["channels"]
    if not isinstance(channels, list) or len(channels) != 34:
        raise ConfigFileError("touch.channels 必须完整包含 34 个通道")
    entries: list[TouchMapEntry] = []
    for expected_channel, raw_entry in enumerate(channels):
        entry = _require_object(raw_entry, f"touch.channels[{expected_channel}]")
        _require_exact_fields(
            entry,
            _TOUCH_CHANNEL_FIELDS,
            f"touch.channels[{expected_channel}]",
        )
        channel = _require_integer(
            entry["channel"], f"touch.channels[{expected_channel}].channel"
        )
        if channel != expected_channel:
            raise ConfigFileError(
                "touch.channels 必须按 0–33 顺序完整排列；"
                f"位置 {expected_channel} 的通道号为 {channel}"
            )
        try:
            entries.append(TouchMapEntry(zone=entry["zone"]))
        except (TypeError, ValueError) as error:
            raise ConfigFileError(
                f"touch.channels[{expected_channel}].zone 无效：{error}"
            ) from error

    led_data = _require_object(root["led"], "led")
    _require_exact_fields(led_data, _LED_FIELDS, "led")
    led_per_bit = _require_integer(led_data["led_per_bit"], "led.led_per_bit")
    rainbow_enabled = led_data["rainbow_enabled"]
    if not isinstance(rainbow_enabled, bool):
        raise ConfigFileError("led.rainbow_enabled 必须是布尔值")
    try:
        led = LedConfig(
            led_per_bit=led_per_bit,
            rainbow_enabled=rainbow_enabled,
        )
    except (TypeError, ValueError) as error:
        raise ConfigFileError(f"led 配置无效：{error}") from error

    keyboard_data = _require_object(root["keyboard"], "keyboard")
    _require_exact_fields(keyboard_data, _KEYBOARD_FIELDS, "keyboard")
    layout_name = keyboard_data["main_layout"]
    layout_by_name = {"1P": LAYOUT_1P, "2P": LAYOUT_2P}
    if not isinstance(layout_name, str) or layout_name not in layout_by_name:
        raise ConfigFileError("keyboard.main_layout 必须是 1P 或 2P")
    raw_keycodes = keyboard_data["ek_keycodes"]
    if not isinstance(raw_keycodes, list) or len(raw_keycodes) != 4:
        raise ConfigFileError("keyboard.ek_keycodes 必须包含四个 HID 字节")
    keycodes: list[int] = []
    for index, raw_keycode in enumerate(raw_keycodes):
        keycode = _require_integer(raw_keycode, f"keyboard.ek_keycodes[{index}]")
        if not 0 <= keycode <= 0xFF:
            raise ConfigFileError(
                f"keyboard.ek_keycodes[{index}] 必须在 0–255 范围内"
            )
        keycodes.append(keycode)

    return DeviceConfigSnapshot(
        touch=TouchConfig(
            entries=tuple(entries),
            cdc_mode=mode_by_name[mode_name],
        ),
        led=led,
        keyboard=KeyboardConfig(
            main_layout=layout_by_name[layout_name],
            ek_keycodes=tuple(keycodes),  # type: ignore[arg-type]
        ),
    )


def write_config_file(path: str | Path, snapshot: DeviceConfigSnapshot) -> None:
    """Write one complete configuration as UTF-8 JSON."""

    target = Path(path)
    with target.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(
            snapshot_to_document(snapshot),
            output,
            ensure_ascii=False,
            indent=2,
        )
        output.write("\n")


def read_config_file(path: str | Path) -> DeviceConfigSnapshot:
    """Read and strictly validate one UTF-8 JSON configuration file."""

    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as input_file:
            document = json.load(input_file)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigFileError(f"配置文件不是有效的 UTF-8 JSON：{error}") from error
    return document_to_snapshot(document)


__all__ = [
    "CONFIG_FILE_EXTENSION",
    "CONFIG_FILE_FORMAT",
    "CONFIG_FILE_VERSION",
    "ConfigFileError",
    "document_to_snapshot",
    "read_config_file",
    "snapshot_to_document",
    "write_config_file",
]
