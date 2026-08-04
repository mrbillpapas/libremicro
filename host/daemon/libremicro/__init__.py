"""LibreMicro host daemon — the "brains" for the Creator Micro 2 custom firmware.

The device is a thin transport (LED sink + input source); everything interesting lives
here. See docs/DESIGN.md for the split and docs/ROADMAP.md for what's built.
"""
from .color import Palette, mix, parse_hex, to_hex
from .config import Config, ConfigError
from .frame import Frame
from .layout import KEY_N, STATUS_N, UNDERGLOW_N, Layout
from .palettes import BUILTIN
from .transport import Link

__all__ = [
    "BUILTIN", "Config", "ConfigError", "Frame", "KEY_N", "Layout", "Link", "Palette",
    "STATUS_N", "UNDERGLOW_N", "mix", "parse_hex", "to_hex",
]
__version__ = "0.1.0"
