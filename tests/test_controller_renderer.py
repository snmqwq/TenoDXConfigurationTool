from __future__ import annotations

import unittest
from pathlib import Path

try:
    from tenodx_config.controller_renderer import ControllerRenderer
except ModuleNotFoundError as error:
    if error.name != "PIL":
        raise
    ControllerRenderer = None  # type: ignore[misc,assignment]


ASSET_DIRECTORY = Path(__file__).resolve().parent.parent / "images"


@unittest.skipIf(ControllerRenderer is None, "Pillow is not installed")
class ControllerRendererTests(unittest.TestCase):
    def test_assets_share_one_center_and_generate_all_layers(self) -> None:
        assert ControllerRenderer is not None
        renderer = ControllerRenderer(ASSET_DIRECTORY, display_size=320)
        self.assertEqual(renderer.base.size, (1200, 1200))
        self.assertEqual(len(renderer.touch_layers), 34)
        self.assertEqual(len(renderer.button_off_layers), 8)
        self.assertEqual(len(renderer.button_on_layers), 8)

        layers = (
            *renderer.touch_layers,
            *renderer.button_off_layers,
            *renderer.button_on_layers,
        )
        for layer in layers:
            left, top = layer.offset
            self.assertGreaterEqual(left, 0)
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(left + layer.image.width, 1200)
            self.assertLessEqual(top + layer.image.height, 1200)

        cached_bytes = sum(
            layer.image.width * layer.image.height * 4 for layer in layers
        )
        self.assertLess(cached_bytes, 20 * 1024 * 1024)

    def test_touch_and_button_states_change_the_composite(self) -> None:
        assert ControllerRenderer is not None
        renderer = ControllerRenderer(ASSET_DIRECTORY, display_size=320)
        released = renderer.render(0, 0)
        active = renderer.render((1 << 0) | (1 << 33), (1 << 0) | (1 << 7))
        self.assertEqual(released.size, (320, 320))
        self.assertEqual(active.size, released.size)
        self.assertNotEqual(active.tobytes(), released.tobytes())


if __name__ == "__main__":
    unittest.main()
