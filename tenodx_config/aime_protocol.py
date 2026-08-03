"""Pure helpers for the SEGA Aime reader serial protocol.

The protocol uses ``0xE0`` as a frame delimiter and ``0xD0`` as an escape
marker.  Every escaped byte is transmitted as its value minus one.  This
module deliberately contains no serial-port or UI code so it can be tested
and reused independently.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

SYNC = 0xE0
ESCAPE = 0xD0
DEFAULT_ADDRESS = 0x00

CMD_GET_FW_VERSION = 0x30
CMD_GET_HW_VERSION = 0x32
CMD_START_POLLING = 0x40
CMD_STOP_POLLING = 0x41
CMD_CARD_DETECT = 0x42
CMD_READ_BLOCK2 = 0x52

STATUS_OK = 0x00
STATUS_NAMES = {
    0x00: "成功",
    0x01: "卡片错误",
    0x02: "不接受",
    0x03: "无效命令",
    0x04: "无效数据",
    0x05: "校验错误",
    0x06: "内部错误",
    0x07: "无效固件数据",
    0x08: "固件更新成功",
    0x10: "兼容状态 837-15286",
    0x20: "兼容状态 837-15396",
}

MAX_FRAME_LENGTH = 128
CARD_BLOCK_LENGTH = 16
ACCESS_CODE_OFFSET = 6


class AimeProtocolError(RuntimeError):
    """Raised when an Aime frame or successful response is invalid."""


@dataclass(frozen=True)
class AimeResponse:
    """A decoded response frame from an Aime-compatible reader."""

    frame_length: int
    address: int
    sequence: int
    command: int
    status: int
    payload: bytes

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


@dataclass(frozen=True)
class AimeCardPresence:
    """Card-presence information returned by command ``0x42``."""

    present: bool
    card_type: str = ""
    identifier: bytes = b""
    pmm: bytes = b""
    raw_type: int = 0


@dataclass(frozen=True)
class AimeBlock2:
    """The raw 16-byte Block 2 and its optional 20-digit access code."""

    raw_block: bytes
    access_code: str | None

    @property
    def parseable(self) -> bool:
        return self.access_code is not None


def escape_frame_bytes(data: Iterable[int]) -> bytes:
    """Escape decoded frame bytes, including the checksum byte."""

    encoded = bytearray()
    for value in data:
        if not 0 <= value <= 0xFF:
            raise ValueError("frame values must be bytes")
        if value in (SYNC, ESCAPE):
            encoded.extend((ESCAPE, (value - 1) & 0xFF))
        else:
            encoded.append(value)
    return bytes(encoded)


def build_request(
    command: int,
    payload: bytes = b"",
    sequence: int = 0,
    address: int = DEFAULT_ADDRESS,
) -> bytes:
    """Build one request frame accepted by an Aime-compatible reader."""

    for name, value in (
        ("command", command),
        ("sequence", sequence),
        ("address", address),
    ):
        if not 0 <= value <= 0xFF:
            raise ValueError(f"{name} must be a byte")
    if len(payload) > MAX_FRAME_LENGTH - 5:
        raise ValueError("payload is too long")

    frame_length = 5 + len(payload)
    body = (
        bytes(
            (
                frame_length,
                address,
                sequence,
                command,
                len(payload),
            )
        )
        + payload
    )
    checksum = sum(body) & 0xFF
    return bytes((SYNC,)) + escape_frame_bytes(body + bytes((checksum,)))


class AimeResponseParser:
    """Incrementally parse escaped response frames from arbitrary chunks."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def reset(self) -> None:
        self.buffer.clear()

    def feed(self, data: bytes) -> list[AimeResponse]:
        self.buffer.extend(data)
        responses: list[AimeResponse] = []

        while True:
            try:
                sync_index = self.buffer.index(SYNC)
            except ValueError:
                self.buffer.clear()
                return responses

            if sync_index:
                del self.buffer[:sync_index]

            decoded = bytearray()
            raw_index = 1
            expected_decoded_length: int | None = None
            restart = False

            while raw_index < len(self.buffer):
                value = self.buffer[raw_index]
                if value == SYNC:
                    del self.buffer[:raw_index]
                    restart = True
                    break

                if value == ESCAPE:
                    if raw_index + 1 >= len(self.buffer):
                        return responses
                    value = (self.buffer[raw_index + 1] + 1) & 0xFF
                    raw_index += 2
                else:
                    raw_index += 1

                decoded.append(value)
                if len(decoded) == 1:
                    frame_length = decoded[0]
                    if not 6 <= frame_length <= MAX_FRAME_LENGTH:
                        del self.buffer[0]
                        raise AimeProtocolError(f"响应长度无效：{frame_length}")
                    expected_decoded_length = frame_length + 1

                if (
                    expected_decoded_length is not None
                    and len(decoded) == expected_decoded_length
                ):
                    del self.buffer[:raw_index]
                    responses.append(self._decode(bytes(decoded)))
                    break
            else:
                return responses

            if restart:
                continue

    @staticmethod
    def _decode(decoded: bytes) -> AimeResponse:
        frame_length = decoded[0]
        body = decoded[:-1]
        checksum = decoded[-1]
        if len(body) != frame_length:
            raise AimeProtocolError("响应声明长度与实际长度不一致")
        if (sum(body) & 0xFF) != checksum:
            raise AimeProtocolError("响应校验和错误")
        if frame_length < 6:
            raise AimeProtocolError("响应头不完整")

        payload_length = body[5]
        if frame_length != 6 + payload_length:
            raise AimeProtocolError("响应负载长度无效")
        return AimeResponse(
            frame_length=frame_length,
            address=body[1],
            sequence=body[2],
            command=body[3],
            status=body[4],
            payload=body[6:],
        )


def status_error(response: AimeResponse) -> AimeProtocolError:
    name = STATUS_NAMES.get(response.status, "未知状态")
    return AimeProtocolError(
        f"命令 0x{response.command:02X} 失败：{name}（0x{response.status:02X}）"
    )


def require_ok(response: AimeResponse) -> AimeResponse:
    if not response.ok:
        raise status_error(response)
    return response


def parse_card_presence(response: AimeResponse) -> AimeCardPresence:
    """Decode the first card record from a ``0x42`` response."""

    if response.command != CMD_CARD_DETECT:
        raise AimeProtocolError("收到的不是卡片检测响应")
    require_ok(response)
    if not response.payload:
        raise AimeProtocolError("卡片检测响应缺少数量字段")

    count = response.payload[0]
    if count == 0:
        return AimeCardPresence(present=False)
    if len(response.payload) < 3:
        raise AimeProtocolError("卡片检测响应头不完整")

    card_type = response.payload[1]
    identifier_length = response.payload[2]
    card_data = response.payload[3:]
    if len(card_data) < identifier_length:
        raise AimeProtocolError("卡片标识数据不完整")

    if card_type == 0x10:
        if not 1 <= identifier_length <= 10:
            raise AimeProtocolError(f"MIFARE UID 长度无效：{identifier_length}")
        return AimeCardPresence(
            present=True,
            card_type="MIFARE",
            identifier=card_data[:identifier_length],
            raw_type=card_type,
        )

    if card_type == 0x20:
        if identifier_length != 0x10:
            raise AimeProtocolError(f"FeliCa IDm/PMm 长度无效：{identifier_length}")
        return AimeCardPresence(
            present=True,
            card_type="FeliCa",
            identifier=card_data[:8],
            pmm=card_data[8:16],
            raw_type=card_type,
        )

    return AimeCardPresence(
        present=True,
        card_type=f"未知类型 0x{card_type:02X}",
        identifier=card_data[:identifier_length],
        raw_type=card_type,
    )


def decode_access_code(raw_block: bytes) -> str | None:
    """Decode the last ten Block 2 bytes as strict packed BCD.

    Both MIFARE Aime blocks and the firmware's FeliCa-derived blocks use this
    representation.  Invalid nibbles are not guessed or rendered as hex; the
    caller receives ``None`` while retaining the raw block.
    """

    if len(raw_block) != CARD_BLOCK_LENGTH:
        raise AimeProtocolError(
            f"Block 2 长度无效：{len(raw_block)}，应为 {CARD_BLOCK_LENGTH}"
        )

    digits: list[str] = []
    for value in raw_block[ACCESS_CODE_OFFSET:]:
        high = value >> 4
        low = value & 0x0F
        if high > 9 or low > 9:
            return None
        digits.extend((str(high), str(low)))
    return "".join(digits)


def parse_block2(response: AimeResponse) -> AimeBlock2:
    """Decode a successful ``0x52`` response and preserve all raw bytes."""

    if response.command != CMD_READ_BLOCK2:
        raise AimeProtocolError("收到的不是 Block 2 响应")
    require_ok(response)
    raw_block = bytes(response.payload)
    access_code = decode_access_code(raw_block)
    return AimeBlock2(raw_block=raw_block, access_code=access_code)
