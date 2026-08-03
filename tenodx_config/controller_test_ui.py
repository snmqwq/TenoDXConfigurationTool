"""Single-window realtime tests for Touch, HID, Aime, and Mai2LED."""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import serial
from PIL import ImageTk
from serial.tools import list_ports

from .aime_reader import BAUDRATES as AIME_BAUDRATES
from .aime_reader import AimeReaderController
from .controller_renderer import ControllerRenderer
from .mai2led import BLACK, LOGICAL_LIGHT_COUNT, Mai2LedController
from .raw_keyboard import (
    RawKeyboardDevice,
    RawKeyboardEvent,
    RawKeyboardMonitor,
    list_raw_keyboard_devices,
    list_serial_bus_descriptions,
)
from .touch_protocol import (
    RSET_COMMAND,
    STAT_COMMAND,
    VALID_TOUCH_MASK,
    ZONE_NAMES,
    TouchFrameParser,
)

TOUCH_BAUDRATE = 9600
TOUCH_START_DELAY_SECONDS = 0.1
TOUCH_TIMEOUT_SECONDS = 0.5
FIRST_TOUCH_FRAME_TIMEOUT_SECONDS = 1.0
TOUCH_POLL_INTERVAL_MS = 10
RAW_INPUT_POLL_INTERVAL_MS = 12
HOTPLUG_REFRESH_DELAY_MS = 200
WORKER_EVENT_POLL_INTERVAL_MS = 30
AIME_SCAN_INTERVAL_SECONDS = 0.10
DISPLAY_SIZE = 600

RGBW_HOLD_MS = 800
CHASE_HOLD_MS = 300
FADE_HOLD_MS = 300
FADE_DURATION_MS = 600
FADE_VISUAL_STEPS = 12

# Physical scan codes are used so keypad input does not depend on Num Lock.
# The 1P and 2P layouts both map onto the same displayed BTN1-BTN8 states.
BUTTON_SCANCODES_1P = (
    0x11,  # W
    0x12,  # E
    0x20,  # D
    0x2E,  # C
    0x2D,  # X
    0x2C,  # Z
    0x1E,  # A
    0x10,  # Q
)
BUTTON_SCANCODES_2P = (
    0x48,  # Keypad 8
    0x49,  # Keypad 9
    0x4D,  # Keypad 6
    0x51,  # Keypad 3
    0x50,  # Keypad 2
    0x4F,  # Keypad 1
    0x4B,  # Keypad 4
    0x47,  # Keypad 7
)
BUTTON_INDEX_BY_SCANCODE = {
    scan_code: index
    for mapping in (BUTTON_SCANCODES_1P, BUTTON_SCANCODES_2P)
    for index, scan_code in enumerate(mapping)
}


class ControllerTestError(RuntimeError):
    """Raised when the realtime test window cannot be started."""


class MainButtonState:
    """Track keyboard make/break events without latching released buttons."""

    def __init__(self) -> None:
        self.pressed_scancodes: set[int] = set()

    @property
    def mask(self) -> int:
        mask = 0
        for scan_code in self.pressed_scancodes:
            index = BUTTON_INDEX_BY_SCANCODE.get(scan_code)
            if index is not None:
                mask |= 1 << index
        return mask

    def update(self, scan_code: int, is_pressed: bool, is_extended: bool) -> bool:
        """Apply one event and report whether the displayed mask changed."""

        if is_extended or scan_code not in BUTTON_INDEX_BY_SCANCODE:
            return False
        previous_mask = self.mask
        if is_pressed:
            self.pressed_scancodes.add(scan_code)
        else:
            self.pressed_scancodes.discard(scan_code)
        return self.mask != previous_mask

    def clear(self) -> bool:
        changed = bool(self.pressed_scancodes)
        self.pressed_scancodes.clear()
        return changed


@dataclass(slots=True)
class _AimeWorkerHandle:
    generation: int
    port: str
    baudrate: int
    stop_event: threading.Event = field(default_factory=threading.Event)
    commands: queue.Queue[str] = field(default_factory=queue.Queue)
    controller: Any | None = None
    thread: threading.Thread | None = None


@dataclass(slots=True)
class _LedCommand:
    kind: str
    arguments: tuple[Any, ...] = ()
    sequence_token: int | None = None
    sequence_index: int | None = None


@dataclass(slots=True)
class _LedWorkerHandle:
    generation: int
    port: str
    stop_event: threading.Event = field(default_factory=threading.Event)
    commands: queue.Queue[_LedCommand] = field(default_factory=queue.Queue)
    controller: Any | None = None
    thread: threading.Thread | None = None


def resource_path(*parts: str) -> Path:
    """Resolve resources in source trees and future PyInstaller bundles."""

    source_root = Path(__file__).resolve().parent.parent
    bundle_root = Path(getattr(sys, "_MEIPASS", source_root))
    return bundle_root.joinpath(*parts)


def serial_port_label(port: Any, bus_description: str | None) -> str:
    """Build a serial-port label with explicit identity fields."""

    reported = (bus_description or "").strip() or "描述未报告"
    description = (getattr(port, "description", "") or "").strip() or "未报告"
    serial_number = (getattr(port, "serial_number", "") or "").strip() or "未报告"
    vid = getattr(port, "vid", None)
    pid = getattr(port, "pid", None)
    vid_text = f"{vid:04X}" if isinstance(vid, int) else "未报告"
    pid_text = f"{pid:04X}" if isinstance(pid, int) else "未报告"
    return (
        f"{port.device} | {reported} | VID {vid_text} | PID {pid_text} | "
        f"描述 {description} | SN {serial_number}"
    )


def active_names(mask: int, names: Iterable[str]) -> str:
    """Return a compact current-state list without keeping history."""

    active = [name for index, name in enumerate(names) if mask & (1 << index)]
    return " ".join(active) if active else "无"


def open_touch_serial(
    port: str,
    *,
    serial_factory: Callable[..., Any] = serial.Serial,
    sleeper: Callable[[float], None] = time.sleep,
) -> Any:
    """Open Mai2Touch streaming mode using only RSET then STAT."""

    device = serial_factory(
        port=port,
        baudrate=TOUCH_BAUDRATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0,
        write_timeout=0.5,
    )
    try:
        device.reset_input_buffer()
        device.write(RSET_COMMAND)
        device.flush()
        sleeper(TOUCH_START_DELAY_SECONDS)
        device.write(STAT_COMMAND)
        device.flush()
    except Exception:
        with suppress(Exception):
            device.close()
        raise
    return device


def _version_text(value: Any) -> str:
    if isinstance(value, bytes):
        if value and all(0x20 <= byte <= 0x7E for byte in value):
            value = value.decode("ascii")
        else:
            value = value.hex(" ").upper()
    text = str(value).strip()
    return text or "—"


def _rgb_hex(color: tuple[int, int, int]) -> str:
    return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"


class ControllerTestWindow:
    """One dashboard for current Touch/BTN state, Aime, and Mai2LED tests."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        serial_factory: Callable[..., Any] = serial.Serial,
        port_provider: Callable[[], Iterable[Any]] = list_ports.comports,
        keyboard_provider: Callable[[], list[RawKeyboardDevice]] = (
            list_raw_keyboard_devices
        ),
        bus_description_provider: Callable[[], dict[str, str]] = (
            list_serial_bus_descriptions
        ),
        monitor_factory: Callable[[], RawKeyboardMonitor] = RawKeyboardMonitor,
        aime_controller_factory: Callable[..., Any] = AimeReaderController,
        led_controller_factory: Callable[..., Any] = Mai2LedController,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = root
        self.serial_factory = serial_factory
        self.port_provider = port_provider
        self.keyboard_provider = keyboard_provider
        self.bus_description_provider = bus_description_provider
        self.aime_controller_factory = aime_controller_factory
        self.led_controller_factory = led_controller_factory
        self.sleeper = sleeper
        self.clock = clock

        self.closed = False
        self.touch_serial: Any | None = None
        self.touch_port_name: str | None = None
        self.touch_parser = TouchFrameParser()
        self.last_valid_touch_frame_at: float | None = None
        self.first_touch_frame_deadline: float | None = None
        self.touch_timed_out = False
        self.current_touch_bits = -1

        self.button_state = MainButtonState()
        self.current_button_mask = -1
        self.selected_keyboard_path: str | None = None

        self.aime_connected = False
        self.aime_connecting = False
        self.aime_disconnecting = False
        self.aime_scanning = False
        self.aime_generation = 0
        self.aime_handle: _AimeWorkerHandle | None = None

        self.led_connected = False
        self.led_connecting = False
        self.led_disconnecting = False
        self.led_generation = 0
        self.led_handle: _LedWorkerHandle | None = None
        self.led_sequence_token = 0
        self.led_sequence_after_id: str | None = None
        self.led_sequence_actions: list[tuple[Callable[[int, int], bool], int]] = []

        self.serial_ports_by_label: dict[str, Any] = {}
        self.keyboards_by_label: dict[str, RawKeyboardDevice] = {}
        self.touch_poll_after_id: str | None = None
        self.raw_poll_after_id: str | None = None
        self.hotplug_refresh_after_id: str | None = None
        self.worker_poll_after_id: str | None = None
        self.worker_events: queue.SimpleQueue[tuple[str, int, Any]] = (
            queue.SimpleQueue()
        )

        try:
            self.monitor = monitor_factory()
            self.renderer = ControllerRenderer(
                resource_path("images"),
                display_size=DISPLAY_SIZE,
            )
        except Exception as error:
            try:
                monitor = getattr(self, "monitor", None)
                if monitor is not None:
                    monitor.close()
            finally:
                raise ControllerTestError(str(error)) from error

        self.reported_monitor_error = self.monitor.error

        self.touch_port_var = tk.StringVar()
        self.keyboard_var = tk.StringVar()
        self.led_port_var = tk.StringVar()
        self.aime_port_var = tk.StringVar()
        self.aime_baudrate_var = tk.StringVar(value=str(AIME_BAUDRATES[0]))
        self.connection_status_var = tk.StringVar(value="请选择要测试的设备")
        self.touch_status_var = tk.StringVar(value="未连接")
        self.keyboard_status_var = tk.StringVar(value="未连接")
        self.led_status_var = tk.StringVar(value="未连接")
        self.aime_status_var = tk.StringVar(value="未连接")

        self.touch_state_var = tk.StringVar(value="触摸：无")
        self.button_state_var = tk.StringVar(value="主按键：无")
        self.aime_firmware_var = tk.StringVar(value="—")
        self.aime_hardware_var = tk.StringVar(value="—")
        self.aime_card_state_var = tk.StringVar(value="未连接读卡器")
        self.aime_access_code_var = tk.StringVar(value="—")
        self.aime_block_var = tk.StringVar(value="—")
        self.red_var = tk.StringVar(value="255")
        self.green_var = tk.StringVar(value="0")
        self.blue_var = tk.StringVar(value="0")
        self.image_photo: ImageTk.PhotoImage | None = None
        self.led_blocks: list[tk.Label] = []
        self.led_test_widgets: list[tk.Widget] = []

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_devices(show_error=False)
        self._show_state(0, 0)
        self._set_led_blocks([BLACK] * LOGICAL_LIGHT_COUNT)
        self._update_module_widgets()
        if self.monitor.error:
            self._set_keyboard_status(self.monitor.error, "#C62828")
        self._schedule_raw_poll()
        self._schedule_worker_poll()

    @property
    def touch_connected(self) -> bool:
        return self.touch_serial is not None

    @property
    def hid_connected(self) -> bool:
        return self.selected_keyboard_path is not None

    @property
    def connected(self) -> bool:
        """Compatibility aggregate: at least one module is connected/connecting."""

        return any(
            (
                self.touch_connected,
                self.hid_connected,
                self.aime_connected,
                self.aime_connecting,
                self.led_connected,
                self.led_connecting,
            )
        )

    def _build_ui(self) -> None:
        self.root.title("TenoDX 控制器综合测试")
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.root.minsize(1180, 820)

        outer = ttk.Frame(self.root, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        connection = ttk.LabelFrame(outer, text="设备选择", padding=10)
        connection.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        connection.columnconfigure(1, weight=1)

        self.touch_port_combo, self.touch_connect_button, self.touch_status_label = (
            self._add_device_row(
                connection,
                0,
                "Touch 串口",
                self.touch_port_var,
                self.toggle_touch_connection,
                self.touch_status_var,
            )
        )
        (
            self.keyboard_combo,
            self.keyboard_connect_button,
            self.keyboard_status_label,
        ) = self._add_device_row(
            connection,
            1,
            "HID 键盘",
            self.keyboard_var,
            self.toggle_keyboard_connection,
            self.keyboard_status_var,
        )
        self.led_port_combo, self.led_connect_button, self.led_status_label = (
            self._add_device_row(
                connection,
                2,
                "LED 串口",
                self.led_port_var,
                self.toggle_led_connection,
                self.led_status_var,
            )
        )
        self.aime_port_combo, self.aime_connect_button, self.aime_status_label = (
            self._add_device_row(
                connection,
                3,
                "Aime 串口",
                self.aime_port_var,
                self.toggle_aime_connection,
                self.aime_status_var,
            )
        )
        self.aime_baudrate_combo = ttk.Combobox(
            connection,
            textvariable=self.aime_baudrate_var,
            values=tuple(str(value) for value in AIME_BAUDRATES),
            state="readonly",
            width=9,
        )
        self.aime_baudrate_combo.grid(row=3, column=2, padx=(0, 8), pady=3)

        controls = ttk.Frame(connection)
        controls.grid(row=0, column=5, rowspan=4, padx=(12, 0), sticky="ns")
        self.refresh_button = ttk.Button(
            controls, text="刷新设备", width=13, command=self.refresh_devices
        )
        self.refresh_button.grid(row=0, column=0, pady=(0, 5), sticky="ew")
        self.connect_selected_button = ttk.Button(
            controls, text="连接已选设备", width=13, command=self.connect_selected
        )
        self.connect_selected_button.grid(row=1, column=0, pady=5, sticky="ew")
        self.disconnect_all_button = ttk.Button(
            controls, text="全部断开", width=13, command=self.disconnect_all
        )
        self.disconnect_all_button.grid(row=2, column=0, pady=5, sticky="ew")

        self.connection_status_label = tk.Label(
            connection,
            textvariable=self.connection_status_var,
            anchor="w",
            foreground="#455A64",
            background=self.root.cget("background"),
        )
        self.connection_status_label.grid(
            row=4, column=0, columnspan=6, pady=(7, 0), sticky="ew"
        )

        content = ttk.Frame(outer)
        content.grid(row=1, column=0, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)

        left = ttk.LabelFrame(content, text="Touch / 主按键实时状态", padding=10)
        left.grid(row=0, column=0, padx=(0, 10), sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.image_label = ttk.Label(left, anchor="center")
        self.image_label.grid(row=0, column=0, sticky="nsew")
        ttk.Label(left, textvariable=self.touch_state_var).grid(
            row=1, column=0, pady=(8, 0), sticky="w"
        )
        ttk.Label(left, textvariable=self.button_state_var).grid(
            row=2, column=0, pady=(5, 0), sticky="w"
        )
        ttk.Label(
            left,
            text="仅显示当前触发状态；1P 与 2P 键位均映射到 BTN1–BTN8。",
            foreground="#666666",
        ).grid(row=3, column=0, pady=(8, 0), sticky="w")

        right = ttk.Frame(content)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self._build_aime_panel(right)
        self._build_led_panel(right)

    def _add_device_row(
        self,
        parent: ttk.LabelFrame,
        row: int,
        title: str,
        variable: tk.StringVar,
        command: Callable[[], None],
        status_variable: tk.StringVar,
    ) -> tuple[ttk.Combobox, ttk.Button, tk.Label]:
        ttk.Label(parent, text=title).grid(
            row=row, column=0, padx=(0, 8), pady=3, sticky="e"
        )
        combo = ttk.Combobox(parent, textvariable=variable, state="readonly", width=74)
        combo.grid(row=row, column=1, padx=(0, 8), pady=3, sticky="ew")
        button = ttk.Button(parent, text="连接", width=9, command=command)
        button.grid(row=row, column=3, padx=(0, 8), pady=3)
        status = tk.Label(
            parent,
            textvariable=status_variable,
            anchor="w",
            width=18,
            foreground="#C62828",
            background=self.root.cget("background"),
        )
        status.grid(row=row, column=4, pady=3, sticky="w")
        return combo, button, status

    def _build_aime_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Aime 协议读卡测试", padding=10)
        panel.grid(row=0, column=0, sticky="ew")
        panel.columnconfigure(1, weight=1)

        self.aime_card_state_label = tk.Label(
            panel,
            textvariable=self.aime_card_state_var,
            anchor="center",
            font=("Microsoft YaHei UI", 15, "bold"),
            foreground="#555555",
            background=self.root.cget("background"),
            pady=8,
        )
        self.aime_card_state_label.grid(row=0, column=0, columnspan=2, sticky="ew")
        for row, (title, variable) in enumerate(
            (
                ("固件版本", self.aime_firmware_var),
                ("硬件版本", self.aime_hardware_var),
                ("Aime 协议卡号", self.aime_access_code_var),
                ("Block 2 原始数据", self.aime_block_var),
            ),
            start=1,
        ):
            ttk.Label(panel, text=title).grid(
                row=row, column=0, padx=(0, 10), pady=3, sticky="ne"
            )
            ttk.Label(
                panel,
                textvariable=variable,
                wraplength=400,
                justify="left",
            ).grid(row=row, column=1, pady=3, sticky="w")

        buttons = ttk.Frame(panel)
        buttons.grid(row=5, column=0, columnspan=2, pady=(10, 0))
        self.aime_start_button = ttk.Button(
            buttons, text="开始读卡", command=self.start_aime_scanning
        )
        self.aime_start_button.grid(row=0, column=0, padx=4)
        self.aime_stop_button = ttk.Button(
            buttons, text="停止读卡", command=self.stop_aime_scanning
        )
        self.aime_stop_button.grid(row=0, column=1, padx=4)
        ttk.Button(buttons, text="清除结果", command=self.clear_aime_result).grid(
            row=0, column=2, padx=4
        )
    def _build_led_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Mai2LED 协议测试", padding=10)
        panel.grid(row=1, column=0, pady=(10, 0), sticky="nsew")
        panel.columnconfigure(0, weight=1)

        lights = ttk.Frame(panel)
        lights.grid(row=0, column=0, sticky="ew")
        for index in range(LOGICAL_LIGHT_COUNT):
            item = ttk.Frame(lights)
            item.grid(row=0, column=index, padx=3)
            ttk.Label(item, text=f"BTN{index + 1}").grid(row=0, column=0)
            block = tk.Label(
                item,
                width=3,
                height=2,
                background="#000000",
                relief="sunken",
                borderwidth=2,
            )
            block.grid(row=1, column=0, pady=(4, 0))
            self.led_blocks.append(block)

        color = ttk.LabelFrame(panel, text="测试颜色（0–255）", padding=8)
        color.grid(row=1, column=0, pady=(10, 0), sticky="ew")
        for column, (name, variable) in enumerate(
            (("R", self.red_var), ("G", self.green_var), ("B", self.blue_var))
        ):
            ttk.Label(color, text=name).grid(row=0, column=column * 2, padx=(0, 3))
            entry = ttk.Entry(color, textvariable=variable, width=5, justify="center")
            entry.grid(row=0, column=column * 2 + 1, padx=(0, 8))
            self.led_test_widgets.append(entry)
        self.color_swatch = tk.Label(
            color,
            width=4,
            background="#FF0000",
            relief="sunken",
            borderwidth=2,
        )
        self.color_swatch.grid(row=0, column=6, padx=(2, 8), sticky="ns")
        constant_button = ttk.Button(
            color, text="显示常亮", command=self.show_led_test_color
        )
        constant_button.grid(row=0, column=7)
        self.led_test_widgets.append(constant_button)

        tests = ttk.Frame(panel)
        tests.grid(row=2, column=0, pady=(10, 0))
        for column, (title, command) in enumerate(
            (
                ("RGBW 循环", self.start_rgbw_test),
                ("BTN1–BTN8 逐灯", self.start_chase_test),
                ("目标色淡入淡出", self.start_fade_test),
                ("停止并全灭", self.stop_led_test),
            )
        ):
            button = ttk.Button(tests, text=title, command=command)
            button.grid(row=column // 2, column=column % 2, padx=4, pady=4, sticky="ew")
            self.led_test_widgets.append(button)

    def refresh_devices(self, show_error: bool = True) -> None:
        if self.closed:
            return

        old_ports = self.serial_ports_by_label.copy()
        old_port_names = [
            getattr(old_ports.get(self.touch_port_var.get()), "device", None)
            or self.touch_port_name,
            getattr(old_ports.get(self.led_port_var.get()), "device", None)
            or (self.led_handle.port if self.led_handle is not None else None),
            getattr(old_ports.get(self.aime_port_var.get()), "device", None)
            or (self.aime_handle.port if self.aime_handle is not None else None),
        ]
        old_keyboard = self.keyboards_by_label.get(self.keyboard_var.get())
        old_keyboard_path = getattr(old_keyboard, "path", None)

        errors: list[str] = []
        try:
            bus_descriptions = self.bus_description_provider()
        except Exception as error:
            bus_descriptions = {}
            errors.append(f"总线描述读取失败：{error}")
        try:
            ports = sorted(
                list(self.port_provider()), key=lambda item: item.device.casefold()
            )
        except Exception as error:
            ports = []
            errors.append(f"串口枚举失败：{error}")
        try:
            keyboards = self.keyboard_provider()
        except Exception as error:
            keyboards = []
            errors.append(f"HID 键盘枚举失败：{error}")

        self.serial_ports_by_label.clear()
        labels_by_port: dict[str, str] = {}
        for port in ports:
            label = serial_port_label(
                port, bus_descriptions.get(port.device.casefold())
            )
            self.serial_ports_by_label[label] = port
            labels_by_port[port.device.casefold()] = label
        port_values = ("", *self.serial_ports_by_label)
        for (combo, variable, active), old_name in zip(
            (
                (self.touch_port_combo, self.touch_port_var, self.touch_connected),
                (self.led_port_combo, self.led_port_var, self.led_handle is not None),
                (
                    self.aime_port_combo,
                    self.aime_port_var,
                    self.aime_handle is not None,
                ),
            ),
            old_port_names,
            strict=True,
        ):
            combo.configure(values=port_values)
            replacement = labels_by_port.get(old_name.casefold()) if old_name else ""
            if replacement:
                variable.set(replacement)
            elif not active:
                variable.set("")

        self.keyboards_by_label.clear()
        selected_keyboard_label = ""
        for index, keyboard in enumerate(keyboards, start=1):
            label = keyboard.display_label(index)
            self.keyboards_by_label[label] = keyboard
            if (
                old_keyboard_path
                and keyboard.path.casefold() == old_keyboard_path.casefold()
            ):
                selected_keyboard_label = label
        self.keyboard_combo.configure(values=("", *self.keyboards_by_label))
        if selected_keyboard_label:
            self.keyboard_var.set(selected_keyboard_label)
        elif not self.hid_connected:
            self.keyboard_var.set("")

        self._update_module_widgets()
        if errors:
            self._set_status("；".join(errors), "#C62828")
            if show_error:
                messagebox.showerror("设备刷新失败", "\n".join(errors))
        elif self.monitor.error and not self.hid_connected:
            self._set_keyboard_status(self.monitor.error, "#C62828")
        else:
            self._set_status("设备列表已刷新，请明确选择需要测试的设备", "#455A64")

    def _selected_port(self, variable: tk.StringVar) -> Any | None:
        return self.serial_ports_by_label.get(variable.get())

    def _selected_serial_names(self) -> list[str]:
        names: list[str] = []
        for variable in (self.touch_port_var, self.led_port_var, self.aime_port_var):
            port = self._selected_port(variable)
            name = getattr(port, "device", None)
            if name:
                names.append(name.casefold())
        return names

    def _serial_port_in_use(self, port_name: str, module: str) -> bool:
        active = {
            "touch": self.touch_port_name if self.touch_connected else None,
            "led": self.led_handle.port if self.led_handle is not None else None,
            "aime": self.aime_handle.port if self.aime_handle is not None else None,
        }
        return any(
            name and key != module and name.casefold() == port_name.casefold()
            for key, name in active.items()
        )

    def connect_selected(self) -> None:
        selected_names = self._selected_serial_names()
        if len(selected_names) != len(set(selected_names)):
            messagebox.showwarning(
                "串口选择重复", "Touch、LED 和 Aime 不能选择同一个串口。"
            )
            return

        selected_any = any(
            (
                self._selected_port(self.touch_port_var),
                self.keyboards_by_label.get(self.keyboard_var.get()),
                self._selected_port(self.led_port_var),
                self._selected_port(self.aime_port_var),
            )
        )
        if not selected_any:
            messagebox.showwarning("未选择设备", "请至少明确选择一个要测试的设备。")
            return

        results: list[bool] = []
        if (
            self._selected_port(self.touch_port_var) is not None
            and not self.touch_connected
        ):
            results.append(self.connect_touch())
        if (
            self.keyboards_by_label.get(self.keyboard_var.get()) is not None
            and not self.hid_connected
        ):
            results.append(self.connect_keyboard())
        if (
            self._selected_port(self.led_port_var) is not None
            and self.led_handle is None
        ):
            results.append(self.connect_led())
        if (
            self._selected_port(self.aime_port_var) is not None
            and self.aime_handle is None
        ):
            results.append(self.connect_aime())
        if any(results):
            self._set_status("已开始连接所选设备；各模块互不影响", "#1565C0")

    def connect(self) -> None:
        """Compatibility alias for connecting all explicitly selected devices."""

        self.connect_selected()

    def disconnect_all(self, *, refresh: bool = True) -> None:
        self.disconnect_touch(refresh=False)
        self.disconnect_keyboard(refresh=False)
        self.disconnect_aime()
        self.disconnect_led()
        self._set_status("正在断开全部设备", "#455A64")
        if refresh and not self.closed:
            self.refresh_devices(show_error=False)

    def disconnect(self, *, refresh: bool = True) -> None:
        """Compatibility alias for disconnecting every module."""

        self.disconnect_all(refresh=refresh)

    def toggle_connection(self) -> None:
        if self.connected:
            self.disconnect_all()
        else:
            self.connect_selected()

    def toggle_touch_connection(self) -> None:
        if self.touch_connected:
            self.disconnect_touch()
        else:
            self.connect_touch()

    def connect_touch(self) -> bool:
        selected = self._selected_port(self.touch_port_var)
        if selected is None:
            messagebox.showwarning("未选择 Touch 串口", "请明确选择 Touch 串口。")
            return False
        if self._serial_port_in_use(selected.device, "touch"):
            messagebox.showwarning(
                "串口已占用", f"{selected.device} 已被其他测试模块使用。"
            )
            return False
        try:
            device = open_touch_serial(
                selected.device,
                serial_factory=self.serial_factory,
                sleeper=self.sleeper,
            )
        except (serial.SerialException, OSError, ValueError) as error:
            self._set_touch_status(f"连接失败：{error}", "#C62828")
            messagebox.showerror("Touch 连接失败", str(error))
            return False

        self.touch_serial = device
        self.touch_port_name = selected.device
        self.touch_parser.reset()
        self.last_valid_touch_frame_at = None
        self.first_touch_frame_deadline = (
            self.clock() + FIRST_TOUCH_FRAME_TIMEOUT_SECONDS
        )
        self.touch_timed_out = False
        self._show_state(0, self.current_button_mask)
        self._set_touch_status("等待状态帧", "#1565C0")
        self._update_module_widgets()
        self._schedule_touch_poll()
        return True

    def disconnect_touch(self, *, refresh: bool = True) -> None:
        self._cancel_after("touch_poll_after_id")
        device = self.touch_serial
        self.touch_serial = None
        self.touch_port_name = None
        if device is not None:
            with suppress(serial.SerialException, OSError):
                device.close()
        self.touch_parser.reset()
        self.last_valid_touch_frame_at = None
        self.first_touch_frame_deadline = None
        self.touch_timed_out = False
        self._show_state(0, self.current_button_mask)
        self._set_touch_status("未连接", "#C62828")
        self._update_module_widgets()
        if refresh and not self.closed:
            self.refresh_devices(show_error=False)

    def _schedule_touch_poll(self) -> None:
        self._cancel_after("touch_poll_after_id")
        if self.touch_connected and not self.closed:
            self.touch_poll_after_id = self.root.after(
                TOUCH_POLL_INTERVAL_MS, self._poll_touch_serial
            )

    def _poll_touch_serial(self) -> None:
        self.touch_poll_after_id = None
        device = self.touch_serial
        if device is None or self.closed:
            return
        try:
            waiting = device.in_waiting
            data = device.read(waiting) if waiting else b""
        except (serial.SerialException, OSError) as error:
            self._handle_touch_error(f"Touch 串口通信失败：{error}")
            return

        now = self.clock()
        states = self.touch_parser.feed(data) if data else []
        if states:
            self.last_valid_touch_frame_at = now
            self.first_touch_frame_deadline = None
            self.touch_timed_out = False
            self._show_state(states[-1], self.current_button_mask)
            self._set_touch_status("运行中", "#2E7D32")
        elif self.last_valid_touch_frame_at is None:
            deadline = self.first_touch_frame_deadline
            if deadline is not None and now >= deadline:
                self._handle_touch_error("连接后 1 秒内未收到合法的 Mai2Touch 状态帧。")
                return
        elif now - self.last_valid_touch_frame_at >= TOUCH_TIMEOUT_SECONDS:
            if not self.touch_timed_out:
                self.touch_timed_out = True
                self._show_state(0, self.current_button_mask)
                self._set_touch_status("数据超时，等待恢复", "#EF6C00")
        self._schedule_touch_poll()

    def _handle_touch_error(self, reason: str) -> None:
        self.disconnect_touch(refresh=True)
        self._set_touch_status(reason, "#C62828")
        self._set_status(reason, "#C62828")
        messagebox.showerror("Touch 连接中断", reason)

    def toggle_keyboard_connection(self) -> None:
        if self.hid_connected:
            self.disconnect_keyboard()
        else:
            self.connect_keyboard()

    def connect_keyboard(self) -> bool:
        selected = self.keyboards_by_label.get(self.keyboard_var.get())
        if selected is None:
            messagebox.showwarning(
                "未选择 HID 键盘", "请明确选择控制器实际使用的 HID 键盘设备。"
            )
            return False
        if self.monitor.error:
            messagebox.showerror("HID 键盘监听不可用", self.monitor.error)
            return False
        try:
            paths = {item.path.casefold() for item in self.keyboard_provider()}
        except Exception as error:
            messagebox.showerror("HID 键盘验证失败", str(error))
            return False
        if selected.path.casefold() not in paths:
            messagebox.showerror(
                "HID 键盘已离线", "所选 HID 键盘已不存在，请刷新后重新选择。"
            )
            self.refresh_devices(show_error=False)
            return False
        try:
            self.monitor.set_target(selected.path)
        except OSError as error:
            messagebox.showerror("HID 键盘连接失败", str(error))
            return False
        self.selected_keyboard_path = selected.path
        self.button_state.clear()
        self._show_state(self.current_touch_bits, 0)
        self._set_keyboard_status("监听中", "#2E7D32")
        self._update_module_widgets()
        return True

    def disconnect_keyboard(self, *, refresh: bool = True) -> None:
        self.monitor.clear_target()
        self.selected_keyboard_path = None
        self.button_state.clear()
        self._show_state(self.current_touch_bits, 0)
        self._set_keyboard_status("未连接", "#C62828")
        self._update_module_widgets()
        if refresh and not self.closed:
            self.refresh_devices(show_error=False)

    def _schedule_raw_poll(self) -> None:
        self._cancel_after("raw_poll_after_id")
        if not self.closed:
            self.raw_poll_after_id = self.root.after(
                RAW_INPUT_POLL_INTERVAL_MS, self._poll_raw_input
            )

    def _poll_raw_input(self) -> None:
        self.raw_poll_after_id = None
        if self.closed:
            return
        monitor_error = self.monitor.error
        if monitor_error:
            if monitor_error != self.reported_monitor_error:
                self.reported_monitor_error = monitor_error
                if self.hid_connected:
                    self.disconnect_keyboard(refresh=True)
                    self._set_keyboard_status(monitor_error, "#C62828")
                    self._set_status(monitor_error, "#C62828")
                    messagebox.showerror("HID 键盘监听中断", monitor_error)
                else:
                    self._set_keyboard_status(monitor_error, "#C62828")
            self._schedule_raw_poll()
            return
        if self.reported_monitor_error is not None:
            self.reported_monitor_error = None
            self.refresh_devices(show_error=False)

        device_changed = False
        button_changed = False
        while True:
            try:
                event: RawKeyboardEvent = self.monitor.events.get_nowait()
            except queue.Empty:
                break
            if event.kind == "device-change":
                device_changed = True
                continue
            if not self.hid_connected or event.kind != "key":
                continue
            if not self._event_is_from_selected_keyboard(event):
                continue
            button_changed |= self.button_state.update(
                event.scan_code, event.is_pressed, event.is_extended
            )
        if button_changed:
            self._show_state(self.current_touch_bits, self.button_state.mask)
        if device_changed:
            if self.hid_connected:
                try:
                    target_present = self.monitor.target_is_present()
                except OSError:
                    target_present = False
                if not target_present:
                    reason = "所选 HID 键盘已断开。"
                    self.disconnect_keyboard(refresh=False)
                    self._set_keyboard_status(reason, "#C62828")
                    self._set_status(reason, "#C62828")
                    messagebox.showerror("HID 键盘连接中断", reason)
            self._schedule_hotplug_refresh()
        self._schedule_raw_poll()

    def _event_is_from_selected_keyboard(self, event: RawKeyboardEvent) -> bool:
        return bool(
            self.selected_keyboard_path
            and event.device_path
            and event.device_path.casefold() == self.selected_keyboard_path.casefold()
        )

    def _schedule_hotplug_refresh(self) -> None:
        self._cancel_after("hotplug_refresh_after_id")
        if not self.closed:
            self.hotplug_refresh_after_id = self.root.after(
                HOTPLUG_REFRESH_DELAY_MS, self._refresh_after_hotplug
            )

    def _refresh_after_hotplug(self) -> None:
        self.hotplug_refresh_after_id = None
        self.refresh_devices(show_error=False)

    def toggle_aime_connection(self) -> None:
        if self.aime_handle is not None:
            self.disconnect_aime()
        else:
            self.connect_aime()

    def connect_aime(self) -> bool:
        selected = self._selected_port(self.aime_port_var)
        if selected is None:
            messagebox.showwarning("未选择 Aime 串口", "请明确选择 Aime 串口。")
            return False
        if self._serial_port_in_use(selected.device, "aime"):
            messagebox.showwarning(
                "串口已占用", f"{selected.device} 已被其他测试模块使用。"
            )
            return False
        try:
            baudrate = int(self.aime_baudrate_var.get())
        except ValueError:
            messagebox.showwarning("Aime 波特率无效", "请选择有效的 Aime 波特率。")
            return False
        if baudrate not in AIME_BAUDRATES:
            messagebox.showwarning("Aime 波特率无效", "请选择支持的 Aime 波特率。")
            return False

        self.aime_generation += 1
        handle = _AimeWorkerHandle(self.aime_generation, selected.device, baudrate)
        thread = threading.Thread(
            target=self._aime_worker,
            args=(handle,),
            name="TenoDX-Aime",
            daemon=True,
        )
        handle.thread = thread
        self.aime_handle = handle
        self.aime_connecting = True
        self.aime_disconnecting = False
        self.aime_connected = False
        self.aime_scanning = False
        self._set_aime_status("正在验证协议", "#1565C0")
        self.aime_card_state_var.set("正在连接…")
        self.aime_card_state_label.configure(foreground="#1565C0")
        self._update_module_widgets()
        thread.start()
        return True

    def _aime_worker(self, handle: _AimeWorkerHandle) -> None:
        controller: Any | None = None
        scanning = False
        try:
            controller = self.aime_controller_factory(
                handle.port,
                handle.baudrate,
                serial_factory=self.serial_factory,
                sleeper=self.sleeper,
                clock=self.clock,
            )
            handle.controller = controller
            if handle.stop_event.is_set():
                return
            info = controller.probe()
            if handle.stop_event.is_set():
                return
            controller.start_polling()
            scanning = True
            self.worker_events.put(("aime-connected", handle.generation, info))
            self.worker_events.put(("aime-scanning", handle.generation, True))
            while not handle.stop_event.is_set():
                while True:
                    try:
                        command = handle.commands.get_nowait()
                    except queue.Empty:
                        break
                    if command == "start" and not scanning:
                        controller.start_polling()
                        scanning = True
                        self.worker_events.put(
                            ("aime-scanning", handle.generation, True)
                        )
                    elif command == "stop" and scanning:
                        controller.stop_polling()
                        scanning = False
                        self.worker_events.put(
                            ("aime-scanning", handle.generation, False)
                        )
                if scanning:
                    card = controller.read_card()
                    self.worker_events.put(("aime-card", handle.generation, card))
                handle.stop_event.wait(AIME_SCAN_INTERVAL_SECONDS)
        except Exception as error:
            if not handle.stop_event.is_set():
                self.worker_events.put(("aime-error", handle.generation, error))
        finally:
            if controller is not None:
                if scanning:
                    with suppress(Exception):
                        controller.stop_polling()
                with suppress(Exception):
                    controller.close()
            handle.controller = None
            self.worker_events.put(("aime-stopped", handle.generation, None))

    def start_aime_scanning(self) -> None:
        handle = self.aime_handle
        if handle is not None and self.aime_connected and not self.aime_scanning:
            handle.commands.put("start")
            self._set_aime_status("正在启动读卡", "#1565C0")

    def stop_aime_scanning(self) -> None:
        handle = self.aime_handle
        if handle is not None and self.aime_connected and self.aime_scanning:
            handle.commands.put("stop")
            self._set_aime_status("正在停止读卡", "#455A64")

    def disconnect_aime(self) -> None:
        handle = self.aime_handle
        if handle is None:
            return
        self.aime_disconnecting = True
        self.aime_connecting = False
        self._set_aime_status("正在断开", "#455A64")
        handle.stop_event.set()
        self._update_module_widgets()

    def clear_aime_result(self) -> None:
        self.aime_access_code_var.set("—")
        self.aime_block_var.set("—")
        if self.aime_scanning:
            self.aime_card_state_var.set("等待刷卡…")
            self.aime_card_state_label.configure(foreground="#1565C0")
        elif self.aime_connected:
            self.aime_card_state_var.set("读卡已停止")
            self.aime_card_state_label.configure(foreground="#555555")
        else:
            self.aime_card_state_var.set("未连接读卡器")
            self.aime_card_state_label.configure(foreground="#555555")

    def toggle_led_connection(self) -> None:
        if self.led_handle is not None:
            self.disconnect_led()
        else:
            self.connect_led()

    def connect_led(self) -> bool:
        selected = self._selected_port(self.led_port_var)
        if selected is None:
            messagebox.showwarning("未选择 LED 串口", "请明确选择 LED 串口。")
            return False
        if self._serial_port_in_use(selected.device, "led"):
            messagebox.showwarning(
                "串口已占用", f"{selected.device} 已被其他测试模块使用。"
            )
            return False

        self.led_generation += 1
        handle = _LedWorkerHandle(self.led_generation, selected.device)
        thread = threading.Thread(
            target=self._led_worker,
            args=(handle,),
            name="TenoDX-Mai2LED",
            daemon=True,
        )
        handle.thread = thread
        self.led_handle = handle
        self.led_connecting = True
        self.led_disconnecting = False
        self.led_connected = False
        self._set_led_status("正在验证 15070-04", "#1565C0")
        self._update_module_widgets()
        thread.start()
        return True

    def _led_worker(self, handle: _LedWorkerHandle) -> None:
        controller: Any | None = None
        shutdown_error: Exception | None = None
        try:
            controller = self.led_controller_factory(
                handle.port,
                serial_factory=self.serial_factory,
                sleeper=self.sleeper,
                clock=self.clock,
            )
            handle.controller = controller
            if handle.stop_event.is_set():
                return
            info = controller.probe()
            if handle.stop_event.is_set():
                return
            self.worker_events.put(("led-connected", handle.generation, info))
            while not handle.stop_event.is_set():
                try:
                    command = handle.commands.get(timeout=0.10)
                except queue.Empty:
                    continue
                kind = command.kind
                arguments = command.arguments
                if (
                    command.sequence_token is not None
                    and command.sequence_token != self.led_sequence_token
                ):
                    continue
                if kind == "set-all":
                    controller.set_all(arguments[0])
                elif kind == "chase":
                    controller.set_chase_frame(arguments[0], arguments[1])
                elif kind == "fade":
                    controller.fade_all(arguments[0], arguments[1], arguments[2])
                self.worker_events.put(("led-applied", handle.generation, command))
        except Exception as error:
            if not handle.stop_event.is_set():
                self.worker_events.put(("led-error", handle.generation, error))
        finally:
            if controller is not None:
                try:
                    controller.set_all(BLACK)
                except Exception as error:
                    shutdown_error = shutdown_error or error
                with suppress(Exception):
                    controller.close()
            handle.controller = None
            self.worker_events.put(("led-stopped", handle.generation, shutdown_error))

    def disconnect_led(self) -> None:
        handle = self.led_handle
        if handle is None:
            return
        self._cancel_led_sequence()
        self.led_disconnecting = True
        self.led_connecting = False
        self._set_led_status("正在全灭并断开", "#455A64")
        self._clear_led_command_queue()
        handle.stop_event.set()
        self._set_led_blocks([BLACK] * LOGICAL_LIGHT_COUNT)
        self._update_module_widgets()

    def _queue_led_command(
        self,
        kind: str,
        *arguments: Any,
        sequence_token: int | None = None,
        sequence_index: int | None = None,
    ) -> bool:
        handle = self.led_handle
        if handle is None or not self.led_connected or self.led_disconnecting:
            return False
        handle.commands.put(
            _LedCommand(
                kind,
                tuple(arguments),
                sequence_token=sequence_token,
                sequence_index=sequence_index,
            )
        )
        return True

    def _clear_led_command_queue(self) -> None:
        handle = self.led_handle
        if handle is None:
            return
        while True:
            try:
                handle.commands.get_nowait()
            except queue.Empty:
                return

    def _parse_test_color(self) -> tuple[int, int, int] | None:
        try:
            color = tuple(
                int(variable.get(), 10)
                for variable in (self.red_var, self.green_var, self.blue_var)
            )
        except ValueError:
            messagebox.showwarning("颜色无效", "R、G、B 必须是 0–255 的整数。")
            return None
        if len(color) != 3 or any(value < 0 or value > 255 for value in color):
            messagebox.showwarning("颜色无效", "R、G、B 必须是 0–255 的整数。")
            return None
        result = (color[0], color[1], color[2])
        self.color_swatch.configure(background=_rgb_hex(result))
        return result

    def _set_led_blocks(self, colors: Iterable[tuple[int, int, int]]) -> None:
        values = list(colors)
        if len(values) != LOGICAL_LIGHT_COUNT:
            raise ValueError("必须提供八路逻辑灯颜色")
        for block, color in zip(self.led_blocks, values, strict=True):
            block.configure(background=_rgb_hex(color))

    def _set_all_led_blocks(self, color: tuple[int, int, int]) -> None:
        self._set_led_blocks([color] * LOGICAL_LIGHT_COUNT)

    def _cancel_led_sequence(self) -> None:
        self.led_sequence_token += 1
        self.led_sequence_actions = []
        if self.led_sequence_after_id is not None:
            with suppress(tk.TclError):
                self.root.after_cancel(self.led_sequence_after_id)
            self.led_sequence_after_id = None

    def _start_led_loop(
        self, actions: list[tuple[Callable[[int, int], bool], int]]
    ) -> None:
        if not actions or not self.led_connected:
            return
        self.led_sequence_actions = actions
        token = self.led_sequence_token
        self._run_led_sequence_step(token, 0)

    def _run_led_sequence_step(self, token: int, index: int) -> None:
        if (
            token != self.led_sequence_token
            or not self.led_connected
            or not self.led_sequence_actions
        ):
            return
        action, _delay_ms = self.led_sequence_actions[index]
        action(token, index)

    def _complete_led_sequence_step(self, command: _LedCommand) -> None:
        token = command.sequence_token
        index = command.sequence_index
        if (
            token is None
            or index is None
            or token != self.led_sequence_token
            or not self.led_connected
            or index >= len(self.led_sequence_actions)
        ):
            return
        delay_ms = self.led_sequence_actions[index][1]
        next_index = (index + 1) % len(self.led_sequence_actions)
        self.led_sequence_after_id = self.root.after(
            delay_ms, self._run_led_sequence_step, token, next_index
        )

    def show_led_test_color(self) -> None:
        color = self._parse_test_color()
        if color is None or not self.led_connected:
            return
        self._cancel_led_sequence()
        self._clear_led_command_queue()
        if self._queue_led_command("set-all", BLACK):
            self._queue_led_command("set-all", color)

    def start_rgbw_test(self) -> None:
        if not self.led_connected:
            return
        if not messagebox.askyesno(
            "RGB 全白亮度提示",
            "RGB 全白可能产生较大电流，请确认供电能力足够。是否继续？",
        ):
            return
        self._cancel_led_sequence()
        self._clear_led_command_queue()
        self._queue_led_command("set-all", BLACK)
        colors = (
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 255),
        )

        def action_for(
            color: tuple[int, int, int],
        ) -> Callable[[int, int], bool]:
            def action(token: int, index: int) -> bool:
                return self._queue_led_command(
                    "set-all",
                    color,
                    sequence_token=token,
                    sequence_index=index,
                )

            return action

        self._start_led_loop([(action_for(color), RGBW_HOLD_MS) for color in colors])

    def start_chase_test(self) -> None:
        color = self._parse_test_color()
        if color is None or not self.led_connected:
            return
        self._cancel_led_sequence()
        self._clear_led_command_queue()
        self._queue_led_command("set-all", BLACK)

        def action_for(light_index: int) -> Callable[[int, int], bool]:
            def action(token: int, sequence_index: int) -> bool:
                return self._queue_led_command(
                    "chase",
                    light_index,
                    color,
                    sequence_token=token,
                    sequence_index=sequence_index,
                )

            return action

        self._start_led_loop(
            [(action_for(index), CHASE_HOLD_MS) for index in range(LOGICAL_LIGHT_COUNT)]
        )

    def _animate_led_blocks(
        self,
        token: int,
        start: tuple[int, int, int],
        end: tuple[int, int, int],
    ) -> None:
        for step in range(1, FADE_VISUAL_STEPS + 1):
            amount = step / FADE_VISUAL_STEPS
            color = tuple(
                round(left + ((right - left) * amount))
                for left, right in zip(start, end, strict=True)
            )

            def update(
                value: tuple[int, int, int] = color,
                expected_token: int = token,
            ) -> None:
                if expected_token == self.led_sequence_token and self.led_connected:
                    self._set_all_led_blocks(value)

            self.root.after(round(FADE_DURATION_MS * amount), update)

    def _fade_action(
        self,
        start: tuple[int, int, int],
        end: tuple[int, int, int],
    ) -> Callable[[int, int], bool]:
        def action(token: int, index: int) -> bool:
            return self._queue_led_command(
                "fade",
                start,
                end,
                FADE_DURATION_MS,
                sequence_token=token,
                sequence_index=index,
            )

        return action

    def start_fade_test(self) -> None:
        target = self._parse_test_color()
        if target is None or not self.led_connected:
            return
        self._cancel_led_sequence()
        self._clear_led_command_queue()
        self._queue_led_command("set-all", BLACK)
        self._start_led_loop(
            [
                (
                    lambda token, index: self._queue_led_command(
                        "set-all",
                        target,
                        sequence_token=token,
                        sequence_index=index,
                    ),
                    FADE_HOLD_MS,
                ),
                (self._fade_action(target, BLACK), FADE_DURATION_MS),
                (self._fade_action(BLACK, target), FADE_DURATION_MS),
            ]
        )

    def stop_led_test(self) -> None:
        self._cancel_led_sequence()
        self._clear_led_command_queue()
        if self._queue_led_command("set-all", BLACK):
            self._set_led_status("测试已停止，已请求全灭", "#2E7D32")

    def _schedule_worker_poll(self) -> None:
        self._cancel_after("worker_poll_after_id")
        if not self.closed:
            self.worker_poll_after_id = self.root.after(
                WORKER_EVENT_POLL_INTERVAL_MS, self._poll_worker_events
            )

    def _poll_worker_events(self) -> None:
        self.worker_poll_after_id = None
        if self.closed:
            return
        while True:
            try:
                kind, generation, payload = self.worker_events.get_nowait()
            except queue.Empty:
                break
            if kind.startswith("aime-"):
                self._apply_aime_event(kind, generation, payload)
            elif kind.startswith("led-"):
                self._apply_led_event(kind, generation, payload)
        self._schedule_worker_poll()

    def _apply_aime_event(self, kind: str, generation: int, payload: Any) -> None:
        if generation != self.aime_generation:
            return
        if kind == "aime-connected":
            self.aime_connecting = False
            self.aime_connected = True
            if hasattr(payload, "firmware") and hasattr(payload, "hardware"):
                firmware = payload.firmware
                hardware = payload.hardware
            else:
                firmware, hardware = payload
            self.aime_firmware_var.set(_version_text(firmware))
            self.aime_hardware_var.set(_version_text(hardware))
            self._set_aime_status("协议已连接", "#2E7D32")
        elif kind == "aime-scanning":
            self.aime_scanning = bool(payload)
            if self.aime_scanning:
                self.aime_card_state_var.set("等待刷卡…")
                self.aime_card_state_label.configure(foreground="#1565C0")
                self._set_aime_status("读卡中", "#2E7D32")
            else:
                self.aime_card_state_var.set("读卡已停止")
                self.aime_card_state_label.configure(foreground="#555555")
                self._set_aime_status("协议已连接", "#2E7D32")
        elif kind == "aime-card":
            present = bool(getattr(payload, "present", False))
            if present:
                access_code = getattr(payload, "access_code", None)
                raw_block = getattr(payload, "raw_block", None)
                self.aime_card_state_var.set("已检测到卡")
                self.aime_card_state_label.configure(foreground="#2E7D32")
                self.aime_access_code_var.set(access_code or "卡号无法解析")
                if isinstance(raw_block, (bytes, bytearray)):
                    self.aime_block_var.set(bytes(raw_block).hex(" ").upper())
            else:
                self.aime_card_state_var.set("等待刷卡…")
                self.aime_card_state_label.configure(foreground="#1565C0")
        elif kind == "aime-error":
            reason = f"Aime 协议通信失败：{payload}"
            self.aime_connected = False
            self.aime_connecting = False
            self.aime_scanning = False
            self._set_aime_status(reason, "#C62828")
            self._set_status(reason, "#C62828")
            messagebox.showerror("Aime 连接中断", reason)
        elif kind == "aime-stopped":
            self.aime_handle = None
            self.aime_connected = False
            self.aime_connecting = False
            self.aime_disconnecting = False
            self.aime_scanning = False
            if not self.aime_status_var.get().startswith("Aime 协议通信失败"):
                self._set_aime_status("未连接", "#C62828")
            self.aime_card_state_var.set("未连接读卡器")
            self.aime_card_state_label.configure(foreground="#555555")
            self.refresh_devices(show_error=False)
        self._update_module_widgets()

    def _apply_led_event(self, kind: str, generation: int, payload: Any) -> None:
        if generation != self.led_generation:
            return
        if kind == "led-connected":
            self.led_connecting = False
            self.led_connected = True
            board = getattr(payload, "board_number", "15070-04")
            revision = getattr(payload, "firmware_revision", "")
            revision_text = (
                f"0x{revision:02X}" if isinstance(revision, int) else str(revision)
            )
            details = f"{board} {revision_text}".strip()
            self._set_led_status(f"已连接 {details}", "#2E7D32")
        elif kind == "led-applied":
            command: _LedCommand = payload
            if (
                command.sequence_token is None
                or command.sequence_token == self.led_sequence_token
            ):
                if command.kind == "set-all":
                    self._set_all_led_blocks(command.arguments[0])
                elif command.kind == "chase":
                    colors = [BLACK] * LOGICAL_LIGHT_COUNT
                    colors[command.arguments[0]] = command.arguments[1]
                    self._set_led_blocks(colors)
                elif command.kind == "fade":
                    start, end, _duration = command.arguments
                    self._set_all_led_blocks(start)
                    if command.sequence_token is not None:
                        self._animate_led_blocks(command.sequence_token, start, end)
            self._complete_led_sequence_step(command)
            self._set_led_status("协议命令已确认", "#2E7D32")
        elif kind == "led-error":
            self._cancel_led_sequence()
            reason = f"Mai2LED 协议通信失败：{payload}"
            self.led_connected = False
            self.led_connecting = False
            self._set_led_blocks([BLACK] * LOGICAL_LIGHT_COUNT)
            self._set_led_status(reason, "#C62828")
            self._set_status(reason, "#C62828")
            messagebox.showerror("LED 连接中断", reason)
        elif kind == "led-stopped":
            self.led_handle = None
            self.led_connected = False
            self.led_connecting = False
            self.led_disconnecting = False
            self._cancel_led_sequence()
            self._set_led_blocks([BLACK] * LOGICAL_LIGHT_COUNT)
            if payload is not None:
                self._set_led_status(f"已断开；全灭失败：{payload}", "#EF6C00")
            elif not self.led_status_var.get().startswith("Mai2LED 协议通信失败"):
                self._set_led_status("未连接", "#C62828")
            self.refresh_devices(show_error=False)
        self._update_module_widgets()

    def _show_state(self, touch_bits: int, button_mask: int) -> None:
        touch_bits &= VALID_TOUCH_MASK
        button_mask &= 0xFF
        if (
            touch_bits == self.current_touch_bits
            and button_mask == self.current_button_mask
        ):
            return
        self.current_touch_bits = touch_bits
        self.current_button_mask = button_mask
        image = self.renderer.render(touch_bits, button_mask)
        self.image_photo = ImageTk.PhotoImage(image)
        self.image_label.configure(image=self.image_photo)
        self.touch_state_var.set(f"触摸：{active_names(touch_bits, ZONE_NAMES)}")
        button_names = tuple(f"BTN{index}" for index in range(1, 9))
        self.button_state_var.set(f"主按键：{active_names(button_mask, button_names)}")

    def _update_module_widgets(self) -> None:
        self.touch_port_combo.configure(
            state="disabled" if self.touch_connected else "readonly"
        )
        self.touch_connect_button.configure(
            text="断开" if self.touch_connected else "连接"
        )
        self.keyboard_combo.configure(
            state="disabled" if self.hid_connected else "readonly"
        )
        self.keyboard_connect_button.configure(
            text="断开" if self.hid_connected else "连接"
        )

        led_busy = self.led_handle is not None
        self.led_port_combo.configure(state="disabled" if led_busy else "readonly")
        self.led_connect_button.configure(
            text=(
                "连接中…"
                if self.led_connecting
                else "断开中…"
                if self.led_disconnecting
                else "断开"
                if self.led_connected
                else "连接"
            ),
            state="disabled" if self.led_disconnecting else "normal",
        )
        for widget in self.led_test_widgets:
            widget.configure(
                state=(
                    "normal"
                    if self.led_connected and not self.led_disconnecting
                    else "disabled"
                )
            )

        aime_busy = self.aime_handle is not None
        self.aime_port_combo.configure(state="disabled" if aime_busy else "readonly")
        self.aime_baudrate_combo.configure(
            state="disabled" if aime_busy else "readonly"
        )
        self.aime_connect_button.configure(
            text=(
                "连接中…"
                if self.aime_connecting
                else "断开中…"
                if self.aime_disconnecting
                else "断开"
                if self.aime_connected
                else "连接"
            ),
            state="disabled" if self.aime_disconnecting else "normal",
        )
        self.aime_start_button.configure(
            state="normal"
            if (
                self.aime_connected
                and not self.aime_scanning
                and not self.aime_disconnecting
            )
            else "disabled"
        )
        self.aime_stop_button.configure(
            state=(
                "normal"
                if (
                    self.aime_connected
                    and self.aime_scanning
                    and not self.aime_disconnecting
                )
                else "disabled"
            )
        )
        self.disconnect_all_button.configure(
            state="normal" if self.connected else "disabled"
        )

    def _set_status(self, text: str, color: str) -> None:
        self.connection_status_var.set(text)
        self.connection_status_label.configure(foreground=color)

    def _set_touch_status(self, text: str, color: str) -> None:
        self.touch_status_var.set(text)
        self.touch_status_label.configure(foreground=color)

    def _set_keyboard_status(self, text: str, color: str) -> None:
        self.keyboard_status_var.set(text)
        self.keyboard_status_label.configure(foreground=color)

    def _set_aime_status(self, text: str, color: str) -> None:
        self.aime_status_var.set(text)
        self.aime_status_label.configure(foreground=color)

    def _set_led_status(self, text: str, color: str) -> None:
        self.led_status_var.set(text)
        self.led_status_label.configure(foreground=color)

    def _cancel_after(self, attribute: str) -> None:
        after_id = getattr(self, attribute)
        if after_id is None:
            return
        with suppress(tk.TclError):
            self.root.after_cancel(after_id)
        setattr(self, attribute, None)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for attribute in (
            "touch_poll_after_id",
            "raw_poll_after_id",
            "hotplug_refresh_after_id",
            "worker_poll_after_id",
        ):
            self._cancel_after(attribute)
        self._cancel_led_sequence()
        self.disconnect_touch(refresh=False)
        self.disconnect_keyboard(refresh=False)

        aime_handle = self.aime_handle
        if aime_handle is not None:
            self.disconnect_aime()
            thread = aime_handle.thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=5.2)
        led_handle = self.led_handle
        if led_handle is not None:
            self.disconnect_led()
            thread = led_handle.thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=3.2)
            if thread is not None and thread.is_alive():
                led_handle.stop_event.set()
                thread.join(timeout=2.7)

        self.monitor.close()
        with suppress(tk.TclError):
            self.root.destroy()


def launch_controller_test() -> int:
    """Create the combined test window and run it until the user closes it."""

    if sys.platform != "win32":
        raise ControllerTestError("控制器综合测试界面仅支持 Windows。")
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise ControllerTestError(f"无法创建图形界面：{error}") from error
    try:
        ControllerTestWindow(root)
    except Exception:
        with suppress(tk.TclError):
            root.destroy()
        raise
    root.mainloop()
    return 0
