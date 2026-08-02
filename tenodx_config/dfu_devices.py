"""Application-side STM32 DFU discovery using the bundled dfu-util."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from DFU.flasher import (
    DfuError,
    get_dfu_util_path,
    subprocess_creation_flags,
    validate_device_id,
)

FOUND_DFU_RE = re.compile(
    r"Found DFU:\s*\[(?P<device>[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4})\](?P<detail>.*)"
)
SERIAL_RE = re.compile(r'serial="(?P<serial>[^"]*)"')
PATH_RE = re.compile(r'path="(?P<path>[^"]*)"')
DEVNUM_RE = re.compile(r"\bdevnum=(?P<devnum>\d+)")


@dataclass(frozen=True)
class DfuDevice:
    device_id: str
    serial_number: str
    usb_path: str
    devnum: int | None


def parse_dfu_devices(output: str, device_id: str) -> list[DfuDevice]:
    expected = validate_device_id(device_id)
    devices: dict[str, DfuDevice] = {}
    for line in output.splitlines():
        match = FOUND_DFU_RE.search(line)
        if match is None or match.group("device").upper() != expected:
            continue
        detail = match.group("detail")
        serial_match = SERIAL_RE.search(detail)
        serial = serial_match.group("serial").strip() if serial_match else ""
        if not serial:
            continue
        path_match = PATH_RE.search(detail)
        devnum_match = DEVNUM_RE.search(detail)
        devices.setdefault(
            serial,
            DfuDevice(
                device_id=expected,
                serial_number=serial,
                usb_path=path_match.group("path") if path_match else "",
                devnum=int(devnum_match.group("devnum")) if devnum_match else None,
            ),
        )
    return sorted(devices.values(), key=lambda item: item.serial_number)


def list_dfu_devices(device_id: str) -> tuple[list[DfuDevice], str]:
    expected = validate_device_id(device_id)
    executable = get_dfu_util_path()
    try:
        result = subprocess.run(
            [str(executable), "-d", expected, "-l"],
            cwd=executable.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess_creation_flags(),
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DfuError(f"无法列举 DFU 设备: {error}") from error
    output = result.stdout or ""
    if result.returncode != 0:
        raise DfuError(
            f"dfu-util 列举设备失败，退出码 {result.returncode}。\n\n{output}"
        )
    return parse_dfu_devices(output, expected), output


def wait_for_new_dfu_devices(
    device_id: str,
    previous_serials: Iterable[str],
    timeout: float = 20.0,
    interval: float = 0.5,
) -> list[DfuDevice]:
    previous = set(previous_serials)
    deadline = time.monotonic() + timeout
    last_output = ""
    while time.monotonic() < deadline:
        devices, last_output = list_dfu_devices(device_id)
        added = [device for device in devices if device.serial_number not in previous]
        if added:
            return added
        time.sleep(interval)
    raise DfuError(
        f"设备接受了进入 DFU 命令，但 {timeout:g} 秒内未出现新的 {device_id} 设备。"
        f"\n\ndfu-util 最后输出：\n{last_output}"
    )


def select_dfu_device(
    devices: Iterable[DfuDevice],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> DfuDevice:
    items = list(devices)
    if not items:
        raise DfuError("没有可选择的 DFU 设备。")
    if len(items) == 1:
        return items[0]
    output_fn("出现多个新的 DFU 设备，请选择：")
    for index, device in enumerate(items, 1):
        detail = f"  path={device.usb_path}" if device.usb_path else ""
        output_fn(
            f"  {index}. {device.device_id}  serial={device.serial_number}{detail}"
        )
    while True:
        answer = input_fn("DFU 设备编号: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1]
        output_fn("DFU 设备编号无效，请重新输入。")
