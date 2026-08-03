from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import unittest
from collections import deque
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import patch

import serial

from tenodx_config.controller_test_ui import (
    BUTTON_SCANCODES_1P,
    BUTTON_SCANCODES_2P,
    ControllerTestWindow,
    MainButtonState,
    active_names,
    open_touch_serial,
    serial_port_label,
)
from tenodx_config.mai2led import BLACK
from tenodx_config.raw_keyboard import RawKeyboardEvent, make_keyboard_device
from tenodx_config.touch_protocol import (
    RSET_COMMAND,
    STAT_COMMAND,
    encode_touch_frame,
)


class RecordingSerial:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls: list[object] = []
        self.closed = False

    def reset_input_buffer(self) -> None:
        self.calls.append("reset")

    def write(self, data: bytes) -> int:
        self.calls.append(("write", data))
        return len(data)

    def flush(self) -> None:
        self.calls.append("flush")

    def close(self) -> None:
        self.closed = True


class StreamingSerial(RecordingSerial):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.buffer = bytearray()

    @property
    def in_waiting(self) -> int:
        return len(self.buffer)

    def read(self, size: int) -> bytes:
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def feed(self, data: bytes) -> None:
        self.buffer.extend(data)


class FakeMonitor:
    def __init__(self) -> None:
        self.events: queue.SimpleQueue[object] = queue.SimpleQueue()
        self.error: str | None = None
        self.target_path: str | None = None
        self.closed = False

    def set_target(self, path: str | None) -> None:
        self.target_path = path

    def clear_target(self) -> None:
        self.target_path = None

    def target_is_present(self) -> bool:
        return self.target_path is not None

    def close(self) -> None:
        self.closed = True


class FakeAimeController:
    def __init__(
        self,
        port: str,
        baudrate: int,
        results: list[object],
    ) -> None:
        self.calls: list[tuple[object, ...]] = [("open", port, baudrate)]
        self.thread_ids: list[int] = []
        self.results = deque(results)

    def _record(self, *call: object) -> None:
        self.calls.append(call)
        self.thread_ids.append(threading.get_ident())

    def probe(self) -> object:
        self._record("probe")
        return SimpleNamespace(firmware=b"\x94", hardware=b"837-15396")

    def start_polling(self) -> None:
        self._record("start_polling")

    def read_card(self) -> object:
        self._record("read_card")
        if self.results:
            return self.results.popleft()
        return SimpleNamespace(present=False, access_code=None, raw_block=b"")

    def stop_polling(self) -> None:
        self._record("stop_polling")

    def close(self) -> None:
        self._record("close")


class FakeLedController:
    def __init__(self, port: str, *, fail_set_all: bool = False) -> None:
        self.calls: list[tuple[object, ...]] = [("open", port)]
        self.thread_ids: list[int] = []
        self.fail_set_all = fail_set_all

    def _record(self, *call: object) -> None:
        self.calls.append(call)
        self.thread_ids.append(threading.get_ident())

    def probe(self) -> object:
        self._record("probe")
        return SimpleNamespace(board_number="15070-04", firmware_revision=0x90)

    def set_all(self, color: tuple[int, int, int]) -> None:
        self._record("set_all", tuple(color))
        if self.fail_set_all:
            self.fail_set_all = False
            raise RuntimeError("LED write failed")

    def set_chase_frame(self, index: int, color: tuple[int, int, int]) -> None:
        self._record("chase", index, tuple(color))

    def fade_all(
        self,
        start: tuple[int, int, int],
        end: tuple[int, int, int],
        duration_ms: int,
    ) -> None:
        self._record("fade", tuple(start), tuple(end), duration_ms)

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


class MainButtonStateTests(unittest.TestCase):
    def test_both_keyboard_layouts_map_to_the_same_eight_buttons(self) -> None:
        state = MainButtonState()
        for scan_code in BUTTON_SCANCODES_1P:
            state.update(scan_code, True, False)
        self.assertEqual(state.mask, 0xFF)

        state.clear()
        for scan_code in BUTTON_SCANCODES_2P:
            state.update(scan_code, True, False)
        self.assertEqual(state.mask, 0xFF)

    def test_repeat_release_and_two_codes_for_one_button_are_not_latched(self) -> None:
        state = MainButtonState()
        first_1p = BUTTON_SCANCODES_1P[0]
        first_2p = BUTTON_SCANCODES_2P[0]

        self.assertTrue(state.update(first_1p, True, False))
        self.assertFalse(state.update(first_1p, True, False))
        self.assertFalse(state.update(first_2p, True, False))
        self.assertEqual(state.mask, 0x01)
        self.assertFalse(state.update(first_1p, False, False))
        self.assertEqual(state.mask, 0x01)
        self.assertTrue(state.update(first_2p, False, False))
        self.assertEqual(state.mask, 0)

    def test_extended_and_unmapped_keys_are_ignored(self) -> None:
        state = MainButtonState()
        self.assertFalse(state.update(BUTTON_SCANCODES_1P[0], True, True))
        self.assertFalse(state.update(0x01, True, False))
        self.assertEqual(state.mask, 0)


class TouchConnectionTests(unittest.TestCase):
    def test_open_uses_9600_8n1_and_only_rset_then_stat(self) -> None:
        created: list[RecordingSerial] = []
        sleeps: list[float] = []

        def factory(**kwargs: object) -> RecordingSerial:
            device = RecordingSerial(**kwargs)
            created.append(device)
            return device

        result = open_touch_serial(
            "COM9",
            serial_factory=factory,
            sleeper=sleeps.append,
        )

        self.assertIs(result, created[0])
        self.assertEqual(
            created[0].kwargs,
            {
                "port": "COM9",
                "baudrate": 9600,
                "bytesize": serial.EIGHTBITS,
                "parity": serial.PARITY_NONE,
                "stopbits": serial.STOPBITS_ONE,
                "timeout": 0,
                "write_timeout": 0.5,
            },
        )
        self.assertEqual(
            created[0].calls,
            [
                "reset",
                ("write", RSET_COMMAND),
                "flush",
                ("write", STAT_COMMAND),
                "flush",
            ],
        )
        self.assertEqual(sleeps, [0.1])


class DeviceLabelTests(unittest.TestCase):
    def test_touch_label_always_has_bus_description_vid_and_pid(self) -> None:
        port = SimpleNamespace(
            device="COM7",
            description="USB Serial Device",
            serial_number="UID-123",
            vid=0x0483,
            pid=0x5740,
        )
        label = serial_port_label(port, "TenoDX Touch")
        self.assertIn("TenoDX Touch", label)
        self.assertNotIn("总线已报告设备描述：", label)
        self.assertIn("VID 0483", label)
        self.assertIn("PID 5740", label)

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
        self.assertNotIn("总线已报告设备描述：", missing)
        self.assertIn("VID 未报告", missing)
        self.assertIn("PID 未报告", missing)

    def test_active_names_contains_only_the_current_state(self) -> None:
        self.assertEqual(active_names(0, ("A1", "A2", "A3")), "无")
        self.assertEqual(active_names(0b101, ("A1", "A2", "A3")), "A1 A3")


class WindowSelectionSmokeTests(unittest.TestCase):
    def test_manual_selection_and_latest_realtime_state(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        root.withdraw()

        monitor = FakeMonitor()
        now = [10.0]
        serial_devices: list[StreamingSerial] = []

        def serial_factory(**kwargs: object) -> StreamingSerial:
            device = StreamingSerial(**kwargs)
            serial_devices.append(device)
            return device

        port = SimpleNamespace(
            device="COM7",
            description="USB Serial Device",
            serial_number="UID-123",
            vid=0x0483,
            pid=0x5740,
        )
        keyboard = make_keyboard_device(
            r"\\?\HID#VID_0483&PID_5740&MI_06#UID-123#{GUID}",
            "TenoDX HID Keyboard",
        )
        app: ControllerTestWindow | None = None
        try:
            app = ControllerTestWindow(
                root,
                serial_factory=serial_factory,
                port_provider=lambda: [port],
                keyboard_provider=lambda: [keyboard],
                bus_description_provider=lambda: {"com7": "TenoDX Touch"},
                monitor_factory=lambda: monitor,  # type: ignore[arg-type]
                sleeper=lambda _delay: None,
                clock=lambda: now[0],
            )
            root.update_idletasks()
            self.assertEqual(app.touch_port_var.get(), "")
            self.assertEqual(app.keyboard_var.get(), "")
            self.assertEqual(app.led_port_var.get(), "")
            self.assertEqual(app.aime_port_var.get(), "")

            touch_labels = app.touch_port_combo.cget("values")
            keyboard_labels = app.keyboard_combo.cget("values")
            self.assertTrue(any("VID 0483" in item for item in touch_labels))
            self.assertTrue(any("PID 5740" in item for item in touch_labels))
            self.assertTrue(any("TenoDX Touch" in item for item in touch_labels))
            self.assertFalse(
                any("总线已报告设备描述：" in item for item in touch_labels)
            )
            self.assertTrue(any("VID 0483" in item for item in keyboard_labels))
            self.assertTrue(any("PID 5740" in item for item in keyboard_labels))
            self.assertTrue(
                any("TenoDX HID Keyboard" in item for item in keyboard_labels)
            )
            self.assertFalse(
                any("总线已报告设备描述：" in item for item in keyboard_labels)
            )

            app.touch_port_var.set(next(iter(app.serial_ports_by_label)))
            app.keyboard_var.set(next(iter(app.keyboards_by_label)))
            app.connect()
            self.assertTrue(app.connected)
            self.assertTrue(app.touch_connected)
            self.assertTrue(app.hid_connected)
            self.assertEqual(monitor.target_path, keyboard.path)

            device = serial_devices[0]
            active_touch = (1 << 0) | (1 << 17) | (1 << 33)
            device.feed(encode_touch_frame(active_touch))
            app._cancel_after("touch_poll_after_id")
            app._poll_touch_serial()
            self.assertEqual(app.current_touch_bits, active_touch)
            self.assertEqual(app.touch_state_var.get(), "触摸：A1 C2 E8")

            now[0] += 0.5
            app._cancel_after("touch_poll_after_id")
            app._poll_touch_serial()
            self.assertTrue(app.connected)
            self.assertEqual(app.current_touch_bits, 0)
            self.assertIn("超时", app.touch_status_var.get())

            now[0] += 0.1
            device.feed(encode_touch_frame(1 << 1))
            app._cancel_after("touch_poll_after_id")
            app._poll_touch_serial()
            self.assertEqual(app.current_touch_bits, 1 << 1)
            self.assertEqual(app.touch_status_var.get(), "运行中")

            monitor.events.put(
                RawKeyboardEvent(
                    kind="key",
                    device_path=keyboard.path,
                    scan_code=BUTTON_SCANCODES_1P[0],
                    is_pressed=True,
                )
            )
            monitor.events.put(
                RawKeyboardEvent(
                    kind="key",
                    device_path=keyboard.path + "-OTHER",
                    scan_code=BUTTON_SCANCODES_1P[1],
                    is_pressed=True,
                )
            )
            app._cancel_after("raw_poll_after_id")
            app._poll_raw_input()
            self.assertEqual(app.current_button_mask, 0x01)
            self.assertEqual(app.button_state_var.get(), "主按键：BTN1")

            monitor.error = "监听线程意外停止"
            app._cancel_after("raw_poll_after_id")
            with patch("tenodx_config.controller_test_ui.messagebox.showerror"):
                app._poll_raw_input()
            self.assertTrue(app.connected)
            self.assertTrue(app.touch_connected)
            self.assertFalse(app.hid_connected)
            self.assertIsNotNone(app.raw_poll_after_id)
            self.assertIn("监听线程意外停止", app.keyboard_status_var.get())
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()
        self.assertTrue(monitor.closed)


class CombinedModuleTests(unittest.TestCase):
    @staticmethod
    def _port(device: str) -> object:
        return SimpleNamespace(
            device=device,
            description=f"USB Serial {device}",
            serial_number=f"UID-{device}",
            vid=0x0483,
            pid=0x5740,
        )

    def _root(self) -> tk.Tk:
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        root.withdraw()
        return root

    def test_duplicate_serial_selection_is_atomic(self) -> None:
        root = self._root()
        monitor = FakeMonitor()
        opens: list[str] = []
        led_opens: list[str] = []
        app: ControllerTestWindow | None = None

        def serial_factory(**kwargs: object) -> StreamingSerial:
            opens.append(str(kwargs["port"]))
            return StreamingSerial(**kwargs)

        def led_factory(port: str, **_kwargs: object) -> FakeLedController:
            led_opens.append(port)
            return FakeLedController(port)

        try:
            app = ControllerTestWindow(
                root,
                serial_factory=serial_factory,
                port_provider=lambda: [self._port("COM7")],
                keyboard_provider=lambda: [],
                bus_description_provider=lambda: {"com7": "TenoDX CDC"},
                monitor_factory=lambda: monitor,  # type: ignore[arg-type]
                led_controller_factory=led_factory,
                sleeper=lambda _delay: None,
            )
            label = next(iter(app.serial_ports_by_label))
            app.touch_port_var.set(label)
            app.led_port_var.set(label)
            with patch(
                "tenodx_config.controller_test_ui.messagebox.showwarning"
            ) as warning:
                app.connect()
            warning.assert_called_once()
            self.assertEqual(opens, [])
            self.assertEqual(led_opens, [])
            self.assertFalse(app.connected)
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_aime_worker_displays_protocol_card_number_and_raw_block(self) -> None:
        root = self._root()
        monitor = FakeMonitor()
        invalid_block = bytes.fromhex("000000000000FA000000000000000000")
        controllers: list[FakeAimeController] = []
        app: ControllerTestWindow | None = None
        main_thread = threading.get_ident()

        def aime_factory(
            port: str, baudrate: int, **_kwargs: object
        ) -> FakeAimeController:
            controller = FakeAimeController(
                port,
                baudrate,
                [
                    SimpleNamespace(
                        present=True,
                        access_code=None,
                        raw_block=invalid_block,
                    )
                ],
            )
            controllers.append(controller)
            return controller

        try:
            app = ControllerTestWindow(
                root,
                port_provider=lambda: [self._port("COM8")],
                keyboard_provider=lambda: [],
                bus_description_provider=lambda: {"com8": "TenoDX Aime"},
                monitor_factory=lambda: monitor,  # type: ignore[arg-type]
                aime_controller_factory=aime_factory,
                sleeper=lambda _delay: None,
            )
            app.aime_port_var.set(next(iter(app.serial_ports_by_label)))
            self.assertTrue(app.connect_aime())
            wait_for_tk(
                root,
                lambda: app.aime_access_code_var.get() == "卡号无法解析",
            )
            self.assertTrue(app.aime_connected)
            self.assertEqual(app.aime_firmware_var.get(), "94")
            self.assertEqual(app.aime_hardware_var.get(), "837-15396")
            self.assertEqual(app.aime_block_var.get(), invalid_block.hex(" ").upper())
            self.assertNotIn("01 02 03 04", app.aime_access_code_var.get())
            self.assertTrue(controllers[0].thread_ids)
            self.assertNotIn(main_thread, controllers[0].thread_ids)

            app.stop_aime_scanning()
            wait_for_tk(root, lambda: not app.aime_scanning)
            valid_code = "01234567890123456789"
            valid_block = bytes.fromhex("00000000000001234567890123456789")
            app._apply_aime_event(
                "aime-card",
                app.aime_generation,
                SimpleNamespace(
                    present=True,
                    access_code=valid_code,
                    raw_block=valid_block,
                ),
            )
            self.assertEqual(app.aime_access_code_var.get(), valid_code)
            self.assertEqual(app.aime_block_var.get(), valid_block.hex(" ").upper())

            previous = app.aime_access_code_var.get()
            app._apply_aime_event(
                "aime-card",
                app.aime_generation - 1,
                SimpleNamespace(
                    present=True,
                    access_code="01234567890123456789",
                    raw_block=b"\x00" * 16,
                ),
            )
            self.assertEqual(app.aime_access_code_var.get(), previous)

            app.start_aime_scanning()
            wait_for_tk(root, lambda: app.aime_scanning)
            app.disconnect_aime()
            wait_for_tk(root, lambda: app.aime_handle is None)
            self.assertIn(("stop_polling",), controllers[0].calls)
            self.assertEqual(controllers[0].calls[-1], ("close",))
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_led_commands_are_serial_and_stop_ends_in_black(self) -> None:
        root = self._root()
        monitor = FakeMonitor()
        controllers: list[FakeLedController] = []
        app: ControllerTestWindow | None = None
        main_thread = threading.get_ident()

        def led_factory(port: str, **_kwargs: object) -> FakeLedController:
            controller = FakeLedController(port)
            controllers.append(controller)
            return controller

        try:
            app = ControllerTestWindow(
                root,
                port_provider=lambda: [self._port("COM9")],
                keyboard_provider=lambda: [],
                bus_description_provider=lambda: {"com9": "TenoDX LED"},
                monitor_factory=lambda: monitor,  # type: ignore[arg-type]
                led_controller_factory=led_factory,
                sleeper=lambda _delay: None,
            )
            app.led_port_var.set(next(iter(app.serial_ports_by_label)))
            self.assertTrue(app.connect_led())
            wait_for_tk(root, lambda: app.led_connected)

            app.red_var.set("12")
            app.green_var.set("34")
            app.blue_var.set("56")
            app.show_led_test_color()
            wait_for_tk(
                root,
                lambda: ("set_all", (12, 34, 56)) in controllers[0].calls,
            )
            app.start_chase_test()
            wait_for_tk(
                root,
                lambda: any(call[0] == "chase" for call in controllers[0].calls),
            )
            app.stop_led_test()
            wait_for_tk(
                root,
                lambda: controllers[0].calls[-1] == ("set_all", BLACK),
            )
            stopped_call_count = len(controllers[0].calls)
            deadline = time.monotonic() + 0.38
            while time.monotonic() < deadline:
                root.update()
                time.sleep(0.005)
            self.assertEqual(len(controllers[0].calls), stopped_call_count)
            self.assertTrue(app.led_connected)
            self.assertTrue(controllers[0].thread_ids)
            self.assertNotIn(main_thread, controllers[0].thread_ids)

            app.disconnect_led()
            wait_for_tk(root, lambda: app.led_handle is None)
            self.assertEqual(controllers[0].calls[-2], ("set_all", BLACK))
            self.assertEqual(controllers[0].calls[-1], ("close",))
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_led_failure_does_not_disconnect_aime(self) -> None:
        root = self._root()
        monitor = FakeMonitor()
        aime_controllers: list[FakeAimeController] = []
        led_controllers: list[FakeLedController] = []
        app: ControllerTestWindow | None = None

        def aime_factory(
            port: str, baudrate: int, **_kwargs: object
        ) -> FakeAimeController:
            controller = FakeAimeController(port, baudrate, [])
            aime_controllers.append(controller)
            return controller

        def led_factory(port: str, **_kwargs: object) -> FakeLedController:
            controller = FakeLedController(port, fail_set_all=True)
            led_controllers.append(controller)
            return controller

        try:
            ports = [self._port("COM10"), self._port("COM11")]
            app = ControllerTestWindow(
                root,
                port_provider=lambda: ports,
                keyboard_provider=lambda: [],
                bus_description_provider=lambda: {},
                monitor_factory=lambda: monitor,  # type: ignore[arg-type]
                aime_controller_factory=aime_factory,
                led_controller_factory=led_factory,
                sleeper=lambda _delay: None,
            )
            labels = list(app.serial_ports_by_label)
            app.aime_port_var.set(labels[0])
            app.led_port_var.set(labels[1])
            self.assertTrue(app.connect_aime())
            self.assertTrue(app.connect_led())
            wait_for_tk(root, lambda: app.aime_connected and app.led_connected)
            with patch("tenodx_config.controller_test_ui.messagebox.showerror"):
                app.show_led_test_color()
                wait_for_tk(root, lambda: app.led_handle is None)
            self.assertFalse(app.led_connected)
            self.assertTrue(app.aime_connected)
            self.assertIsNotNone(app.aime_handle)
            self.assertNotIn(("close",), aime_controllers[0].calls)
            self.assertEqual(led_controllers[0].calls[-1], ("close",))
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()

    def test_window_close_stops_aime_and_turns_led_off_before_close(self) -> None:
        root = self._root()
        monitor = FakeMonitor()
        aime_controllers: list[FakeAimeController] = []
        led_controllers: list[FakeLedController] = []
        app: ControllerTestWindow | None = None

        def aime_factory(
            port: str, baudrate: int, **_kwargs: object
        ) -> FakeAimeController:
            controller = FakeAimeController(port, baudrate, [])
            aime_controllers.append(controller)
            return controller

        def led_factory(port: str, **_kwargs: object) -> FakeLedController:
            controller = FakeLedController(port)
            led_controllers.append(controller)
            return controller

        try:
            ports = [self._port("COM12"), self._port("COM13")]
            app = ControllerTestWindow(
                root,
                port_provider=lambda: ports,
                keyboard_provider=lambda: [],
                bus_description_provider=lambda: {},
                monitor_factory=lambda: monitor,  # type: ignore[arg-type]
                aime_controller_factory=aime_factory,
                led_controller_factory=led_factory,
                sleeper=lambda _delay: None,
            )
            labels = list(app.serial_ports_by_label)
            app.aime_port_var.set(labels[0])
            app.led_port_var.set(labels[1])
            app.connect_selected()
            wait_for_tk(root, lambda: app.aime_connected and app.led_connected)

            app.close()
            app.close()
            self.assertEqual(
                aime_controllers[0].calls[-2:],
                [
                    ("stop_polling",),
                    ("close",),
                ],
            )
            self.assertEqual(
                led_controllers[0].calls[-2:],
                [
                    ("set_all", BLACK),
                    ("close",),
                ],
            )
            self.assertTrue(monitor.closed)
        finally:
            if app is not None and not app.closed:
                app.close()
            elif app is None:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
