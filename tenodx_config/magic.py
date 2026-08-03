"""TenoDX Magic protocol support for entering DFU and verifying re-enumeration."""

from __future__ import annotations

import dataclasses
import re
import threading
import time
from collections.abc import Iterable
from typing import Self

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised by CLI dependency error path
    serial = None
    list_ports = None


MAGIC_SEQUENCE = bytes([0x91, 0x3E, 0xED, 0x20, 0x7C, 0x99, 0x58, 0xAC])
MAGIC_RESPONSE_SYNC = 0xAC
MAX_PAYLOAD = 248
LIGHT_MODULE = 0x20
INFO_COMMAND = 0x05
LIGHT_INFO_PARAMS = frozenset((0x01, 0x02))
GLOBAL_MODULE = 0x00
ENTER_DFU_COMMAND = 0x84
DFU_CONFIRM = 0xA5


class MagicError(RuntimeError):
    """Raised for serial or Magic protocol errors."""


@dataclasses.dataclass(frozen=True)
class MagicResponse:
    status: int
    module: int
    command: int
    param: int
    payload: bytes

    @property
    def ok(self) -> bool:
        return self.status == 0


@dataclasses.dataclass(frozen=True)
class MagicPort:
    device: str
    description: str = ""
    usb_serial: str = ""
    hwid: str = ""


def require_pyserial() -> None:
    if serial is None or list_ports is None:
        raise MagicError(
            "缺少 pyserial，请执行: python -m pip install -r requirements.txt"
        )


def build_magic_request(
    module: int, command: int, param: int = 0, payload: bytes = b""
) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise MagicError(f"Magic payload 过长: {len(payload)} > {MAX_PAYLOAD}")
    header = bytes((module & 0xFF, command & 0xFF, param & 0xFF, len(payload)))
    checksum = (sum(header) + sum(payload)) & 0xFF
    return MAGIC_SEQUENCE + header + payload + bytes((checksum,))


def parse_magic_response(frame: bytes) -> MagicResponse:
    if len(frame) < 7 or frame[0] != MAGIC_RESPONSE_SYNC:
        raise MagicError("Magic 响应帧无效。")
    payload_length = frame[5]
    expected_length = 7 + payload_length
    if len(frame) != expected_length:
        raise MagicError(f"Magic 响应长度错误: {len(frame)} != {expected_length}")
    expected_checksum = sum(frame[:-1]) & 0xFF
    if frame[-1] != expected_checksum:
        raise MagicError(
            f"Magic 响应校验和错误: 0x{frame[-1]:02X} != 0x{expected_checksum:02X}"
        )
    return MagicResponse(
        status=frame[1],
        module=frame[2],
        command=frame[3],
        param=frame[4],
        payload=frame[6:-1],
    )


class MagicClient:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0) -> None:
        require_pyserial()
        self.port = port
        self.timeout = timeout
        self._io_lock = threading.Lock()
        try:
            self.serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=0.05,
                write_timeout=timeout,
            )
        except Exception as error:
            raise MagicError(f"无法打开串口 {port}: {error}") from error

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        with self._io_lock:
            if self.serial.is_open:
                self.serial.close()

    def _read_exact(self, length: int, deadline: float) -> bytes:
        data = bytearray()
        while len(data) < length and time.monotonic() < deadline:
            chunk = self.serial.read(length - len(data))
            if chunk:
                data.extend(chunk)
        if len(data) != length:
            raise MagicError(
                f"读取 Magic 响应超时: 需要 {length} 字节，只收到 {len(data)} 字节"
            )
        return bytes(data)

    def _read_response(self) -> MagicResponse:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            first = self.serial.read(1)
            if not first or first[0] != MAGIC_RESPONSE_SYNC:
                continue
            header_tail = self._read_exact(5, deadline)
            header = first + header_tail
            payload_and_checksum = self._read_exact(header[5] + 1, deadline)
            return parse_magic_response(header + payload_and_checksum)
        raise MagicError("等待 Magic 响应超时。")

    def request(
        self, module: int, command: int, param: int = 0, payload: bytes = b""
    ) -> MagicResponse:
        frame = build_magic_request(module, command, param, payload)
        with self._io_lock:
            if not self.serial.is_open:
                raise MagicError(f"串口已关闭: {self.port}")
            self.serial.reset_input_buffer()
            self.serial.write(frame)
            self.serial.flush()
            response = self._read_response()
        if response.module != (module & 0xFF) or response.command != (command & 0xFF):
            raise MagicError(
                f"Magic 响应不匹配: module=0x{response.module:02X}, "
                f"command=0x{response.command:02X}"
            )
        return response


def verify_magic_client(client: MagicClient) -> None:
    response = client.request(LIGHT_MODULE, INFO_COMMAND)
    if not response.ok:
        raise MagicError(f"串口响应了 Magic 协议，但状态为 0x{response.status:02X}。")
    if not LIGHT_INFO_PARAMS.issubset(response.payload):
        raise MagicError("串口响应了 Magic 协议，但不是兼容的 TenoDX Aime/Magic 端口。")


def probe_magic_port(port: MagicPort, timeout: float = 0.6) -> bool:
    try:
        with MagicClient(port.device, timeout=timeout) as client:
            verify_magic_client(client)
        return True
    except (MagicError, OSError):
        return False


def _port_sort_key(port: MagicPort) -> tuple[int, str]:
    match = re.fullmatch(r"COM(\d+)", port.device, re.IGNORECASE)
    return (int(match.group(1)), port.device) if match else (2**31 - 1, port.device)


def list_serial_ports() -> list[MagicPort]:
    require_pyserial()
    ports = [
        MagicPort(
            device=item.device,
            description=item.description or "",
            usb_serial=item.serial_number or "",
            hwid=item.hwid or "",
        )
        for item in list_ports.comports()
    ]
    return sorted(ports, key=_port_sort_key)


def discover_magic_ports(requested_port: str | None = None) -> list[MagicPort]:
    ports = list_serial_ports()
    if requested_port:
        selected = next(
            (
                item
                for item in ports
                if item.device.casefold() == requested_port.casefold()
            ),
            MagicPort(device=requested_port.upper()),
        )
        ports = [selected]
    return [port for port in ports if probe_magic_port(port)]


def send_enter_dfu(port: MagicPort, timeout: float = 1.0) -> None:
    with MagicClient(port.device, timeout=timeout) as client:
        verify_magic_client(client)
        response = client.request(
            GLOBAL_MODULE, ENTER_DFU_COMMAND, 0x00, bytes((DFU_CONFIRM,))
        )
        if not response.ok:
            raise MagicError(f"进入 DFU 命令被拒绝，状态 0x{response.status:02X}。")


def wait_for_magic_return(
    original: MagicPort,
    other_initial_ports: Iterable[MagicPort],
    timeout: float = 30.0,
    interval: float = 0.5,
) -> MagicPort:
    """Wait for the flashed controller, verify Magic, and close the probe port."""
    deadline = time.monotonic() + timeout
    other_devices = {port.device.casefold() for port in other_initial_ports}
    while time.monotonic() < deadline:
        current = list_serial_ports()
        if original.usb_serial:
            candidates = [
                port for port in current if port.usb_serial == original.usb_serial
            ]
        else:
            candidates = [
                port
                for port in current
                if port.device.casefold() == original.device.casefold()
                or port.device.casefold() not in other_devices
            ]
        candidates.sort(
            key=lambda port: (
                port.device.casefold() != original.device.casefold(),
                _port_sort_key(port),
            )
        )
        for candidate in candidates:
            if probe_magic_port(candidate):
                return candidate
        time.sleep(interval)
    raise MagicError(
        f"固件已刷写，但 {timeout:g} 秒内未找到重新枚举且通过 Magic 验证的设备。"
    )
