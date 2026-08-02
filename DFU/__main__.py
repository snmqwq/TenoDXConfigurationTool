"""Command-line entry point for the standalone DFU component."""

from __future__ import annotations

import argparse
from pathlib import Path

from .flasher import DEFAULT_LEAVE_DELAY, DfuError, flash_firmware


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用随组件提供的 dfu-util 刷写一个已经进入 DFU 的 STM32 设备。"
    )
    parser.add_argument(
        "--device-id", required=True, help="dfu-util 设备 ID，例如 0483:DF11"
    )
    parser.add_argument("--serial", required=True, help="目标 DFU 设备的 USB 序列号")
    parser.add_argument(
        "--firmware", required=True, type=Path, help="带时间戳的 BIN 固件路径"
    )
    parser.add_argument(
        "--leave-delay",
        type=float,
        default=DEFAULT_LEAVE_DELAY,
        help="刷写完成后、退出 DFU 前的等待秒数，默认 0.5",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        flash_firmware(
            device_id=args.device_id,
            serial_number=args.serial,
            firmware_path=args.firmware,
            on_output=print,
            leave_delay=args.leave_delay,
        )
    except DfuError as error:
        print(f"刷写失败: {error}")
        return 1
    print("固件刷写完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
