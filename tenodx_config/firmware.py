"""Timestamped firmware discovery and interactive selection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from DFU.flasher import FIRMWARE_NAME_RE, validate_firmware


class FirmwareSelectionError(RuntimeError):
    """Raised when no valid firmware can be selected."""


@dataclass(frozen=True)
class FirmwareCandidate:
    path: Path
    timestamp: datetime


def parse_firmware_candidate(path: Path) -> FirmwareCandidate | None:
    match = FIRMWARE_NAME_RE.fullmatch(path.name)
    if match is None or not path.is_file():
        return None
    try:
        timestamp = datetime.strptime(  # noqa: DTZ007 - filename stores local wall time
            match.group("date") + match.group("time"), "%Y%m%d%H%M%S"
        )
    except ValueError:
        return None
    if path.stat().st_size <= 0:
        return None
    return FirmwareCandidate(path=path.resolve(), timestamp=timestamp)


def discover_firmware(firmware_dir: Path) -> list[FirmwareCandidate]:
    directory = firmware_dir.expanduser().resolve()
    if not directory.is_dir():
        raise FirmwareSelectionError(f"固件目录不存在: {directory}")
    candidates = [
        candidate
        for path in directory.iterdir()
        if (candidate := parse_firmware_candidate(path)) is not None
    ]
    return sorted(candidates, key=lambda item: item.timestamp, reverse=True)


def validate_selected_firmware(path: Path, firmware_dir: Path) -> Path:
    selected = validate_firmware(path)
    directory = firmware_dir.expanduser().resolve()
    if selected.parent != directory:
        raise FirmwareSelectionError(
            f"固件必须直接位于 {directory}，当前文件为 {selected}"
        )
    return selected


def select_firmware(
    candidates: Iterable[FirmwareCandidate],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> Path:
    items = list(candidates)
    if not items:
        raise FirmwareSelectionError(
            "未找到 maimai_controller_H503_YYYYMMDD_HHMMSS.bin 格式的非空固件。"
        )
    if len(items) == 1:
        output_fn(f"已选择唯一固件: {items[0].path.name}")
        return items[0].path

    output_fn("发现多个固件，请选择：")
    for index, item in enumerate(items, 1):
        output_fn(f"  {index}. {item.path.name}  ({item.timestamp:%Y-%m-%d %H:%M:%S})")
    while True:
        answer = input_fn("固件编号: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1].path
        output_fn("固件编号无效，请重新输入。")
