"""Serial transport and high-level operations for an Aime reader."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, NamedTuple, Self

try:
    import serial
except ImportError:  # pragma: no cover - dependency error path
    serial = None

from .aime_protocol import (
    CMD_CARD_DETECT,
    CMD_GET_FW_VERSION,
    CMD_GET_HW_VERSION,
    CMD_READ_BLOCK2,
    CMD_START_POLLING,
    CMD_STOP_POLLING,
    AimeBlock2,
    AimeCardPresence,
    AimeProtocolError,
    AimeResponse,
    AimeResponseParser,
    build_request,
    parse_block2,
    parse_card_presence,
    require_ok,
)

BAUDRATES = (115200, 38400)
SERIAL_OPEN_DELAY_SECONDS = 1.5
SERIAL_TIMEOUT_SECONDS = 0.7
CARD_DETECT_TIMEOUT_SECONDS = 1.5
SERIAL_READ_TIMEOUT_SECONDS = 0.02
SERIAL_WRITE_TIMEOUT_SECONDS = 0.5
READ_IDLE_SECONDS = 0.001
BLOCK2_REQUEST_PAYLOAD = bytes((0x00, 0x00, 0x00, 0x00, 0x02))


class AimeResponseTimeout(AimeProtocolError):  # noqa: N818
    """Raised when no matching Aime response arrives before the deadline."""


class AimeSerialError(AimeProtocolError):
    """Raised when opening, reading, or writing the serial port fails."""


class AimeReaderInfo(NamedTuple):
    """Firmware and hardware payloads returned by a reader probe."""

    firmware: bytes
    hardware: bytes


@dataclass(frozen=True)
class AimeCardResult:
    """UI-friendly aggregate result of card detection and Block 2 reading."""

    present: bool
    access_code: str | None
    raw_block: bytes


def validate_block2_payload(payload: bytes) -> bytes:
    """Validate the request fields used by the firmware's ``0x52`` handler."""

    payload = bytes(payload)
    if len(payload) < 5:
        raise ValueError("Block 2 请求负载至少需要 5 字节")
    if payload[4] != 0x02:
        raise ValueError("Block 2 请求负载的第 5 字节必须为 0x02")
    return payload


class AimeReaderController:
    """Synchronous high-level client for the Aime reader protocol.

    ``serial_factory``, ``sleeper`` and ``clock`` can be injected by tests or
    alternate runtimes.  The default transport is pyserial configured as
    8-N-1 with DTR and RTS asserted, matching the original reader tool.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        *,
        serial_factory: Any | None = None,
        sleeper: Any = time.sleep,
        clock: Any = time.monotonic,
        dtr: bool = True,
        rts: bool = True,
        open_delay: float = SERIAL_OPEN_DELAY_SECONDS,
    ) -> None:
        if baudrate not in BAUDRATES:
            raise ValueError(f"不支持的 Aime 波特率：{baudrate}；仅支持 115200/38400")
        if open_delay < 0:
            raise ValueError("open_delay cannot be negative")
        if serial_factory is None:
            if serial is None:
                raise AimeSerialError(
                    "缺少 pyserial，请执行：python -m pip install -r requirements.txt"
                )
            serial_factory = serial.Serial

        self.port = port
        self.baudrate = baudrate
        self.sequence = 0
        self.parser = AimeResponseParser()
        self._sleep = sleeper
        self._clock = clock
        self._io_lock = threading.Lock()
        self.serial: Any | None = None

        bytesize = serial.EIGHTBITS if serial is not None else 8
        parity = serial.PARITY_NONE if serial is not None else "N"
        stopbits = serial.STOPBITS_ONE if serial is not None else 1
        try:
            self.serial = serial_factory(
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=SERIAL_READ_TIMEOUT_SECONDS,
                write_timeout=SERIAL_WRITE_TIMEOUT_SECONDS,
            )
            self.serial.dtr = dtr
            self.serial.rts = rts
            self._sleep(open_delay)
            self.serial.reset_input_buffer()
        except Exception as error:
            self._close_quietly()
            raise AimeSerialError(
                f"无法初始化 Aime 串口 {port}（{baudrate} baud）：{error}"
            ) from error

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return bool(self.serial is not None and getattr(self.serial, "is_open", True))

    def _close_quietly(self) -> None:
        device = self.serial
        if device is None:
            return
        try:
            if getattr(device, "is_open", True):
                device.close()
        except Exception:
            pass

    def close(self) -> None:
        with self._io_lock:
            self._close_quietly()

    def _ensure_open(self) -> Any:
        if self.serial is None or not getattr(self.serial, "is_open", True):
            raise AimeSerialError(f"Aime 串口已关闭：{self.port}")
        return self.serial

    def command(
        self,
        command: int,
        payload: bytes = b"",
        timeout: float = SERIAL_TIMEOUT_SECONDS,
    ) -> AimeResponse:
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        with self._io_lock:
            device = self._ensure_open()
            sequence = self.sequence
            self.sequence = (self.sequence + 1) & 0xFF
            frame = build_request(command, payload, sequence=sequence)
            self.parser.reset()

            try:
                device.reset_input_buffer()
                written = device.write(frame)
                device.flush()
            except Exception as error:
                raise AimeSerialError(
                    f"写入 Aime 串口 {self.port} 失败：{error}"
                ) from error
            if written != len(frame):
                raise AimeSerialError(
                    f"Aime 串口短写：应发送 {len(frame)} 字节，实际发送 {written} 字节"
                )

            deadline = self._clock() + timeout
            while self._clock() < deadline:
                try:
                    waiting = int(getattr(device, "in_waiting", 0))
                    chunk = device.read(waiting if waiting > 0 else 1)
                except Exception as error:
                    raise AimeSerialError(
                        f"读取 Aime 串口 {self.port} 失败：{error}"
                    ) from error
                if not chunk:
                    self._sleep(READ_IDLE_SECONDS)
                    continue
                for response in self.parser.feed(chunk):
                    if (
                        response.command == command
                        and response.sequence == sequence
                        and response.address == 0
                    ):
                        return response

        raise AimeResponseTimeout(
            f"Aime 命令 0x{command:02X} 响应超时（{timeout:g} 秒）"
        )

    def command_ok(
        self,
        command: int,
        payload: bytes = b"",
        timeout: float = SERIAL_TIMEOUT_SECONDS,
    ) -> AimeResponse:
        return require_ok(self.command(command, payload, timeout))

    def probe(self) -> AimeReaderInfo:
        firmware = self.command_ok(CMD_GET_FW_VERSION).payload
        hardware = self.command_ok(CMD_GET_HW_VERSION).payload
        if not firmware or not hardware:
            raise AimeProtocolError("Aime 版本响应为空")
        return AimeReaderInfo(firmware=firmware, hardware=hardware)

    def start_polling(self) -> None:
        self.command_ok(CMD_START_POLLING)

    def stop_polling(self) -> None:
        self.command_ok(CMD_STOP_POLLING)

    def detect_card(self) -> AimeCardPresence:
        response = self.command(
            CMD_CARD_DETECT,
            timeout=CARD_DETECT_TIMEOUT_SECONDS,
        )
        return parse_card_presence(response)

    def read_block2(
        self,
        payload: bytes = BLOCK2_REQUEST_PAYLOAD,
    ) -> AimeBlock2:
        response = self.command_ok(
            CMD_READ_BLOCK2,
            validate_block2_payload(payload),
        )
        return parse_block2(response)

    def read_card(self) -> AimeCardResult:
        presence = self.detect_card()
        if not presence.present:
            return AimeCardResult(
                present=False,
                access_code=None,
                raw_block=b"",
            )
        block = self.read_block2()
        return AimeCardResult(
            present=True,
            access_code=block.access_code,
            raw_block=block.raw_block,
        )
