from __future__ import annotations

import tempfile
import threading
import time
import tkinter as tk
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tenodx_config.config_file import read_config_file, write_config_file
from tenodx_config.device_config import (
    LAYOUT_1P,
    LAYOUT_2P,
    PSOC_STATUS_FLAG_CONNECTED,
    PSOC_STATUS_FLAG_LEGACY_FIRMWARE,
    PSOC_STATUS_FLAG_OPERATIONAL,
    PSOC_STATUS_FLAG_POWER_CYCLE_REQUIRED,
    PSOC_STATUS_FLAG_SOFT_RESET_SUPPORTED,
    PSOC_STATUS_FLAG_VALID,
    TOUCH_CDC_MODE_MAI2TOUCH,
    TOUCH_CDC_MODE_RAW,
    TOUCH_ZONE_NAMES,
    DeviceConfigSnapshot,
    KeyboardConfig,
    LedConfig,
    PsocRuntimeStatus,
    TouchConfig,
    TouchMapEntry,
    TouchRuntimeStatus,
)
from tenodx_config.device_config_ui import (
    DeviceConfigWindow,
    _psoc_firmware_text,
    serial_port_label,
)


def make_touch_config() -> TouchConfig:
    return TouchConfig(
        entries=tuple(
            TouchMapEntry(zone=TOUCH_ZONE_NAMES[channel])
            for channel in range(34)
        )
    )


def make_snapshot() -> DeviceConfigSnapshot:
    return DeviceConfigSnapshot(
        touch=make_touch_config(),
        led=LedConfig(led_per_bit=2, rainbow_enabled=False),
        keyboard=KeyboardConfig(
            main_layout=LAYOUT_1P,
            ek_keycodes=(0xFE, 0x05, 0x00, 0x63),
        ),
    )


def make_runtime_status() -> TouchRuntimeStatus:
    return TouchRuntimeStatus(
        state=6,
        flags=0x02,
        devices=(
            PsocRuntimeStatus(
                address=0x08,
                raw_status=0x02,
                flags=(
                    PSOC_STATUS_FLAG_CONNECTED
                    | PSOC_STATUS_FLAG_OPERATIONAL
                    | PSOC_STATUS_FLAG_VALID
                    | PSOC_STATUS_FLAG_SOFT_RESET_SUPPORTED
                ),
                consecutive_failures=0,
                status_age_ms=8,
            ),
            PsocRuntimeStatus(
                address=0x09,
                raw_status=0x02,
                flags=(
                    PSOC_STATUS_FLAG_CONNECTED
                    | PSOC_STATUS_FLAG_OPERATIONAL
                    | PSOC_STATUS_FLAG_VALID
                    | PSOC_STATUS_FLAG_LEGACY_FIRMWARE
                    | PSOC_STATUS_FLAG_POWER_CYCLE_REQUIRED
                ),
                consecutive_failures=1,
                status_age_ms=12,
            ),
        ),
    )


class PsocFirmwareTextTests(unittest.TestCase):
    def test_reports_detection_progress_before_running(self) -> None:
        self.assertEqual(
            _psoc_firmware_text(
                state=5,
                connected=True,
                flags=PSOC_STATUS_FLAG_CONNECTED | PSOC_STATUS_FLAG_VALID,
            ),
            "识别中",
        )

    def test_reports_failed_detection_only_after_running(self) -> None:
        self.assertEqual(
            _psoc_firmware_text(
                state=6,
                connected=True,
                flags=(
                    PSOC_STATUS_FLAG_CONNECTED
                    | PSOC_STATUS_FLAG_OPERATIONAL
                    | PSOC_STATUS_FLAG_VALID
                ),
            ),
            "未知（识别失败）",
        )

    def test_reports_disconnected_device_separately(self) -> None:
        self.assertEqual(
            _psoc_firmware_text(state=6, connected=False, flags=0),
            "无法识别（未连接）",
        )


class FakeController:
    def __init__(self, port: str, snapshot: DeviceConfigSnapshot) -> None:
        self.port = port
        self.touch = snapshot.touch
        self.led = snapshot.led
        self.keyboard = snapshot.keyboard
        self.runtime_status = make_runtime_status()
        self.calls: list[tuple[object, ...]] = [("open", port)]
        self.thread_ids: list[int] = []

    def _record(self, *call: object) -> None:
        self.calls.append(call)
        self.thread_ids.append(threading.get_ident())

    def probe(self) -> None:
        self._record("probe")

    def read_snapshot(self) -> DeviceConfigSnapshot:
        self._record("read_snapshot")
        return DeviceConfigSnapshot(self.touch, self.led, self.keyboard)

    def read_touch(self) -> TouchConfig:
        self._record("read_touch")
        return self.touch

    def read_touch_runtime_status(self) -> TouchRuntimeStatus:
        self._record("read_touch_runtime_status")
        return self.runtime_status

    def apply_touch(
        self,
        changes: Mapping[int, TouchMapEntry],
        *,
        cdc_mode: int | None = None,
    ) -> None:
        copied = dict(changes)
        self._record("apply_touch", copied, cdc_mode)
        entries = list(self.touch.entries)
        for channel, entry in copied.items():
            entries[channel] = entry
        self.touch = TouchConfig(
            tuple(entries),
            self.touch.cdc_mode if cdc_mode is None else cdc_mode,
        )

    def save_touch(self) -> None:
        self._record("save_touch")

    def restore_touch_defaults(self) -> TouchConfig:
        self._record("restore_touch_defaults")
        self.touch = make_touch_config()
        return self.touch

    def read_led(self) -> LedConfig:
        self._record("read_led")
        return self.led

    def apply_led(self, config: LedConfig) -> None:
        self._record("apply_led", config)
        self.led = config

    def save_led(self) -> None:
        self._record("save_led")

    def restore_led_defaults(self) -> LedConfig:
        self._record("restore_led_defaults")
        self.led = LedConfig(led_per_bit=2, rainbow_enabled=True)
        return self.led

    def read_keyboard(self) -> KeyboardConfig:
        self._record("read_keyboard")
        return self.keyboard

    def apply_keyboard(self, config: KeyboardConfig) -> None:
        self._record("apply_keyboard", config)
        self.keyboard = config

    def save_keyboard(self) -> None:
        self._record("save_keyboard")

    def restore_keyboard_defaults(self) -> KeyboardConfig:
        self._record("restore_keyboard_defaults")
        self.keyboard = KeyboardConfig(
            main_layout=LAYOUT_1P,
            ek_keycodes=(0x20, 0x55, 0x25, 0x26),
        )
        return self.keyboard

    def close(self) -> None:
        self._record("close")


def wait_for_tk(
    root: tk.Tk, predicate: Callable[[], bool], timeout: float = 2.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for Tk/background work")


class DeviceConfigUiHelpersTests(unittest.TestCase):
    def test_serial_label_has_actual_description_vid_pid_without_fixed_prefix(
        self,
    ) -> None:
        port = SimpleNamespace(
            device="COM12",
            description="USB Serial Device",
            serial_number="UID-12",
            vid=0x0483,
            pid=0x5740,
        )
        label = serial_port_label(port, "TenoDX Aime")
        self.assertIn("COM12 | TenoDX Aime", label)
        self.assertIn("VID 0483", label)
        self.assertIn("PID 5740", label)
        self.assertNotIn("总线已报告设备描述：", label)

        missing = serial_port_label(
            SimpleNamespace(
                device="COM1",
                description="",
                serial_number=None,
                vid=None,
                pid=None,
            ),
            None,
        )
        self.assertIn("描述未报告", missing)
        self.assertIn("VID 未报告", missing)
        self.assertIn("PID 未报告", missing)

class DeviceConfigWindowTests(unittest.TestCase):
    def _root(self) -> tk.Tk:
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        root.withdraw()
        return root

    @staticmethod
    def _port() -> SimpleNamespace:
        return SimpleNamespace(
            device="COM12",
            description="USB Serial Device",
            serial_number="UID-12",
            vid=0x0483,
            pid=0x5740,
        )

    def test_manual_connect_does_not_write_and_worker_never_updates_tk(self) -> None:
        root = self._root()
        controllers: list[FakeController] = []
        main_thread = threading.get_ident()
        ui_thread_ids: list[int] = []
        app: DeviceConfigWindow | None = None

        def factory(port: str) -> FakeController:
            controller = FakeController(port, make_snapshot())
            controllers.append(controller)
            return controller

        try:
            app = DeviceConfigWindow(
                root,
                controller_factory=factory,
                port_provider=lambda: [self._port()],
                bus_description_provider=lambda: {"com12": "TenoDX Aime"},
            )
            original_load_snapshot = app._load_snapshot

            def record_load(snapshot: DeviceConfigSnapshot) -> None:
                ui_thread_ids.append(threading.get_ident())
                original_load_snapshot(snapshot)

            app._load_snapshot = record_load  # type: ignore[method-assign]
            root.update_idletasks()
            self.assertEqual(app.port_var.get(), "")
            self.assertEqual(len(app.port_combo.cget("values")), 1)

            app.port_var.set(next(iter(app.serial_ports_by_label)))
            self.assertTrue(app.connect())
            wait_for_tk(root, lambda: app.connected)

            self.assertEqual(ui_thread_ids, [main_thread])
            self.assertTrue(controllers[0].thread_ids)
            self.assertNotIn(main_thread, controllers[0].thread_ids)
            self.assertEqual(
                controllers[0].calls[:3],
                [("open", "COM12"), ("probe",), ("read_snapshot",)],
            )
            wait_for_tk(
                root,
                lambda: "运行中" in app.touch_controller_status_var.get(),
            )
            self.assertIn("新版", app.psoc_status_vars[0].get())
            self.assertIn("旧版", app.psoc_status_vars[1].get())
            self.assertIn("需要断电重连", app.psoc_status_vars[1].get())
            self.assertEqual(app.touch_dirty_var.get(), "已与设备同步")
            self.assertEqual(
                app.touch_cdc_mode_var.get(), TOUCH_CDC_MODE_MAI2TOUCH
            )
            self.assertEqual(app.led_dirty_var.get(), "已与设备同步")
            self.assertEqual(app.keyboard_dirty_var.get(), "已与设备同步")
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_touch_allows_shared_region_led_saves_and_unknown_ek_is_preserved(
        self,
    ) -> None:
        root = self._root()
        controllers: list[FakeController] = []
        app: DeviceConfigWindow | None = None

        def factory(port: str) -> FakeController:
            controller = FakeController(port, make_snapshot())
            controllers.append(controller)
            return controller

        try:
            app = DeviceConfigWindow(
                root,
                controller_factory=factory,
                port_provider=lambda: [self._port()],
                bus_description_provider=lambda: {},
            )
            app.port_var.set(next(iter(app.serial_ports_by_label)))
            app.connect()
            wait_for_tk(root, lambda: app.connected)
            controller = controllers[0]

            # Each physical channel selects exactly one region, while different
            # channels may select the same region in one batch update.
            app.touch_tree.selection_set("0")
            app._load_touch_editor(0)
            app.touch_zone_var.set("C2")
            app._commit_touch_editor()
            self.assertEqual(app.touch_tree.item("0", "text"), "0 *")
            self.assertEqual(app.touch_block_var.get(), "C")

            app.touch_tree.selection_set("1")
            app._load_touch_editor(1)
            app.touch_zone_var.set("C2")
            app._commit_touch_editor()
            self.assertEqual(app.touch_tree.item("1", "text"), "1 *")
            self.assertEqual(app.touch_dirty_var.get(), "有未应用修改")
            self.assertFalse(any(call[0] == "apply_touch" for call in controller.calls))

            app.apply_page("touch", save=False)
            wait_for_tk(root, lambda: not app.operation_pending)
            touch_calls = [
                call for call in controller.calls if call[0] == "apply_touch"
            ]
            self.assertEqual(len(touch_calls), 1)
            changes = touch_calls[0][1]
            self.assertEqual(set(changes), {0, 1})
            self.assertEqual(changes[0], TouchMapEntry("C2"))
            self.assertEqual(changes[1], TouchMapEntry("C2"))
            self.assertIsNone(touch_calls[0][2])
            self.assertNotIn(("save_touch",), controller.calls)
            self.assertEqual(app.touch_tree.item("0", "text"), "0")
            self.assertEqual(app.touch_tree.item("1", "text"), "1")

            app.touch_cdc_mode_var.set(TOUCH_CDC_MODE_RAW)
            app._on_touch_mode_changed()
            self.assertEqual(app.touch_dirty_var.get(), "有未应用修改")
            app.apply_page("touch", save=True)
            wait_for_tk(root, lambda: not app.operation_pending)
            self.assertIn(
                ("apply_touch", {}, TOUCH_CDC_MODE_RAW),
                controller.calls,
            )
            mode_call_index = controller.calls.index(
                ("apply_touch", {}, TOUCH_CDC_MODE_RAW)
            )
            self.assertEqual(controller.calls[mode_call_index + 1], ("save_touch",))
            self.assertEqual(app.touch_dirty_var.get(), "已与设备同步")

            app.led_per_bit_var.set("4")
            app.led_rainbow_var.set(True)
            app._on_led_changed()
            self.assertEqual(app.led_physical_count_var.get(), "物理灯珠总数：32")
            app.apply_page("led", save=True)
            wait_for_tk(root, lambda: not app.operation_pending)
            self.assertIn(
                ("apply_led", LedConfig(led_per_bit=4, rainbow_enabled=True)),
                controller.calls,
            )
            self.assertEqual(
                controller.calls[
                    controller.calls.index(
                        (
                            "apply_led",
                            LedConfig(led_per_bit=4, rainbow_enabled=True),
                        )
                    )
                    + 1
                ],
                ("save_led",),
            )

            # 0xFE is not offered as a writable raw HID value.  It appears only
            # in EK_1 to preserve the current device byte during a layout change.
            unknown_display = app.keyboard_ek_vars[0].get()
            self.assertIn("0xFE", unknown_display)
            self.assertIn(unknown_display, app._keyboard_ek_combo(0).cget("values"))
            self.assertNotIn(
                unknown_display,
                app._keyboard_ek_combo(1).cget("values"),
            )
            app.keyboard_layout_var.set("2P")
            app._on_keyboard_changed()
            self.assertIn("Keypad 8", app.keyboard_btn_preview_vars[0].get())
            app.apply_page("keyboard", save=False)
            wait_for_tk(root, lambda: not app.operation_pending)
            self.assertIn(
                (
                    "apply_keyboard",
                    KeyboardConfig(
                        main_layout=LAYOUT_2P,
                        ek_keycodes=(0xFE, 0x05, 0x00, 0x63),
                    ),
                ),
                controller.calls,
            )

            app.keyboard_ek_vars[0].set("A")
            app._on_keyboard_changed()
            app.apply_page("keyboard", save=False)
            wait_for_tk(root, lambda: not app.operation_pending)
            self.assertNotIn(
                unknown_display,
                app._keyboard_ek_combo(0).cget("values"),
            )
            self.assertEqual(app.keyboard_ek_vars[0].get(), "A")
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_reading_touch_does_not_overwrite_dirty_led_draft(self) -> None:
        root = self._root()
        controllers: list[FakeController] = []
        app: DeviceConfigWindow | None = None

        def factory(port: str) -> FakeController:
            controller = FakeController(port, make_snapshot())
            controllers.append(controller)
            return controller

        try:
            app = DeviceConfigWindow(
                root,
                controller_factory=factory,
                port_provider=lambda: [self._port()],
                bus_description_provider=lambda: {},
            )
            app.port_var.set(next(iter(app.serial_ports_by_label)))
            app.connect()
            wait_for_tk(root, lambda: app.connected)

            app.led_per_bit_var.set("3")
            app._on_led_changed()
            self.assertEqual(app.led_dirty_var.get(), "有未应用修改")

            controllers[0].touch = TouchConfig(
                tuple(TouchMapEntry(zone="C1") for _index in range(34))
            )
            with patch(
                "tenodx_config.device_config_ui.messagebox.askyesno",
                return_value=True,
            ):
                app.read_page("touch")
            wait_for_tk(root, lambda: not app.operation_pending)
            self.assertEqual(app.touch_draft[0], TouchMapEntry("C1"))
            self.assertEqual(app.led_per_bit_var.get(), "3")
            self.assertEqual(app.led_dirty_var.get(), "有未应用修改")
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_restore_defaults_reads_back_ram_without_saving(self) -> None:
        root = self._root()
        controllers: list[FakeController] = []
        app: DeviceConfigWindow | None = None

        def factory(port: str) -> FakeController:
            controller = FakeController(port, make_snapshot())
            controllers.append(controller)
            return controller

        try:
            app = DeviceConfigWindow(
                root,
                controller_factory=factory,
                port_provider=lambda: [self._port()],
                bus_description_provider=lambda: {},
            )
            app.port_var.set(next(iter(app.serial_ports_by_label)))
            app.connect()
            wait_for_tk(root, lambda: app.connected)

            with patch(
                "tenodx_config.device_config_ui.messagebox.askyesno",
                return_value=True,
            ):
                app.restore_page_defaults("led")
            wait_for_tk(root, lambda: not app.operation_pending)

            self.assertIn(("restore_led_defaults",), controllers[0].calls)
            self.assertNotIn(("save_led",), controllers[0].calls)
            self.assertEqual(app.led_per_bit_var.get(), "2")
            self.assertTrue(app.led_rainbow_var.get())
            self.assertIn("尚未保存", app.led_page_status_var.get())
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_export_draft_and_import_complete_config_without_device_writes(self) -> None:
        root = self._root()
        controllers: list[FakeController] = []
        app: DeviceConfigWindow | None = None

        def factory(port: str) -> FakeController:
            controller = FakeController(port, make_snapshot())
            controllers.append(controller)
            return controller

        try:
            app = DeviceConfigWindow(
                root,
                controller_factory=factory,
                port_provider=lambda: [self._port()],
                bus_description_provider=lambda: {},
            )
            app.port_var.set(next(iter(app.serial_ports_by_label)))
            app.connect()
            wait_for_tk(root, lambda: app.connected)
            controller = controllers[0]

            app.touch_cdc_mode_var.set(TOUCH_CDC_MODE_RAW)
            app._on_touch_mode_changed()
            app.led_per_bit_var.set("4")
            app.led_rainbow_var.set(True)
            app._on_led_changed()

            with tempfile.TemporaryDirectory() as directory:
                export_path = Path(directory, "export.tenodx.json")
                with patch(
                    "tenodx_config.device_config_ui.filedialog.asksaveasfilename",
                    return_value=str(export_path),
                ):
                    app.export_configuration()
                exported = read_config_file(export_path)
                self.assertEqual(exported.touch.cdc_mode, TOUCH_CDC_MODE_RAW)
                self.assertEqual(exported.led, LedConfig(4, True))
                self.assertEqual(exported.keyboard.ek_keycodes[0], 0xFE)

                imported = DeviceConfigSnapshot(
                    touch=TouchConfig(
                        entries=tuple(TouchMapEntry("E8") for _ in range(34)),
                        cdc_mode=TOUCH_CDC_MODE_MAI2TOUCH,
                    ),
                    led=LedConfig(3, False),
                    keyboard=KeyboardConfig(
                        main_layout=LAYOUT_2P,
                        ek_keycodes=(0xFD, 0x00, 0x04, 0x55),
                    ),
                )
                import_path = Path(directory, "import.tenodx.json")
                write_config_file(import_path, imported)
                calls_before_import = list(controller.calls)
                with (
                    patch(
                        "tenodx_config.device_config_ui.filedialog.askopenfilename",
                        return_value=str(import_path),
                    ),
                    patch(
                        "tenodx_config.device_config_ui.messagebox.askyesno",
                        return_value=True,
                    ),
                ):
                    app.import_configuration()

                self.assertEqual(controller.calls, calls_before_import)
                self.assertEqual(app.touch_draft[0], TouchMapEntry("E8"))
                self.assertEqual(app.led_per_bit_var.get(), "3")
                self.assertIn("0xFD", app.keyboard_ek_vars[0].get())
                self.assertEqual(app.touch_dirty_var.get(), "已导入，尚未应用")
                self.assertEqual(app.led_dirty_var.get(), "已导入，尚未应用")
                self.assertEqual(app.keyboard_dirty_var.get(), "已导入，尚未应用")

                app.apply_page("keyboard", save=False)
                wait_for_tk(root, lambda: not app.operation_pending)
                self.assertIn(
                    (
                        "apply_keyboard",
                        KeyboardConfig(
                            main_layout=LAYOUT_2P,
                            ek_keycodes=(0xFD, 0x00, 0x04, 0x55),
                        ),
                    ),
                    controller.calls,
                )
                self.assertEqual(app.keyboard_dirty_var.get(), "已与设备同步")
                self.assertEqual(app.touch_dirty_var.get(), "已导入，尚未应用")
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
