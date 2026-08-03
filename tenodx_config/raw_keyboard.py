"""Device-specific Windows keyboard input for the controller test screen.

The module deliberately uses Raw Input device paths as identities.  A VID/PID
pair is useful to a person choosing a device, but it is not unique when two
identical controllers are connected at the same time.
"""

from __future__ import annotations

import ctypes
import os
import queue
import re
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Literal

IS_WINDOWS = sys.platform == "win32"

RIM_TYPE_KEYBOARD = 1
RIDI_DEVICE_NAME = 0x20000007
RID_INPUT = 0x10000003
RIDEV_INPUT_SINK = 0x00000100
RIDEV_DEVICE_NOTIFY = 0x00002000
WM_INPUT = 0x00FF
WM_INPUT_DEVICE_CHANGE = 0x00FE
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
GIDC_ARRIVAL = 1
GIDC_REMOVAL = 2
RI_KEY_BREAK = 0x0001
RI_KEY_E0 = 0x0002
UINT_ERROR = 0xFFFFFFFF
CR_SUCCESS = 0x00000000
CR_BUFFER_SMALL = 0x0000001A
DEVPROP_TYPE_STRING = 0x00000012
DIGCF_PRESENT = 0x00000002
SPDRP_FRIENDLY_NAME = 0x0000000C
ERROR_NO_MORE_ITEMS = 259

NO_BUS_DESCRIPTION = "描述未报告"
NOT_REPORTED = "未报告"


if IS_WINDOWS:
    _UlongPtr = wintypes.WPARAM
    _Lresult = ctypes.c_ssize_t
    _WindowProc = ctypes.WINFUNCTYPE(
        _Lresult,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class _RawInputDevice(ctypes.Structure):
        _fields_ = (
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD),
            ("hwndTarget", wintypes.HWND),
        )

    class _RawInputDeviceList(ctypes.Structure):
        _fields_ = (
            ("hDevice", wintypes.HANDLE),
            ("dwType", wintypes.DWORD),
        )

    class _RawInputHeader(ctypes.Structure):
        _fields_ = (
            ("dwType", wintypes.DWORD),
            ("dwSize", wintypes.DWORD),
            ("hDevice", wintypes.HANDLE),
            ("wParam", _UlongPtr),
        )

    class _RawKeyboard(ctypes.Structure):
        _fields_ = (
            ("MakeCode", wintypes.USHORT),
            ("Flags", wintypes.USHORT),
            ("Reserved", wintypes.USHORT),
            ("VKey", wintypes.USHORT),
            ("Message", wintypes.UINT),
            ("ExtraInformation", wintypes.ULONG),
        )

    class _RawInputUnion(ctypes.Union):
        _fields_ = (("keyboard", _RawKeyboard),)

    class _RawInput(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = (
            ("header", _RawInputHeader),
            ("data", _RawInputUnion),
        )

    class _WindowClassEx(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WindowProc),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HANDLE),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HANDLE),
        )

    class _Guid(ctypes.Structure):
        _fields_ = (
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        )

    class _DevicePropertyKey(ctypes.Structure):
        _fields_ = (("fmtid", _Guid), ("pid", wintypes.DWORD))

    class _DeviceInfoData(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("ClassGuid", _Guid),
            ("DevInst", wintypes.DWORD),
            ("Reserved", _UlongPtr),
        )

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)
    _setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

    _user32.GetRawInputDeviceList.argtypes = (
        ctypes.POINTER(_RawInputDeviceList),
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    )
    _user32.GetRawInputDeviceList.restype = wintypes.UINT
    _user32.GetRawInputDeviceInfoW.argtypes = (
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
    )
    _user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
    _user32.GetRawInputData.argtypes = (
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    )
    _user32.GetRawInputData.restype = wintypes.UINT
    _user32.RegisterRawInputDevices.argtypes = (
        ctypes.POINTER(_RawInputDevice),
        wintypes.UINT,
        wintypes.UINT,
    )
    _user32.RegisterRawInputDevices.restype = wintypes.BOOL
    _user32.RegisterClassExW.argtypes = (ctypes.POINTER(_WindowClassEx),)
    _user32.RegisterClassExW.restype = wintypes.ATOM
    _user32.CreateWindowExW.argtypes = (
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    )
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DefWindowProcW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    _user32.DefWindowProcW.restype = _Lresult
    _user32.PostMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.DestroyWindow.argtypes = (wintypes.HWND,)
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.GetMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    )
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    _user32.TranslateMessage.restype = wintypes.BOOL
    _user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    _user32.DispatchMessageW.restype = _Lresult
    _user32.PostQuitMessage.argtypes = (ctypes.c_int,)
    _user32.PostQuitMessage.restype = None
    _kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    _kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    _cfgmgr32.CM_Locate_DevNodeW.argtypes = (
        ctypes.POINTER(wintypes.ULONG),
        wintypes.LPWSTR,
        wintypes.ULONG,
    )
    _cfgmgr32.CM_Locate_DevNodeW.restype = wintypes.ULONG
    _cfgmgr32.CM_Get_Parent.argtypes = (
        ctypes.POINTER(wintypes.ULONG),
        wintypes.ULONG,
        wintypes.ULONG,
    )
    _cfgmgr32.CM_Get_Parent.restype = wintypes.ULONG
    _cfgmgr32.CM_Get_DevNode_PropertyW.argtypes = (
        wintypes.ULONG,
        ctypes.POINTER(_DevicePropertyKey),
        ctypes.POINTER(wintypes.ULONG),
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.ULONG),
        wintypes.ULONG,
    )
    _cfgmgr32.CM_Get_DevNode_PropertyW.restype = wintypes.ULONG
    _setupapi.SetupDiGetClassDevsW.argtypes = (
        ctypes.POINTER(_Guid),
        wintypes.LPCWSTR,
        wintypes.HWND,
        wintypes.DWORD,
    )
    _setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    _setupapi.SetupDiEnumDeviceInfo.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(_DeviceInfoData),
    )
    _setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
    _setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_DeviceInfoData),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    _setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
    _setupapi.SetupDiDestroyDeviceInfoList.argtypes = (wintypes.HANDLE,)
    _setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    # DEVPKEY_Device_BusReportedDeviceDesc
    _BUS_REPORTED_DESCRIPTION_KEY = _DevicePropertyKey(
        _Guid(
            0x540B947E,
            0x8B40,
            0x45BC,
            (wintypes.BYTE * 8)(
                0xA8,
                0xA2,
                0x6A,
                0x0B,
                0x89,
                0x4C,
                0xBD,
                0xA2,
            ),
        ),
        4,
    )
    _PORTS_CLASS_GUID = _Guid(
        0x4D36E978,
        0xE325,
        0x11CE,
        (wintypes.BYTE * 8)(
            0xBF,
            0xC1,
            0x08,
            0x00,
            0x2B,
            0xE1,
            0x03,
            0x18,
        ),
    )


@dataclass(frozen=True, slots=True)
class RawKeyboardDevice:
    """One keyboard collection, uniquely identified by its Raw Input path."""

    path: str
    bus_description: str | None
    vid: int | None
    pid: int | None
    interface: str | None
    instance_tail: str

    @property
    def label(self) -> str:
        """Stable label for a combobox; selection identity remains ``path``."""

        return keyboard_device_label(self.path, None, self.bus_description)

    def display_label(self, index: int | None = None) -> str:
        return keyboard_device_label(self.path, index, self.bus_description)


@dataclass(frozen=True, slots=True)
class RawKeyboardEvent:
    """A selected-keyboard event or a Raw Input hot-plug notification."""

    kind: Literal["key", "device-change"]
    device_path: str | None = None
    scan_code: int = 0
    is_pressed: bool = False
    is_extended: bool = False
    change: Literal["arrival", "removal", "unknown"] | None = None


def _raw_path_to_instance_id(path: str) -> str | None:
    value = path.strip()
    for prefix in ("\\\\?\\", "\\??\\"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    segments = value.split("#")
    if len(segments) < 3 or not all(segments[:3]):
        return None
    return "\\".join(segments[:3])


def is_hid_keyboard_path(path: str) -> bool:
    """Return whether a Raw Input path belongs to the HID enumerator."""

    instance_id = _raw_path_to_instance_id(path)
    return bool(instance_id and instance_id.casefold().startswith("hid\\"))


def _path_identity(path: str) -> tuple[int | None, int | None, str | None, str]:
    upper = path.upper()
    vid_match = re.search(r"(?:^|[&#\\])VID_([0-9A-F]{4})(?:[&#\\]|$)", upper)
    pid_match = re.search(r"(?:^|[&#\\])PID_([0-9A-F]{4})(?:[&#\\]|$)", upper)
    interface_match = re.search(r"(?:^|[&#\\])MI_([0-9A-F]{2})(?:[&#\\]|$)", upper)
    segments = path.split("#")
    instance_tail = segments[2].strip() if len(segments) > 2 else ""
    return (
        int(vid_match.group(1), 16) if vid_match else None,
        int(pid_match.group(1), 16) if pid_match else None,
        f"MI_{interface_match.group(1)}" if interface_match else None,
        instance_tail or NOT_REPORTED,
    )


def keyboard_device_label(
    path: str,
    index: int | None,
    bus_description: str | None,
) -> str:
    """Build a label that always exposes every requested identity field."""

    vid, pid, interface, instance_tail = _path_identity(path)
    prefix = f"键盘 {index} | " if index is not None else ""
    description_text = (bus_description or "").strip() or NO_BUS_DESCRIPTION
    vid_text = f"{vid:04X}" if vid is not None else NOT_REPORTED
    pid_text = f"{pid:04X}" if pid is not None else NOT_REPORTED
    interface_text = interface or f"MI_{NOT_REPORTED}"
    return (
        f"{prefix}{description_text} | VID {vid_text} | PID {pid_text} | "
        f"{interface_text} | 实例 {instance_tail}"
    )


def make_keyboard_device(
    path: str,
    bus_description: str | None = None,
) -> RawKeyboardDevice:
    vid, pid, interface, instance_tail = _path_identity(path)
    return RawKeyboardDevice(
        path=path,
        bus_description=bus_description,
        vid=vid,
        pid=pid,
        interface=interface,
        instance_tail=instance_tail,
    )


def _raw_device_name(device_handle: int) -> str:
    if not IS_WINDOWS:
        return ""
    length = wintypes.UINT(0)
    result = _user32.GetRawInputDeviceInfoW(
        device_handle,
        RIDI_DEVICE_NAME,
        None,
        ctypes.byref(length),
    )
    if result == UINT_ERROR or length.value == 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length.value + 1)
    result = _user32.GetRawInputDeviceInfoW(
        device_handle,
        RIDI_DEVICE_NAME,
        buffer,
        ctypes.byref(length),
    )
    return "" if result == UINT_ERROR else buffer.value


def list_raw_keyboard_names() -> list[str]:
    """Enumerate HID keyboard Raw Input paths in stable order."""

    if not IS_WINDOWS:
        return []
    count = wintypes.UINT(0)
    result = _user32.GetRawInputDeviceList(
        None,
        ctypes.byref(count),
        ctypes.sizeof(_RawInputDeviceList),
    )
    if result == UINT_ERROR or count.value == 0:
        return []

    devices = (_RawInputDeviceList * count.value)()
    result = _user32.GetRawInputDeviceList(
        devices,
        ctypes.byref(count),
        ctypes.sizeof(_RawInputDeviceList),
    )
    if result == UINT_ERROR:
        return []

    by_key: dict[str, str] = {}
    for item in devices[:result]:
        if item.dwType != RIM_TYPE_KEYBOARD:
            continue
        path = _raw_device_name(item.hDevice)
        if path and is_hid_keyboard_path(path):
            by_key.setdefault(path.casefold(), path)
    return sorted(by_key.values(), key=str.casefold)


def _devnode_string_property(devnode: int) -> str | None:
    if not IS_WINDOWS:
        return None
    property_type = wintypes.ULONG(0)
    size = wintypes.ULONG(0)
    result = _cfgmgr32.CM_Get_DevNode_PropertyW(
        devnode,
        ctypes.byref(_BUS_REPORTED_DESCRIPTION_KEY),
        ctypes.byref(property_type),
        None,
        ctypes.byref(size),
        0,
    )
    if result not in (CR_SUCCESS, CR_BUFFER_SMALL) or size.value < 2:
        return None

    character_count = size.value // ctypes.sizeof(ctypes.c_wchar) + 1
    buffer = ctypes.create_unicode_buffer(character_count)
    result = _cfgmgr32.CM_Get_DevNode_PropertyW(
        devnode,
        ctypes.byref(_BUS_REPORTED_DESCRIPTION_KEY),
        ctypes.byref(property_type),
        buffer,
        ctypes.byref(size),
        0,
    )
    if result != CR_SUCCESS or property_type.value != DEVPROP_TYPE_STRING:
        return None
    value = buffer.value.strip()
    return value or None


def bus_reported_device_description(path: str) -> str | None:
    """Read the bus-reported description from the HID node or its parents."""

    if not IS_WINDOWS:
        return None
    instance_id = _raw_path_to_instance_id(path)
    if not instance_id:
        return None

    devnode = wintypes.ULONG(0)
    result = _cfgmgr32.CM_Locate_DevNodeW(
        ctypes.byref(devnode),
        instance_id,
        0,
    )
    if result != CR_SUCCESS:
        return None

    # HID class nodes commonly store this property on the immediate USB
    # interface parent.  A third lookup also covers a composite device node,
    # without walking as far as a host controller or root hub.
    current = devnode
    for _depth in range(3):
        description = _devnode_string_property(current.value)
        if description:
            return description
        parent = wintypes.ULONG(0)
        result = _cfgmgr32.CM_Get_Parent(
            ctypes.byref(parent),
            current.value,
            0,
        )
        if result != CR_SUCCESS:
            break
        current = parent
    return None


def _setupapi_registry_string(
    device_info_set: int,
    device_info: object,
    property_code: int,
) -> str | None:
    if not IS_WINDOWS:
        return None
    buffer = ctypes.create_unicode_buffer(512)
    property_type = wintypes.DWORD(0)
    required = wintypes.DWORD(0)
    success = _setupapi.SetupDiGetDeviceRegistryPropertyW(
        device_info_set,
        ctypes.byref(device_info),
        property_code,
        ctypes.byref(property_type),
        buffer,
        ctypes.sizeof(buffer),
        ctypes.byref(required),
    )
    if not success:
        return None
    value = buffer.value.strip()
    return value or None


def list_serial_bus_descriptions() -> dict[str, str]:
    """Map case-folded COM names to their bus-reported descriptions."""

    if not IS_WINDOWS:
        return {}
    device_info_set = _setupapi.SetupDiGetClassDevsW(
        ctypes.byref(_PORTS_CLASS_GUID),
        None,
        None,
        DIGCF_PRESENT,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if not device_info_set or device_info_set == invalid_handle:
        return {}

    descriptions: dict[str, str] = {}
    try:
        index = 0
        while True:
            device_info = _DeviceInfoData(cbSize=ctypes.sizeof(_DeviceInfoData))
            success = _setupapi.SetupDiEnumDeviceInfo(
                device_info_set,
                index,
                ctypes.byref(device_info),
            )
            if not success:
                if ctypes.get_last_error() == ERROR_NO_MORE_ITEMS:
                    break
                index += 1
                continue
            index += 1

            friendly_name = _setupapi_registry_string(
                device_info_set,
                device_info,
                SPDRP_FRIENDLY_NAME,
            )
            if not friendly_name:
                continue
            ports = re.findall(r"\bCOM\d+\b", friendly_name, re.IGNORECASE)
            if not ports:
                continue

            current = wintypes.ULONG(device_info.DevInst)
            description: str | None = None
            for _depth in range(3):
                description = _devnode_string_property(current.value)
                if description:
                    break
                parent = wintypes.ULONG(0)
                result = _cfgmgr32.CM_Get_Parent(
                    ctypes.byref(parent),
                    current.value,
                    0,
                )
                if result != CR_SUCCESS:
                    break
                current = parent
            if description:
                for port in ports:
                    descriptions[port.casefold()] = description
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(device_info_set)
    return descriptions


def enumerate_raw_keyboards() -> list[RawKeyboardDevice]:
    """Return all current HID keyboards with their display metadata."""

    return [
        make_keyboard_device(path, bus_reported_device_description(path))
        for path in list_raw_keyboard_names()
    ]


def list_raw_keyboard_devices() -> list[RawKeyboardDevice]:
    """GUI-facing alias returning devices with ``path`` and ``label``."""

    return enumerate_raw_keyboards()


class RawKeyboardMonitor:
    """Receive keys only from one explicitly selected Raw Input device path."""

    def __init__(self, *, start: bool = True) -> None:
        self.events: queue.SimpleQueue[RawKeyboardEvent] = queue.SimpleQueue()
        self._target_path: str | None = None
        self._target_key: str | None = None
        self._target_lock = threading.Lock()
        self._device_name_cache: dict[int, str] = {}
        self._ready = threading.Event()
        self._error: str | None = None
        self._window: int | None = None
        self._thread: threading.Thread | None = None
        self._closing = False

        if not start:
            self._ready.set()
            return
        if not IS_WINDOWS:
            self._error = "HID 键盘直读仅支持 Windows"
            self._ready.set()
            return

        self._thread = threading.Thread(
            target=self._thread_main,
            name="TenoDXRawKeyboard",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(2.0):
            self._error = "初始化 HID 键盘监听超时"

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def target_path(self) -> str | None:
        with self._target_lock:
            return self._target_path

    @property
    def is_alive(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def set_target(self, path: str | None) -> None:
        """Select exactly one full Raw Input path; ``None`` disables keys."""

        target = path.strip() if path else None
        with self._target_lock:
            self._target_path = target
            self._target_key = target.casefold() if target else None
        self.clear_events()

    def clear_target(self) -> None:
        self.set_target(None)

    def accepts_device_path(self, path: str) -> bool:
        """Check the same full-path predicate used for incoming key reports."""

        with self._target_lock:
            return self._target_key is not None and path.casefold() == self._target_key

    def clear_events(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return

    def target_is_present(self) -> bool:
        target = self.target_path
        if target is None:
            return False
        current = {path.casefold() for path in list_raw_keyboard_names()}
        return target.casefold() in current

    def close(self) -> None:
        self._closing = True
        window = self._window
        if IS_WINDOWS and window:
            _user32.PostMessageW(window, WM_CLOSE, 0, 0)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _thread_main(self) -> None:
        try:
            instance = _kernel32.GetModuleHandleW(None)
            class_name = f"TenoDXRawKeyboard_{os.getpid()}_{id(self):x}"
            self._window_proc_callback = _WindowProc(self._window_proc)
            window_class = _WindowClassEx(
                cbSize=ctypes.sizeof(_WindowClassEx),
                style=0,
                lpfnWndProc=self._window_proc_callback,
                cbClsExtra=0,
                cbWndExtra=0,
                hInstance=instance,
                hIcon=None,
                hCursor=None,
                hbrBackground=None,
                lpszMenuName=None,
                lpszClassName=class_name,
                hIconSm=None,
            )
            if not _user32.RegisterClassExW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())

            window = _user32.CreateWindowExW(
                0,
                class_name,
                class_name,
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                instance,
                None,
            )
            if not window:
                raise ctypes.WinError(ctypes.get_last_error())
            self._window = window

            registration = _RawInputDevice(
                usUsagePage=0x01,
                usUsage=0x06,
                dwFlags=RIDEV_INPUT_SINK | RIDEV_DEVICE_NOTIFY,
                hwndTarget=window,
            )
            if not _user32.RegisterRawInputDevices(
                ctypes.byref(registration),
                1,
                ctypes.sizeof(_RawInputDevice),
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            # Clear a possible initialization-timeout report if the listener
            # finished starting shortly after the caller's wait expired.
            self._error = None
            self._ready.set()
            if self._closing:
                _user32.DestroyWindow(window)
                return
            message = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:  # thread boundary: preserve useful details
            self._error = f"初始化 HID 键盘监听失败：{error}"
            self._ready.set()
        finally:
            self._window = None
            if not self._closing and self._error is None:
                self._error = "HID 键盘监听线程意外停止"

    def _window_proc(
        self,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        try:
            if message == WM_INPUT:
                self._handle_raw_input(lparam)
                return 0
            if message == WM_INPUT_DEVICE_CHANGE:
                self._handle_device_change(wparam, lparam)
                return 0
            if message == WM_CLOSE:
                _user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
        except Exception:
            # Never allow a Python exception to cross the Win32 callback.
            pass
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _handle_device_change(self, change_code: int, device_handle: int) -> None:
        handle = int(device_handle or 0)
        path = self._device_name_cache.get(handle, "")
        if not path:
            path = _raw_device_name(handle)

        if change_code == GIDC_ARRIVAL:
            change: Literal["arrival", "removal", "unknown"] = "arrival"
            if path:
                self._device_name_cache[handle] = path
        elif change_code == GIDC_REMOVAL:
            change = "removal"
            self._device_name_cache.pop(handle, None)
        else:
            change = "unknown"
            self._device_name_cache.clear()

        self.events.put(
            RawKeyboardEvent(
                kind="device-change",
                device_path=path or None,
                change=change,
            )
        )

    def _handle_raw_input(self, raw_input_handle: int) -> None:
        size = wintypes.UINT(0)
        result = _user32.GetRawInputData(
            raw_input_handle,
            RID_INPUT,
            None,
            ctypes.byref(size),
            ctypes.sizeof(_RawInputHeader),
        )
        if result == UINT_ERROR or size.value < ctypes.sizeof(_RawInput):
            return

        buffer = ctypes.create_string_buffer(size.value)
        result = _user32.GetRawInputData(
            raw_input_handle,
            RID_INPUT,
            buffer,
            ctypes.byref(size),
            ctypes.sizeof(_RawInputHeader),
        )
        if result == UINT_ERROR or result < ctypes.sizeof(_RawInput):
            return

        raw = ctypes.cast(buffer, ctypes.POINTER(_RawInput)).contents
        if raw.header.dwType != RIM_TYPE_KEYBOARD:
            return

        handle = int(raw.header.hDevice or 0)
        path = self._device_name_cache.get(handle)
        if path is None:
            path = _raw_device_name(handle)
            self._device_name_cache[handle] = path
        if not path or not self.accepts_device_path(path):
            return

        self.events.put(
            RawKeyboardEvent(
                kind="key",
                device_path=path,
                scan_code=int(raw.keyboard.MakeCode),
                is_pressed=not bool(raw.keyboard.Flags & RI_KEY_BREAK),
                is_extended=bool(raw.keyboard.Flags & RI_KEY_E0),
            )
        )
