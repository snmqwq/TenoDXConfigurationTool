"""Mai2Touch status-stream parsing used by the realtime test display."""

from __future__ import annotations

FRAME_START = 0x28  # (
FRAME_END = 0x29  # )
FRAME_DATA_LENGTH = 7
FRAME_LENGTH = FRAME_DATA_LENGTH + 2
FRAME_DATA_MASK = 0x1F

VALID_TOUCH_BITS = 34
VALID_TOUCH_MASK = (1 << VALID_TOUCH_BITS) - 1

RSET_COMMAND = b"{RSET}"
STAT_COMMAND = b"{STAT}"

ZONE_NAMES = (
    *(f"A{index}" for index in range(1, 9)),
    *(f"B{index}" for index in range(1, 9)),
    "C1",
    "C2",
    *(f"D{index}" for index in range(1, 9)),
    *(f"E{index}" for index in range(1, 9)),
)


class TouchFrameParser:
    """Parse fixed `(xxxxxxx)` Mai2Touch frames from an arbitrary byte stream."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def reset(self) -> None:
        self.buffer.clear()

    def feed(self, data: bytes) -> list[int]:
        self.buffer.extend(data)
        states: list[int] = []

        while True:
            try:
                start_index = self.buffer.index(FRAME_START)
            except ValueError:
                self.buffer.clear()
                break

            if start_index:
                del self.buffer[:start_index]
            if len(self.buffer) < FRAME_LENGTH:
                break

            candidate = self.buffer[:FRAME_LENGTH]
            frame_data = candidate[1:-1]
            if candidate[-1] != FRAME_END or any(
                value > FRAME_DATA_MASK for value in frame_data
            ):
                del self.buffer[0]
                continue

            del self.buffer[:FRAME_LENGTH]
            touch_bits = 0
            for chunk_index, value in enumerate(frame_data):
                touch_bits |= value << (chunk_index * 5)
            states.append(touch_bits & VALID_TOUCH_MASK)

        return states


def encode_touch_frame(touch_bits: int) -> bytes:
    """Encode a state for parser tests and protocol diagnostics."""

    if touch_bits < 0 or touch_bits > VALID_TOUCH_MASK:
        raise ValueError("touch bits must fit the 34 valid Mai2Touch bits")

    frame = bytearray((FRAME_START,))
    remaining = touch_bits
    for _ in range(FRAME_DATA_LENGTH):
        frame.append(remaining & FRAME_DATA_MASK)
        remaining >>= 5
    frame.append(FRAME_END)
    return bytes(frame)
