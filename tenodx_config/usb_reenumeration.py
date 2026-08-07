"""Windows PnP removal and USB re-enumeration for a selected DFU device."""

from __future__ import annotations

import base64
import ctypes
import json
import locale
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from DFU.flasher import (
    subprocess_creation_flags,
    validate_device_id,
    validate_serial_number,
)

USB_PORT_RE = re.compile(r"#USB\((?P<port>\d+)\)", re.IGNORECASE)
DFU_PATH_RE = re.compile(r"^\d+-(?P<ports>\d+(?:\.\d+)*)$")


class UsbReenumerationError(RuntimeError):
    """Raised when a selected DFU node cannot be removed or rescanned."""


@dataclass(frozen=True)
class PnpDevice:
    instance_id: str
    location_paths: str
    service: str = ""
    friendly_name: str = ""


def get_pnputil_path() -> Path:
    """Locate the native Windows PnPUtil, including from 32-bit Python."""
    if sys.platform != "win32":
        raise UsbReenumerationError("USB 设备重新枚举仅支持 Windows。")
    windows_dir = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    candidates = (
        windows_dir / "Sysnative" / "pnputil.exe",
        windows_dir / "System32" / "pnputil.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise UsbReenumerationError("找不到 Windows 系统工具 pnputil.exe。")


def get_powershell_path() -> Path:
    """Locate 64-bit Windows PowerShell, including from 32-bit Python."""
    if sys.platform != "win32":
        raise UsbReenumerationError("USB 设备枚举仅支持 Windows。")
    windows_dir = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    candidates = (
        windows_dir / "Sysnative" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        windows_dir / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise UsbReenumerationError("找不到 Windows PowerShell 5.1。")


def ensure_usb_reenumeration_available() -> None:
    """Fail before flashing when PnP removal cannot run in this process."""
    get_pnputil_path()
    get_powershell_path()
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise UsbReenumerationError(
            "卸载 DFU 设备需要管理员权限，请以管理员身份运行本程序。"
        )


def _run_pnputil(arguments: list[str], timeout: float = 30.0) -> tuple[int, str]:
    executable = get_pnputil_path()
    try:
        result = subprocess.run(
            [str(executable), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding=locale.getpreferredencoding(False),
            errors="replace",
            creationflags=subprocess_creation_flags(),
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UsbReenumerationError(f"无法运行 pnputil: {error}") from error
    return result.returncode, result.stdout or ""


def _run_powershell(script: str, timeout: float = 30.0) -> tuple[int, str]:
    executable = get_powershell_path()
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                str(executable),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            errors="replace",
            creationflags=subprocess_creation_flags(),
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UsbReenumerationError(
            f"无法运行 Windows PowerShell 设备枚举: {error}"
        ) from error
    output = result.stdout or ""
    if result.returncode != 0 and result.stderr:
        output = f"{output}\n{result.stderr}".strip()
    return result.returncode, output


def _hardware_id(device_id: str) -> str:
    vendor_id, product_id = validate_device_id(device_id).split(":", 1)
    return f"USB\\VID_{vendor_id}&PID_{product_id}"


def _parse_pnp_devices(output: str, hardware_id: str) -> list[PnpDevice]:
    expected_prefix = f"{hardware_id}\\".casefold()
    content = output.strip().lstrip("\ufeff")
    if not content:
        return []
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as error:
        raise UsbReenumerationError(
            f"无法解析 Windows PowerShell 设备枚举结果: {error}"
        ) from error
    rows = [decoded] if isinstance(decoded, dict) else decoded
    if not isinstance(rows, list):
        raise UsbReenumerationError("Windows PowerShell 设备枚举结果格式无效。")

    devices: list[PnpDevice] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance_id = str(row.get("InstanceId") or "").strip()
        if not instance_id.casefold().startswith(expected_prefix):
            continue
        location_paths = row.get("LocationPaths") or []
        if isinstance(location_paths, str):
            location_paths = [location_paths]
        devices.append(
            PnpDevice(
                instance_id=instance_id,
                location_paths=";".join(
                    str(path).strip() for path in location_paths if path
                ),
                service=str(row.get("Service") or "").strip(),
                friendly_name=str(row.get("FriendlyName") or "").strip(),
            )
        )
    return devices


def _pnp_enumeration_script(hardware_id: str) -> str:
    prefix = f"{hardware_id}\\"
    return rf"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {{
    $prefix = '{prefix}'
    $devices = @(
        Get-PnpDevice -PresentOnly -ErrorAction Stop |
            Where-Object {{
                $_.InstanceId.StartsWith(
                    $prefix,
                    [StringComparison]::OrdinalIgnoreCase
                )
            }} |
            ForEach-Object {{
                $locationProperty = Get-PnpDeviceProperty `
                    -InstanceId $_.InstanceId `
                    -KeyName 'DEVPKEY_Device_LocationPaths' `
                    -ErrorAction SilentlyContinue
                $serviceProperty = Get-PnpDeviceProperty `
                    -InstanceId $_.InstanceId `
                    -KeyName 'DEVPKEY_Device_Service' `
                    -ErrorAction SilentlyContinue
                [pscustomobject]@{{
                    InstanceId = [string]$_.InstanceId
                    LocationPaths = @($locationProperty.Data)
                    Service = [string]$serviceProperty.Data
                    FriendlyName = [string]$_.FriendlyName
                }}
            }}
    )
    ConvertTo-Json -InputObject @($devices) -Compress -Depth 4
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}}
"""


def list_connected_dfu_nodes(device_id: str) -> list[PnpDevice]:
    hardware_id = _hardware_id(device_id)
    return_code, output = _run_powershell(_pnp_enumeration_script(hardware_id))
    if return_code != 0:
        detail = f"\n\n{output}" if output else ""
        raise UsbReenumerationError(
            "无法通过 Windows PowerShell 枚举已连接的 "
            f"{device_id} DFU 设备，退出码 {return_code}。{detail}"
        )
    return _parse_pnp_devices(output, hardware_id)


def describe_new_dfu_nodes(
    device_id: str,
    previous_instance_ids: Iterable[str],
) -> str | None:
    """Describe PnP nodes that appeared even though dfu-util could not list them."""

    previous = {instance_id.casefold() for instance_id in previous_instance_ids}
    added = [
        device
        for device in list_connected_dfu_nodes(device_id)
        if device.instance_id.casefold() not in previous
    ]
    if not added:
        return None

    compatible_services = {"winusb", "libusb0", "libusbk"}
    incompatible = [
        device for device in added if device.service.casefold() not in compatible_services
    ]
    details = "\n".join(
        "  "
        + f"{device.friendly_name or '未知设备'}  "
        + f"service={device.service or '未知'}  {device.instance_id}"
        for device in added
    )
    if incompatible:
        return (
            "Windows 已经枚举出新的 DFU 设备，但其驱动不兼容 dfu-util：\n"
            f"{details}\n"
            "请运行发布目录 DFU-driver\\install_dfu_driver.cmd，安装并切换到 "
            "STMicroelectronics 签名的 WinUSB 驱动，然后重新执行刷写。"
        )
    return (
        "Windows 已经枚举出新的 DFU 设备，驱动服务看起来兼容，但 dfu-util 仍未列出：\n"
        f"{details}\n"
        "请确认没有其他刷机程序正在占用该设备，并保留本段信息用于诊断。"
    )


def _dfu_port_chain(usb_path: str) -> tuple[int, ...] | None:
    match = DFU_PATH_RE.fullmatch(usb_path.strip())
    if match is None:
        return None
    return tuple(int(port) for port in match.group("ports").split("."))


def _pnp_port_chains(location_paths: str) -> set[tuple[int, ...]]:
    chains: set[tuple[int, ...]] = set()
    for location_path in location_paths.split(";"):
        ports = tuple(
            int(match.group("port")) for match in USB_PORT_RE.finditer(location_path)
        )
        if ports:
            chains.add(ports)
    return chains


def _select_target_node(
    devices: Iterable[PnpDevice],
    serial_number: str,
    usb_path: str,
) -> PnpDevice | None:
    candidates = list(devices)
    if not candidates:
        return None

    serial = validate_serial_number(serial_number)
    if serial.casefold() != "unknown":
        serial_matches = [
            device
            for device in candidates
            if device.instance_id.rsplit("\\", 1)[-1].casefold()
            == serial.casefold()
        ]
        if len(serial_matches) == 1:
            return serial_matches[0]

    port_chain = _dfu_port_chain(usb_path)
    if port_chain is not None:
        location_matches = [
            device
            for device in candidates
            if port_chain in _pnp_port_chains(device.location_paths)
        ]
        if len(location_matches) == 1:
            return location_matches[0]

    if len(candidates) == 1:
        return candidates[0]

    instances = "\n".join(f"  {device.instance_id}" for device in candidates)
    raise UsbReenumerationError(
        "无法根据 USB 序列号或物理端口唯一确定待卸载的 DFU 设备：\n"
        f"{instances}"
    )


def remove_dfu_device_and_rescan(
    device_id: str,
    serial_number: str,
    usb_path: str,
    on_output: Callable[[str], None] | None = None,
) -> None:
    """Remove the selected DFU PnP node and synchronously rescan devices."""
    target = _select_target_node(
        list_connected_dfu_nodes(device_id),
        serial_number,
        usb_path,
    )
    if target is not None:
        return_code, output = _run_pnputil(["/remove-device", target.instance_id])
        if return_code != 0:
            remaining = list_connected_dfu_nodes(device_id)
            if any(device.instance_id == target.instance_id for device in remaining):
                detail = f"\n\n{output}" if output else ""
                raise UsbReenumerationError(
                    f"无法卸载 DFU 设备 {target.instance_id}，退出码 {return_code}。"
                    f"{detail}"
                )
        if on_output is not None:
            on_output(f"已卸载 DFU 设备节点: {target.instance_id}")
    elif on_output is not None:
        on_output("DFU 设备节点已经离线，无需卸载。")

    return_code, output = _run_pnputil(["/scan-devices"])
    if return_code != 0:
        detail = f"\n\n{output}" if output else ""
        raise UsbReenumerationError(
            f"USB 设备重新扫描失败，退出码 {return_code}。{detail}"
        )
    if on_output is not None:
        on_output("已触发 USB 设备重新枚举。")
