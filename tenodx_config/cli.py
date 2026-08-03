"""Command-line orchestration for TenoDX device configuration."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

from DFU import DfuError, flash_firmware

from .dfu_devices import list_dfu_devices, select_dfu_device, wait_for_new_dfu_devices
from .firmware import (
    FirmwareSelectionError,
    discover_firmware,
    select_firmware,
    validate_selected_firmware,
)
from .magic import (
    MagicError,
    MagicPort,
    discover_magic_ports,
    send_enter_dfu,
    wait_for_magic_return,
)
from .usb_reenumeration import (
    UsbReenumerationError,
    ensure_usb_reenumeration_available,
    remove_dfu_device_and_rescan,
)

DEFAULT_DEVICE_ID = "0483:DF11"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_DIR = PROJECT_ROOT / "firmware"
T = TypeVar("T")


class CliError(RuntimeError):
    """Raised for command-line workflow errors."""


def select_item(
    items: Iterable[T],
    title: str,
    describe: Callable[[T], str],
    prompt: str,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> T:
    choices = list(items)
    if not choices:
        raise CliError(f"没有可选择的{title}。")
    if len(choices) == 1:
        return choices[0]
    output_fn(f"发现多个{title}，请选择：")
    for index, item in enumerate(choices, 1):
        output_fn(f"  {index}. {describe(item)}")
    while True:
        answer = input_fn(prompt).strip()
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        output_fn("编号无效，请重新输入。")


def select_magic_port(ports: Iterable[MagicPort]) -> MagicPort:
    return select_item(
        ports,
        title="兼容的 TenoDX Aime/Magic 串口",
        describe=lambda port: "  ".join(
            part for part in (port.device, port.description, port.usb_serial) if part
        ),
        prompt="串口编号: ",
    )


def resolve_firmware(specified: Path | None) -> Path:
    if specified is not None:
        candidate = specified
        if not candidate.is_absolute():
            candidate = (
                PROJECT_ROOT / candidate
                if candidate.parts and candidate.parts[0].casefold() == "firmware"
                else FIRMWARE_DIR / candidate
            )
        return validate_selected_firmware(candidate, FIRMWARE_DIR)
    return select_firmware(discover_firmware(FIRMWARE_DIR))


def run_dfu_update(args: argparse.Namespace) -> int:
    ensure_usb_reenumeration_available()
    firmware = resolve_firmware(args.firmware)
    print(f"固件: {firmware.name}")

    print("[1/6] 正在查找并验证 TenoDX Aime/Magic 串口...")
    magic_ports = discover_magic_ports(args.port)
    if not magic_ports:
        target = f" {args.port}" if args.port else ""
        raise CliError(f"未找到兼容的 TenoDX Aime/Magic 串口{target}。")
    selected_magic = select_magic_port(magic_ports)
    other_magic = [port for port in magic_ports if port != selected_magic]
    print(f"应用设备: {selected_magic.device}")

    existing_dfu, _ = list_dfu_devices(args.device_id)
    previous_serials = {device.serial_number for device in existing_dfu}

    print("[2/6] 正在发送进入 DFU 命令...")
    send_enter_dfu(selected_magic)
    print("设备已接受 DFU 命令，应用串口已释放。")

    print(f"[3/6] 正在等待新的 {args.device_id} DFU 设备...")
    new_devices = wait_for_new_dfu_devices(
        args.device_id,
        previous_serials,
        timeout=args.dfu_timeout,
    )
    selected_dfu = select_dfu_device(new_devices)
    print(f"DFU 设备: {selected_dfu.device_id}  serial={selected_dfu.serial_number}")

    print("[4/6] 正在刷写固件...")
    flash_firmware(
        device_id=selected_dfu.device_id,
        serial_number=selected_dfu.serial_number,
        firmware_path=firmware,
        on_output=lambda line: print(f"[dfu-util] {line}"),
    )

    print("[5/6] 正在卸载 DFU 设备并重新枚举 USB 设备...")
    remove_dfu_device_and_rescan(
        device_id=selected_dfu.device_id,
        serial_number=selected_dfu.serial_number,
        usb_path=selected_dfu.usb_path,
        on_output=lambda line: print(f"[PnP] {line}"),
    )

    print("[6/6] 正在等待应用设备重新枚举并验证 Magic 协议...")
    returned = wait_for_magic_return(
        selected_magic,
        other_magic,
        timeout=args.app_timeout,
    )
    print(f"完成: {returned.device} 已通过 Magic 协议验证，串口已释放。")
    return 0


def run_live_test(_args: argparse.Namespace) -> int:
    """Launch the optional real-time controller test UI."""

    try:
        from .controller_test_ui import ControllerTestError, launch_controller_test
    except ImportError as error:
        raise CliError(f"无法加载实时测试界面：{error}") from error

    try:
        return launch_controller_test()
    except ControllerTestError as error:
        raise CliError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TenoDX 设备命令行配置程序")
    subparsers = parser.add_subparsers(dest="command")

    dfu = subparsers.add_parser("dfu", help="进入 DFU、选择固件并更新主控")
    dfu.add_argument("--port", help="指定应用模式的 Aime/Magic 串口，例如 COM7")
    dfu.add_argument(
        "--device-id", default=DEFAULT_DEVICE_ID, help="DFU VID:PID，默认 0483:DF11"
    )
    dfu.add_argument(
        "--firmware",
        type=Path,
        help="指定 firmware 目录中的时间戳 BIN；省略时自动发现或提示选择",
    )
    dfu.add_argument(
        "--dfu-timeout", type=float, default=20.0, help="等待 DFU 枚举秒数，默认 20"
    )
    dfu.add_argument(
        "--app-timeout", type=float, default=30.0, help="等待应用重新枚举秒数，默认 30"
    )
    dfu.set_defaults(handler=run_dfu_update)

    live_test = subparsers.add_parser(
        "test", help="打开 Touch、主按键、Aime 和 Mai2LED 综合测试界面"
    )
    live_test.set_defaults(handler=run_live_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    if getattr(args, "dfu_timeout", 1.0) <= 0 or getattr(args, "app_timeout", 1.0) <= 0:
        parser.error("超时时间必须大于 0。")
    try:
        return args.handler(args)
    except (
        CliError,
        DfuError,
        FirmwareSelectionError,
        MagicError,
        UsbReenumerationError,
    ) as error:
        print(f"错误: {error}")
        return 1
    except KeyboardInterrupt:
        print("\n操作已取消。")
        return 130
