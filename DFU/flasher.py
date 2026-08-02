"""Minimal flashing-only wrapper around the bundled dfu-util executable."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

DEFAULT_FLASH_ADDRESS = 0x08000000
FIRMWARE_NAME_RE = re.compile(
    r"^maimai_controller_H503_(?P<date>\d{8})_(?P<time>\d{6})\.bin$"
)
DEVICE_ID_RE = re.compile(r"^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}$")


class DfuError(RuntimeError):
    """Raised when validation or dfu-util flashing fails."""


def component_path(*parts: str) -> Path:
    """Return a path inside the standalone DFU component."""
    return Path(__file__).resolve().parent.joinpath(*parts)


def get_dfu_util_path() -> Path:
    """Locate the bundled Windows x64 dfu-util executable."""
    if sys.platform != "win32":
        raise DfuError(f"当前 DFU 组件只支持 Windows x64，当前平台为 {sys.platform}。")
    path = component_path("vendor", "dfu-util", "windows-x64", "dfu-util.exe")
    if not path.is_file():
        raise DfuError(f"缺少随组件提供的 dfu-util.exe: {path}")
    return path


def validate_device_id(device_id: str) -> str:
    normalized = device_id.strip().upper()
    if not DEVICE_ID_RE.fullmatch(normalized):
        raise DfuError(f"DFU 设备 ID 格式无效: {device_id!r}，应类似 0483:DF11。")
    return normalized


def validate_serial_number(serial_number: str) -> str:
    normalized = serial_number.strip()
    if not normalized:
        raise DfuError("缺少目标 DFU 设备的 USB 序列号。")
    if any(character in normalized for character in ("\r", "\n", ",")):
        raise DfuError("USB 序列号包含不支持的字符。")
    return normalized


def validate_firmware(firmware_path: Path | str) -> Path:
    path = Path(firmware_path).expanduser().resolve()
    if not path.is_file():
        raise DfuError(f"固件文件不存在: {path}")
    if FIRMWARE_NAME_RE.fullmatch(path.name) is None:
        raise DfuError(
            "固件名必须为 maimai_controller_H503_YYYYMMDD_HHMMSS.bin，"
            f"当前文件为 {path.name}。"
        )
    if path.stat().st_size <= 0:
        raise DfuError(f"固件文件为空: {path}")
    return path


def build_flash_command(
    device_id: str,
    serial_number: str,
    firmware_path: Path | str,
    flash_address: int = DEFAULT_FLASH_ADDRESS,
) -> list[str]:
    """Validate inputs and build the exact dfu-util flashing command."""
    if flash_address < 0 or flash_address > 0xFFFFFFFF:
        raise DfuError(f"无效的 Flash 地址: {flash_address}")
    executable = get_dfu_util_path()
    device = validate_device_id(device_id)
    serial = validate_serial_number(serial_number)
    firmware = validate_firmware(firmware_path)
    return [
        str(executable),
        "-d",
        device,
        "-S",
        serial,
        "-a",
        "0",
        "-s",
        f"0x{flash_address:08X}:leave",
        "-D",
        str(firmware),
    ]


def subprocess_creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _is_expected_leave_status_error(output: str) -> bool:
    """Return whether dfu-util failed only because leave disconnected the device."""
    normalized = output.casefold()
    return all(
        marker in normalized
        for marker in (
            "file downloaded successfully",
            "submitting leave request",
            "error during download get_status",
        )
    )


def flash_firmware(
    device_id: str,
    serial_number: str,
    firmware_path: Path | str,
    on_output: Callable[[str], None] | None = None,
) -> str:
    """
    Flash one selected DFU device and ask it to leave DFU mode.

    Device discovery, firmware selection, entering DFU, and post-flash Magic
    verification intentionally belong to the calling application.
    """
    command = build_flash_command(device_id, serial_number, firmware_path)
    executable_dir = Path(command[0]).parent
    try:
        process = subprocess.Popen(
            command,
            cwd=executable_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess_creation_flags(),
        )
    except OSError as error:
        raise DfuError(f"无法启动 dfu-util: {error}") from error

    output_lines: list[str] = []
    if process.stdout is None:
        process.kill()
        raise DfuError("无法读取 dfu-util 输出。")

    try:
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            output_lines.append(line)
            if on_output is not None:
                on_output(line)
    except BaseException:
        if process.poll() is None:
            process.terminate()
        process.wait()
        raise

    return_code = process.wait()
    output = "\n".join(output_lines)
    if return_code != 0 and not _is_expected_leave_status_error(output):
        detail = f"\n\n{output}" if output else ""
        raise DfuError(f"dfu-util 刷写失败，退出码 {return_code}。{detail}")
    if return_code != 0 and on_output is not None:
        on_output("leave 后设备未响应最终状态查询；固件数据已写入完成。")
    return output
