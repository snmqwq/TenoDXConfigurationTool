"""Compose the touch-panel and main-button assets around one shared center."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

SOURCE_CANVAS_SIZE = 1200
TOUCH_SOURCE_SIZE = 1080
TOUCH_SOURCE_OFFSET = (60, 60)
TOUCH_ZONE_COUNT = 34
MAIN_BUTTON_COUNT = 8

_TOUCH_GROUPS = (
    ("A", 8, 45),
    ("B", 8, 45),
    ("C", 2, 180),
    ("D", 8, 45),
    ("E", 8, 45),
)


@dataclass(frozen=True, slots=True)
class OverlayLayer:
    """A cropped RGBA layer positioned in the shared 1200px coordinate space."""

    image: Image.Image
    offset: tuple[int, int]

    @classmethod
    def from_canvas(cls, canvas: Image.Image) -> OverlayLayer:
        bounds = canvas.getchannel("A").getbbox()
        if bounds is None:
            return cls(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (0, 0))
        left, top, right, bottom = bounds
        return cls(canvas.crop(bounds), (left, top))

    def composite_onto(self, canvas: Image.Image) -> None:
        canvas.alpha_composite(self.image, dest=self.offset)


class ControllerRenderer:
    """Render current touch and BTN1-BTN8 states from the supplied PNG assets."""

    def __init__(
        self,
        asset_directory: Path,
        display_size: int = 700,
        background: tuple[int, int, int, int] = (240, 240, 240, 255),
    ) -> None:
        if display_size <= 0:
            raise ValueError("display size must be positive")

        self.asset_directory = asset_directory
        self.display_size = display_size
        self.background = background

        sensor = self._open_rgba("sensor.png", TOUCH_SOURCE_SIZE)
        button_off = self._open_rgba("BUTTON_OFF.png", SOURCE_CANVAS_SIZE)
        button_on = self._open_rgba("BUTTON_ON.png", SOURCE_CANVAS_SIZE)
        if button_off.getchannel("A").tobytes() != button_on.getchannel("A").tobytes():
            raise ValueError("BUTTON_ON/OFF 的透明轮廓必须一致")

        base = Image.new(
            "RGBA",
            (SOURCE_CANVAS_SIZE, SOURCE_CANVAS_SIZE),
            background,
        )
        base.alpha_composite(sensor, dest=TOUCH_SOURCE_OFFSET)

        self.touch_layers = self._build_touch_layers()
        self.button_off_layers = self._build_button_layers(button_off)
        self.button_on_layers = self._build_button_layers(button_on)

        # Keep every layer on the shared 1200x1200 logical canvas.  Resizing
        # only the completed frame avoids seams or center drift between
        # independently resampled layers.
        self.base = base

        if len(self.touch_layers) != TOUCH_ZONE_COUNT:
            raise ValueError("触摸区域素材数量不是 34")
        if len(self.button_on_layers) != MAIN_BUTTON_COUNT:
            raise ValueError("主按键素材数量不是 8")

    def _open_rgba(self, filename: str, expected_size: int) -> Image.Image:
        path = self.asset_directory / filename
        if not path.is_file():
            raise FileNotFoundError(f"缺少测试素材：{path}")
        image = Image.open(path).convert("RGBA")
        if image.size != (expected_size, expected_size):
            raise ValueError(
                f"{filename} 必须是 {expected_size}x{expected_size}，"
                f"当前为 {image.width}x{image.height}"
            )
        return image

    @staticmethod
    def _rotate(source: Image.Image, angle: int) -> Image.Image:
        if angle == 0:
            return source.copy()
        return source.rotate(
            angle,
            resample=Image.Resampling.BICUBIC,
            expand=False,
            fillcolor=(0, 0, 0, 0),
        )

    def _touch_canvas(self, source: Image.Image) -> Image.Image:
        canvas = Image.new(
            "RGBA",
            (SOURCE_CANVAS_SIZE, SOURCE_CANVAS_SIZE),
            (0, 0, 0, 0),
        )
        canvas.alpha_composite(source, dest=TOUCH_SOURCE_OFFSET)
        return canvas

    def _build_touch_layers(self) -> tuple[OverlayLayer, ...]:
        layers: list[OverlayLayer] = []
        for group, count, step in _TOUCH_GROUPS:
            source = self._open_rgba(f"canvas{group}.png", TOUCH_SOURCE_SIZE)
            for index in range(count):
                rotated = self._rotate(source, -(index * step))
                layers.append(OverlayLayer.from_canvas(self._touch_canvas(rotated)))
        return tuple(layers)

    def _build_button_layers(self, source: Image.Image) -> tuple[OverlayLayer, ...]:
        return tuple(
            OverlayLayer.from_canvas(self._rotate(source, -(index * 45)))
            for index in range(MAIN_BUTTON_COUNT)
        )

    def _resize(self, image: Image.Image) -> Image.Image:
        return image.resize(
            (self.display_size, self.display_size),
            Image.Resampling.LANCZOS,
        )

    def render(self, touch_bits: int, button_mask: int) -> Image.Image:
        image = self.base.copy()
        for index, layer in enumerate(self.touch_layers):
            if touch_bits & (1 << index):
                layer.composite_onto(image)
        for index, (off_layer, on_layer) in enumerate(
            zip(self.button_off_layers, self.button_on_layers, strict=True)
        ):
            (on_layer if button_mask & (1 << index) else off_layer).composite_onto(
                image
            )
        return self._resize(image)
