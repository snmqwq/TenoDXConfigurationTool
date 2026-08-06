"""Windowed entry point for the standalone device-configuration build."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox


def _show_startup_error(error: BaseException) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror("TenoDX Config", f"无法启动配置程序：{error}")
    finally:
        root.destroy()


def main() -> int:
    try:
        from tenodx_config.device_config_ui import launch_device_config

        return launch_device_config()
    except Exception as error:
        _show_startup_error(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
