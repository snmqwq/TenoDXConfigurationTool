"""Windowed entry point used only for the standalone controller-test build."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox


def _show_startup_error(error: BaseException) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror("TenoDX Controller Test", f"无法启动测试程序：{error}")
    finally:
        root.destroy()


def main() -> int:
    try:
        from tenodx_config.controller_test_ui import launch_controller_test

        return launch_controller_test()
    except Exception as error:
        _show_startup_error(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
