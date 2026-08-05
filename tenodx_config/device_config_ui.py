"""Simple graphical editor for the controller's persistent Magic settings."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from tkinter import messagebox, ttk
from typing import Any, Literal

from serial.tools import list_ports

from .device_config import (
    HID_KEY_CHOICES,
    LAYOUT_1P,
    LAYOUT_2P,
    TOUCH_ZONE_NAMES,
    DeviceConfigController,
    DeviceConfigSnapshot,
    KeyboardConfig,
    LedConfig,
    TouchConfig,
    TouchMapEntry,
    hid_key_name,
    main_keycodes_for_layout,
)
from .raw_keyboard import list_serial_bus_descriptions

WORKER_EVENT_POLL_INTERVAL_MS = 30
LOGICAL_LED_COUNT = 8

ConfigPage = Literal["touch", "led", "keyboard"]


class DeviceConfigUiError(RuntimeError):
    """Raised when the configuration window cannot be started."""


@dataclass(frozen=True, slots=True)
class _WorkerCommand:
    kind: Literal["read", "apply", "disconnect"]
    page: ConfigPage | None = None
    value: Any = None
    save: bool = False


@dataclass(slots=True)
class _WorkerHandle:
    generation: int
    port: str
    commands: queue.Queue[_WorkerCommand] = field(default_factory=queue.Queue)
    controller: Any | None = None
    thread: threading.Thread | None = None


def serial_port_label(port: Any, bus_description: str | None) -> str:
    """Return a manually selectable label with explicit USB identity fields."""

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


class DeviceConfigWindow:
    """Edit Touch, LED, and keyboard settings through one Magic serial port."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        controller_factory: Callable[..., Any] = DeviceConfigController,
        port_provider: Callable[[], Iterable[Any]] = list_ports.comports,
        bus_description_provider: Callable[[], dict[str, str]] = (
            list_serial_bus_descriptions
        ),
    ) -> None:
        self.root = root
        self.controller_factory = controller_factory
        self.port_provider = port_provider
        self.bus_description_provider = bus_description_provider

        self.closed = False
        self.connecting = False
        self.connected = False
        self.disconnecting = False
        self.operation_pending = False
        self.generation = 0
        self.handle: _WorkerHandle | None = None
        self.worker_events: queue.SimpleQueue[tuple[str, int, Any]] = (
            queue.SimpleQueue()
        )
        self.worker_poll_after_id: str | None = None

        self.serial_ports_by_label: dict[str, Any] = {}
        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请选择 Aime / Magic 串口")

        self.snapshot: DeviceConfigSnapshot | None = None
        self.touch_device_config: TouchConfig | None = None
        self.touch_draft: list[TouchMapEntry] = []
        self.led_device_config: LedConfig | None = None
        self.keyboard_device_config: KeyboardConfig | None = None
        self.loading_controls = False

        self.touch_dirty_var = tk.StringVar(value="尚未读取")
        self.led_dirty_var = tk.StringVar(value="尚未读取")
        self.keyboard_dirty_var = tk.StringVar(value="尚未读取")
        self.touch_page_status_var = tk.StringVar(value="—")
        self.led_page_status_var = tk.StringVar(value="—")
        self.keyboard_page_status_var = tk.StringVar(value="—")
        self.selected_touch_channel_var = tk.StringVar(value="物理通道：—")
        self.touch_block_var = tk.StringVar(value="A")
        self.touch_zone_var = tk.StringVar(value=TOUCH_ZONE_NAMES[0])
        self.led_per_bit_var = tk.StringVar(value="1")
        self.led_physical_count_var = tk.StringVar(value="物理灯珠总数：8")
        self.led_rainbow_var = tk.BooleanVar(value=False)
        self.keyboard_layout_var = tk.StringVar(value="1P")
        self.keyboard_ek_vars = [tk.StringVar() for _ in range(4)]
        self.keyboard_unknown_names: list[str | None] = [None] * 4
        self.keyboard_unknown_codes: list[int | None] = [None] * 4
        self.keyboard_btn_preview_vars = [tk.StringVar() for _ in range(8)]
        self.keyboard_ek_combos: list[ttk.Combobox] = []
        self.hid_name_to_code = dict(HID_KEY_CHOICES)
        self.hid_code_to_name = {
            keycode: display_name for display_name, keycode in HID_KEY_CHOICES
        }

        self.input_widgets: list[tuple[tk.Widget, str]] = []
        self.page_buttons: dict[ConfigPage, list[ttk.Button]] = {
            "touch": [],
            "led": [],
            "keyboard": [],
        }

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_devices(show_error=False)
        self._update_keyboard_preview()
        self._update_widgets()
        self._schedule_worker_poll()

    def _build_ui(self) -> None:
        self.root.title("TenoDX 配置工具")
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.root.minsize(1040, 720)

        outer = ttk.Frame(self.root, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        connection = ttk.LabelFrame(outer, text="配置连接", padding=10)
        connection.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        connection.columnconfigure(1, weight=1)
        ttk.Label(connection, text="Aime / Magic 串口").grid(
            row=0, column=0, padx=(0, 8), sticky="e"
        )
        self.port_combo = ttk.Combobox(
            connection,
            textvariable=self.port_var,
            state="readonly",
            width=80,
        )
        self.port_combo.grid(row=0, column=1, padx=(0, 8), sticky="ew")
        self.refresh_button = ttk.Button(
            connection, text="刷新设备", command=self.refresh_devices
        )
        self.refresh_button.grid(row=0, column=2, padx=(0, 8))
        self.connect_button = ttk.Button(
            connection, text="连接", width=10, command=self.toggle_connection
        )
        self.connect_button.grid(row=0, column=3)

        warning = tk.Label(
            connection,
            text="配置与 Aime 协议共用同一串口；连接前请先断开综合测试中的 Aime。",
            anchor="w",
            foreground="#AD5700",
            background=self.root.cget("background"),
        )
        warning.grid(row=1, column=0, columnspan=4, pady=(8, 2), sticky="ew")
        self.status_label = tk.Label(
            connection,
            textvariable=self.status_var,
            anchor="w",
            foreground="#455A64",
            background=self.root.cget("background"),
        )
        self.status_label.grid(row=2, column=0, columnspan=4, sticky="ew")

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.touch_page = ttk.Frame(self.notebook, padding=10)
        self.led_page = ttk.Frame(self.notebook, padding=10)
        self.keyboard_page = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.touch_page, text="Touch 区块映射")
        self.notebook.add(self.led_page, text="LED 配置")
        self.notebook.add(self.keyboard_page, text="按键配置")
        self._build_touch_page()
        self._build_led_page()
        self._build_keyboard_page()

    def _build_touch_page(self) -> None:
        self.touch_page.rowconfigure(0, weight=1)
        self.touch_page.columnconfigure(0, weight=3)
        self.touch_page.columnconfigure(1, weight=2)

        table_frame = ttk.LabelFrame(self.touch_page, text="物理通道 0–33", padding=8)
        table_frame.grid(row=0, column=0, padx=(0, 10), sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.touch_tree = ttk.Treeview(
            table_frame,
            columns=("zone", "block"),
            show="tree headings",
            selectmode="browse",
            height=20,
        )
        self.touch_tree.heading("#0", text="通道")
        self.touch_tree.heading("zone", text="触发区块")
        self.touch_tree.heading("block", text="扫描 Block")
        self.touch_tree.column("#0", width=70, minwidth=60, stretch=False)
        self.touch_tree.column("zone", width=330, minwidth=160)
        self.touch_tree.column("block", width=95, minwidth=80, stretch=False)
        scroll = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.touch_tree.yview
        )
        self.touch_tree.configure(yscrollcommand=scroll.set)
        self.touch_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.touch_tree.bind("<<TreeviewSelect>>", self._on_touch_selection)
        self.touch_tree.tag_configure("changed", background="#FFF3CD")
        self.input_widgets.append((self.touch_tree, "normal"))

        editor = ttk.LabelFrame(self.touch_page, text="所选通道", padding=10)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        ttk.Label(
            editor,
            textvariable=self.selected_touch_channel_var,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            editor,
            text="每个物理通道必须选择一个区域；同一区域可由多个通道复用。",
            foreground="#546E7A",
        ).grid(row=1, column=0, pady=(3, 8), sticky="w")

        zones = ttk.Frame(editor)
        zones.grid(row=2, column=0, sticky="nsew")
        groups = (
            ("A", TOUCH_ZONE_NAMES[0:8]),
            ("B", TOUCH_ZONE_NAMES[8:16]),
            ("C", TOUCH_ZONE_NAMES[16:18]),
            ("D", TOUCH_ZONE_NAMES[18:26]),
            ("E", TOUCH_ZONE_NAMES[26:34]),
        )
        for column, (group_name, group_names) in enumerate(groups):
            group = ttk.LabelFrame(zones, text=group_name, padding=5)
            group.grid(row=0, column=column, padx=3, sticky="ns")
            for row, name in enumerate(group_names):
                radio = ttk.Radiobutton(
                    group,
                    text=name,
                    variable=self.touch_zone_var,
                    value=name,
                    command=self._commit_touch_editor,
                )
                radio.grid(row=row, column=0, sticky="w")
                self.input_widgets.append((radio, "normal"))

        block_row = ttk.Frame(editor)
        block_row.grid(row=3, column=0, pady=(9, 0), sticky="w")
        ttk.Label(block_row, text="扫描 Block").grid(row=0, column=0, padx=(0, 8))
        ttk.Label(
            block_row,
            textvariable=self.touch_block_var,
            width=6,
            anchor="center",
        ).grid(row=0, column=1)

        self._build_page_actions(
            self.touch_page,
            row=1,
            page="touch",
            dirty_var=self.touch_dirty_var,
            status_var=self.touch_page_status_var,
        )

    def _build_led_page(self) -> None:
        self.led_page.columnconfigure(0, weight=1)
        settings = ttk.LabelFrame(self.led_page, text="Mai2LED", padding=16)
        settings.grid(row=0, column=0, sticky="new")
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="每个逻辑灯的物理灯珠数").grid(
            row=0, column=0, padx=(0, 12), pady=6, sticky="e"
        )
        self.led_per_bit_combo = ttk.Combobox(
            settings,
            textvariable=self.led_per_bit_var,
            values=("1", "2", "3", "4"),
            state="readonly",
            width=8,
        )
        self.led_per_bit_combo.grid(row=0, column=1, pady=6, sticky="w")
        self.led_per_bit_combo.bind("<<ComboboxSelected>>", self._on_led_changed)
        self.input_widgets.append((self.led_per_bit_combo, "readonly"))

        ttk.Label(settings, textvariable=self.led_physical_count_var).grid(
            row=1, column=1, pady=(0, 10), sticky="w"
        )
        self.led_rainbow_check = ttk.Checkbutton(
            settings,
            text="启用空闲彩虹灯效",
            variable=self.led_rainbow_var,
            command=self._on_led_changed,
        )
        self.led_rainbow_check.grid(row=2, column=1, pady=6, sticky="w")
        self.input_widgets.append((self.led_rainbow_check, "normal"))

        ttk.Label(
            settings,
            text="总灯珠数 = 8 个逻辑灯 × 每个逻辑灯的灯珠数。",
            foreground="#546E7A",
        ).grid(row=3, column=0, columnspan=2, pady=(12, 0), sticky="w")

        self._build_page_actions(
            self.led_page,
            row=1,
            page="led",
            dirty_var=self.led_dirty_var,
            status_var=self.led_page_status_var,
        )

    def _build_keyboard_page(self) -> None:
        self.keyboard_page.columnconfigure(0, weight=1)
        settings = ttk.LabelFrame(self.keyboard_page, text="键盘 HID 配置", padding=16)
        settings.grid(row=0, column=0, sticky="new")
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="主按键布局").grid(
            row=0, column=0, padx=(0, 12), pady=6, sticky="e"
        )
        layout_row = ttk.Frame(settings)
        layout_row.grid(row=0, column=1, pady=6, sticky="w")
        for column, layout_name in enumerate(("1P", "2P")):
            button = ttk.Radiobutton(
                layout_row,
                text=layout_name,
                value=layout_name,
                variable=self.keyboard_layout_var,
                command=self._on_keyboard_changed,
            )
            button.grid(row=0, column=column, padx=(0, 12))
            self.input_widgets.append((button, "normal"))

        preview = ttk.LabelFrame(settings, text="BTN1–BTN8（只读预览）", padding=8)
        preview.grid(row=1, column=0, columnspan=2, pady=(8, 14), sticky="ew")
        for index, variable in enumerate(self.keyboard_btn_preview_vars):
            ttk.Label(preview, textvariable=variable, width=18).grid(
                row=index // 4, column=index % 4, padx=4, pady=3, sticky="w"
            )

        ttk.Separator(settings).grid(
            row=2, column=0, columnspan=2, pady=(0, 10), sticky="ew"
        )
        ttk.Label(
            settings,
            text="EK_1–EK_4",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(
            settings,
            text="仅提供常用、安全的 HID 键值；不接受任意原始字节。",
            foreground="#546E7A",
        ).grid(row=4, column=0, columnspan=2, pady=(2, 8), sticky="w")
        for index, variable in enumerate(self.keyboard_ek_vars):
            ttk.Label(settings, text=f"EK_{index + 1}").grid(
                row=5 + index, column=0, padx=(0, 12), pady=4, sticky="e"
            )
            combo = ttk.Combobox(
                settings,
                textvariable=variable,
                values=tuple(self.hid_name_to_code),
                state="readonly",
                width=28,
            )
            combo.grid(row=5 + index, column=1, pady=4, sticky="w")
            combo.bind("<<ComboboxSelected>>", self._on_keyboard_changed)
            self.keyboard_ek_combos.append(combo)
            self.input_widgets.append((combo, "readonly"))

        self._build_page_actions(
            self.keyboard_page,
            row=1,
            page="keyboard",
            dirty_var=self.keyboard_dirty_var,
            status_var=self.keyboard_page_status_var,
        )

    def _build_page_actions(
        self,
        parent: ttk.Frame,
        *,
        row: int,
        page: ConfigPage,
        dirty_var: tk.StringVar,
        status_var: tk.StringVar,
    ) -> None:
        actions = ttk.Frame(parent)
        actions.grid(row=row, column=0, columnspan=2, pady=(12, 0), sticky="ew")
        actions.columnconfigure(4, weight=1)
        read_button = ttk.Button(
            actions,
            text="重新读取",
            command=lambda selected=page: self.read_page(selected),
        )
        read_button.grid(row=0, column=0, padx=(0, 6))
        apply_button = ttk.Button(
            actions,
            text="临时应用到 RAM",
            command=lambda selected=page: self.apply_page(selected, save=False),
        )
        apply_button.grid(row=0, column=1, padx=6)
        save_button = ttk.Button(
            actions,
            text="应用并保存到 Flash",
            command=lambda selected=page: self.apply_page(selected, save=True),
        )
        save_button.grid(row=0, column=2, padx=6)
        ttk.Label(actions, textvariable=dirty_var).grid(
            row=0, column=3, padx=(12, 8), sticky="w"
        )
        ttk.Label(actions, textvariable=status_var, foreground="#455A64").grid(
            row=0, column=4, sticky="e"
        )
        self.page_buttons[page].extend((read_button, apply_button, save_button))

    def refresh_devices(self, show_error: bool = True) -> None:
        """Refresh serial labels while retaining a still-present manual choice."""

        if self.connected or self.connecting or self.disconnecting:
            return
        selected = self.serial_ports_by_label.get(self.port_var.get())
        selected_name = getattr(selected, "device", "").casefold()
        try:
            ports = list(self.port_provider())
            descriptions = {
                key.casefold(): value
                for key, value in self.bus_description_provider().items()
            }
        except Exception as error:
            if show_error:
                messagebox.showerror("刷新设备失败", str(error), parent=self.root)
            self._set_status(f"刷新设备失败：{error}", "#C62828")
            return

        self.serial_ports_by_label.clear()
        retained_label = ""
        for port in ports:
            device = str(getattr(port, "device", ""))
            if not device:
                continue
            label = serial_port_label(port, descriptions.get(device.casefold()))
            self.serial_ports_by_label[label] = port
            if selected_name and device.casefold() == selected_name:
                retained_label = label
        labels = tuple(self.serial_ports_by_label)
        self.port_combo.configure(values=labels)
        # Never select a device solely because it is the only one present.
        self.port_var.set(retained_label)
        self._set_status(
            f"已发现 {len(labels)} 个串口，请手动选择 Aime / Magic 端口",
            "#455A64",
        )

    def toggle_connection(self) -> None:
        if self.connected or self.connecting:
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> bool:
        """Start the sole serial worker and read the complete configuration."""

        if self.connected or self.connecting or self.disconnecting:
            return False
        port = self.serial_ports_by_label.get(self.port_var.get())
        if port is None:
            messagebox.showwarning(
                "未选择设备",
                "请手动选择 Aime / Magic 串口。",
                parent=self.root,
            )
            return False

        self.generation += 1
        handle = _WorkerHandle(
            generation=self.generation,
            port=str(port.device),
        )
        self.handle = handle
        self.connecting = True
        self.disconnecting = False
        self._set_status("正在连接并读取配置（请确保 Aime 测试已断开）…", "#1565C0")
        thread = threading.Thread(
            target=self._worker_main,
            args=(handle,),
            name="TenoDX-config-worker",
            daemon=True,
        )
        handle.thread = thread
        thread.start()
        self._update_widgets()
        return True

    def disconnect(self) -> None:
        handle = self.handle
        if handle is None or self.disconnecting:
            return
        self.disconnecting = True
        self.operation_pending = False
        self._set_status("正在断开配置串口…", "#1565C0")
        self._discard_worker_commands(handle)
        handle.commands.put(_WorkerCommand("disconnect"))
        self._update_widgets()

    @staticmethod
    def _discard_worker_commands(handle: _WorkerHandle) -> None:
        while True:
            try:
                handle.commands.get_nowait()
            except queue.Empty:
                return

    def _worker_main(self, handle: _WorkerHandle) -> None:
        controller: Any | None = None
        try:
            controller = self.controller_factory(handle.port)
            handle.controller = controller
            controller.probe()
            snapshot = controller.read_snapshot()
            self.worker_events.put(("connected", handle.generation, snapshot))
            while True:
                command = handle.commands.get()
                if command.kind == "disconnect":
                    break
                if command.kind == "read":
                    value = self._worker_read_page(controller, command.page)
                    self.worker_events.put(
                        (
                            "read-complete",
                            handle.generation,
                            (command.page, value),
                        )
                    )
                    continue
                if command.kind == "apply":
                    self._worker_apply_page(
                        controller,
                        command.page,
                        command.value,
                        command.save,
                    )
                    self.worker_events.put(
                        (
                            "apply-complete",
                            handle.generation,
                            (command.page, command.value, command.save),
                        )
                    )
        except Exception as error:
            self.worker_events.put(("error", handle.generation, str(error)))
        finally:
            if controller is not None:
                with suppress(Exception):
                    controller.close()
            self.worker_events.put(("disconnected", handle.generation, None))

    @staticmethod
    def _worker_read_page(controller: Any, page: ConfigPage | None) -> Any:
        if page == "touch":
            return controller.read_touch()
        if page == "led":
            return controller.read_led()
        if page == "keyboard":
            return controller.read_keyboard()
        return controller.read_snapshot()

    @staticmethod
    def _worker_apply_page(
        controller: Any,
        page: ConfigPage | None,
        value: Any,
        save: bool,
    ) -> None:
        if page == "touch":
            controller.apply_touch(value)
            if save:
                controller.save_touch()
            return
        if page == "led":
            controller.apply_led(value)
            if save:
                controller.save_led()
            return
        if page == "keyboard":
            controller.apply_keyboard(value)
            if save:
                controller.save_keyboard()
            return
        raise ValueError("未知配置页")

    def read_page(self, page: ConfigPage) -> None:
        if not self._can_start_operation():
            return
        if self._page_is_dirty(page) and not messagebox.askyesno(
            "放弃本地修改",
            "重新读取会放弃本页尚未应用的修改，是否继续？",
            parent=self.root,
        ):
            return
        handle = self.handle
        if handle is None:
            return
        self.operation_pending = True
        self._set_page_status(page, "正在重新读取…")
        handle.commands.put(_WorkerCommand("read", page=page))
        self._update_widgets()

    def apply_page(self, page: ConfigPage, *, save: bool) -> None:
        if not self._can_start_operation():
            return
        handle = self.handle
        if handle is None:
            return
        try:
            value = self._page_apply_value(page)
        except (TypeError, ValueError) as error:
            messagebox.showerror("配置无效", str(error), parent=self.root)
            return
        self.operation_pending = True
        operation = "应用并保存" if save else "临时应用"
        self._set_page_status(page, f"正在{operation}…")
        handle.commands.put(_WorkerCommand("apply", page=page, value=value, save=save))
        self._update_widgets()

    def _can_start_operation(self) -> bool:
        if not self.connected or self.handle is None:
            messagebox.showwarning(
                "尚未连接", "请先连接 Aime / Magic 串口。", parent=self.root
            )
            return False
        return not self.operation_pending and not self.disconnecting

    def _page_apply_value(self, page: ConfigPage) -> Any:
        if page == "touch":
            if self.touch_device_config is None:
                raise ValueError("尚未读取 Touch 配置")
            return {
                index: entry
                for index, entry in enumerate(self.touch_draft)
                if entry != self.touch_device_config.entries[index]
            }
        if page == "led":
            return self._current_led_config()
        if page == "keyboard":
            return self._current_keyboard_config()
        raise ValueError("未知配置页")

    def _schedule_worker_poll(self) -> None:
        if self.closed:
            return
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
            if generation != self.generation:
                continue
            self._apply_worker_event(kind, payload)
        self._schedule_worker_poll()

    def _apply_worker_event(self, kind: str, payload: Any) -> None:
        if kind == "connected":
            self.connecting = False
            self.connected = True
            self.disconnecting = False
            self.operation_pending = False
            self._load_snapshot(payload)
            self._set_status("配置串口已连接", "#2E7D32")
        elif kind == "read-complete":
            page, value = payload
            self.operation_pending = False
            self._load_page(page, value)
            self._set_page_status(page, "已重新读取")
        elif kind == "apply-complete":
            page, value, save = payload
            self.operation_pending = False
            self._accept_applied_value(page, value)
            action = "已应用并保存到 Flash" if save else "已临时应用到 RAM"
            self._set_page_status(page, action)
        elif kind == "error":
            self.operation_pending = False
            self.connecting = False
            self.connected = False
            self._set_status(f"配置操作失败：{payload}", "#C62828")
            if not self.closed and not self.disconnecting:
                messagebox.showerror(
                    "配置操作失败",
                    f"{payload}\n\n请确认该串口未被 Aime 测试或其他程序占用。"
                    "若失败发生在应用过程中，设备 RAM 可能只完成部分字段；"
                    "请重新连接并读取后再修改。",
                    parent=self.root,
                )
        elif kind == "disconnected":
            self.connecting = False
            self.connected = False
            self.disconnecting = False
            self.operation_pending = False
            self.handle = None
            if not self.status_var.get().startswith("配置操作失败"):
                self._set_status("配置串口已断开", "#455A64")
        self._update_widgets()

    def _load_snapshot(self, snapshot: DeviceConfigSnapshot) -> None:
        self.snapshot = snapshot
        self._load_touch(snapshot.touch)
        self._load_led(snapshot.led)
        self._load_keyboard(snapshot.keyboard)

    def _load_page(self, page: ConfigPage, value: Any) -> None:
        if page == "touch":
            self._load_touch(value)
        elif page == "led":
            self._load_led(value)
        elif page == "keyboard":
            self._load_keyboard(value)

    def _load_touch(self, config: TouchConfig) -> None:
        self.touch_device_config = config
        self.touch_draft = list(config.entries)
        for item in self.touch_tree.get_children():
            self.touch_tree.delete(item)
        for index, entry in enumerate(self.touch_draft):
            self.touch_tree.insert(
                "",
                "end",
                iid=str(index),
                text=str(index),
                values=(entry.zone, entry.block),
            )
            self._update_touch_row(index)
        if self.touch_draft:
            self.touch_tree.selection_set("0")
            self.touch_tree.focus("0")
            self.touch_tree.see("0")
            self._load_touch_editor(0)
        self._update_touch_dirty()

    def _load_led(self, config: LedConfig) -> None:
        self.led_device_config = config
        self.loading_controls = True
        try:
            self.led_per_bit_var.set(str(config.led_per_bit))
            self.led_rainbow_var.set(config.rainbow_enabled)
        finally:
            self.loading_controls = False
        self._update_led_dirty()

    def _load_keyboard(self, config: KeyboardConfig) -> None:
        self.keyboard_device_config = config
        self.loading_controls = True
        try:
            self.keyboard_layout_var.set(
                "1P" if config.main_layout == LAYOUT_1P else "2P"
            )
            for index, (variable, keycode) in enumerate(
                zip(self.keyboard_ek_vars, config.ek_keycodes, strict=True)
            ):
                widget = self._keyboard_ek_combo(index)
                widget.configure(values=tuple(self.hid_name_to_code))
                display = self.hid_code_to_name.get(keycode)
                if display is None:
                    display = f"未识别 0x{keycode:02X}（保持原值）"
                    widget.configure(values=(display, *tuple(self.hid_name_to_code)))
                    self.keyboard_unknown_names[index] = display
                    self.keyboard_unknown_codes[index] = keycode
                else:
                    self.keyboard_unknown_names[index] = None
                    self.keyboard_unknown_codes[index] = None
                variable.set(display)
            self._update_keyboard_preview()
        finally:
            self.loading_controls = False
        self._update_keyboard_dirty()

    def _keyboard_ek_combo(self, index: int) -> ttk.Combobox:
        return self.keyboard_ek_combos[index]

    def _on_touch_selection(self, _event: object | None = None) -> None:
        selection = self.touch_tree.selection()
        if not selection:
            return
        self._load_touch_editor(int(selection[0]))

    def _load_touch_editor(self, channel: int) -> None:
        if not 0 <= channel < len(self.touch_draft):
            return
        entry = self.touch_draft[channel]
        self.loading_controls = True
        try:
            self.selected_touch_channel_var.set(f"物理通道：{channel}")
            self.touch_zone_var.set(entry.zone)
            self.touch_block_var.set(entry.block)
        finally:
            self.loading_controls = False

    def _commit_touch_editor(self, _event: object | None = None) -> None:
        if self.loading_controls:
            return
        selection = self.touch_tree.selection()
        if not selection:
            return
        channel = int(selection[0])
        entry = TouchMapEntry(zone=self.touch_zone_var.get())
        self.touch_block_var.set(entry.block)
        self.touch_draft[channel] = entry
        self._update_touch_row(channel)
        self._update_touch_dirty()

    def _update_touch_row(self, channel: int) -> None:
        entry = self.touch_draft[channel]
        changed = (
            self.touch_device_config is not None
            and entry != self.touch_device_config.entries[channel]
        )
        self.touch_tree.item(
            str(channel),
            text=f"{channel} *" if changed else str(channel),
            values=(entry.zone, entry.block),
            tags=("changed",) if changed else (),
        )

    def _on_led_changed(self, _event: object | None = None) -> None:
        if not self.loading_controls:
            self._update_led_dirty()

    def _on_keyboard_changed(self, _event: object | None = None) -> None:
        if self.loading_controls:
            return
        self._update_keyboard_preview()
        self._update_keyboard_dirty()

    def _current_led_config(self) -> LedConfig:
        led_per_bit = int(self.led_per_bit_var.get())
        return LedConfig(
            led_per_bit=led_per_bit,
            rainbow_enabled=self.led_rainbow_var.get(),
        )

    def _current_keyboard_config(self) -> KeyboardConfig:
        layout = LAYOUT_1P if self.keyboard_layout_var.get() == "1P" else LAYOUT_2P
        resolved: list[int] = []
        for index, variable in enumerate(self.keyboard_ek_vars):
            name = variable.get()
            if name in self.hid_name_to_code:
                resolved.append(self.hid_name_to_code[name])
            elif (
                name == self.keyboard_unknown_names[index]
                and self.keyboard_unknown_codes[index] is not None
            ):
                unknown_code = self.keyboard_unknown_codes[index]
                if unknown_code is None:  # pragma: no cover - narrowed above
                    raise ValueError(f"未知 HID 键值：{name}")
                resolved.append(unknown_code)
            else:
                raise ValueError(f"未知 HID 键值：{name}")
        keycodes = tuple(resolved)
        return KeyboardConfig(main_layout=layout, ek_keycodes=keycodes)

    def _update_keyboard_preview(self) -> None:
        layout = LAYOUT_1P if self.keyboard_layout_var.get() == "1P" else LAYOUT_2P
        for index, (variable, keycode) in enumerate(
            zip(
                self.keyboard_btn_preview_vars,
                main_keycodes_for_layout(layout),
                strict=True,
            )
        ):
            variable.set(f"BTN{index + 1}: {hid_key_name(keycode)}")

    def _update_touch_dirty(self) -> None:
        dirty = (
            self.touch_device_config is not None
            and tuple(self.touch_draft) != self.touch_device_config.entries
        )
        self.touch_dirty_var.set("有未应用修改" if dirty else "已与设备同步")

    def _update_led_dirty(self) -> None:
        try:
            current = self._current_led_config()
            led_per_bit = current.led_per_bit
        except (TypeError, ValueError):
            current = None
            led_per_bit = 0
        self.led_physical_count_var.set(
            f"物理灯珠总数：{LOGICAL_LED_COUNT * led_per_bit}"
        )
        dirty = self.led_device_config is not None and current != self.led_device_config
        self.led_dirty_var.set("有未应用修改" if dirty else "已与设备同步")

    def _update_keyboard_dirty(self) -> None:
        try:
            current = self._current_keyboard_config()
        except ValueError:
            current = None
        dirty = (
            self.keyboard_device_config is not None
            and current != self.keyboard_device_config
        )
        self.keyboard_dirty_var.set("有未应用修改" if dirty else "已与设备同步")

    def _page_is_dirty(self, page: ConfigPage) -> bool:
        variable = {
            "touch": self.touch_dirty_var,
            "led": self.led_dirty_var,
            "keyboard": self.keyboard_dirty_var,
        }[page]
        return variable.get() == "有未应用修改"

    def _accept_applied_value(self, page: ConfigPage, value: Any) -> None:
        if page == "touch":
            if self.touch_device_config is None:
                raise RuntimeError("尚未读取 Touch 配置")
            entries = list(self.touch_device_config.entries)
            for channel, entry in value.items():
                entries[channel] = entry
            self.touch_device_config = TouchConfig(entries=tuple(entries))
            for channel in range(len(self.touch_draft)):
                self._update_touch_row(channel)
            self._update_touch_dirty()
        elif page == "led":
            self.led_device_config = value
            self._update_led_dirty()
        elif page == "keyboard":
            # Rebuild each EK choice list from the value now active in RAM.
            # Once an unknown legacy byte is deliberately replaced with a
            # known key, it must no longer remain available as a raw choice.
            self._load_keyboard(value)

    def _set_page_status(self, page: ConfigPage, text: str) -> None:
        {
            "touch": self.touch_page_status_var,
            "led": self.led_page_status_var,
            "keyboard": self.keyboard_page_status_var,
        }[page].set(text)

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        self.status_label.configure(foreground=color)

    def _update_widgets(self) -> None:
        active = (
            self.connected and not self.operation_pending and not self.disconnecting
        )
        self.port_combo.configure(
            state="disabled"
            if self.connected or self.connecting or self.disconnecting
            else "readonly"
        )
        self.refresh_button.configure(
            state="disabled"
            if self.connected or self.connecting or self.disconnecting
            else "normal"
        )
        self.connect_button.configure(
            text="断开" if self.connected or self.connecting else "连接",
            state="disabled" if self.disconnecting else "normal",
        )
        for widget, ready_state in self.input_widgets:
            if isinstance(widget, ttk.Treeview):
                widget.state(("!disabled",) if active else ("disabled",))
            else:
                widget.configure(state=ready_state if active else "disabled")
        for buttons in self.page_buttons.values():
            for button in buttons:
                button.configure(state="normal" if active else "disabled")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.worker_poll_after_id is not None:
            with suppress(tk.TclError):
                self.root.after_cancel(self.worker_poll_after_id)
            self.worker_poll_after_id = None
        handle = self.handle
        if handle is not None:
            self._discard_worker_commands(handle)
            handle.commands.put(_WorkerCommand("disconnect"))
            thread = handle.thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=3.0)
        with suppress(tk.TclError):
            self.root.destroy()


def launch_device_config() -> int:
    """Create the standalone configuration window and run it until closed."""

    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise DeviceConfigUiError(f"无法创建图形界面：{error}") from error
    try:
        DeviceConfigWindow(root)
    except Exception:
        with suppress(tk.TclError):
            root.destroy()
        raise
    root.mainloop()
    return 0


__all__ = [
    "DeviceConfigUiError",
    "DeviceConfigWindow",
    "launch_device_config",
    "serial_port_label",
]
