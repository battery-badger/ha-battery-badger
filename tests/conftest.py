"""Make the pure ``modes`` module importable without Home Assistant.

Importing ``custom_components.battery_badger`` would run the package
``__init__`` (which imports homeassistant), so put the package directory
itself on the path and import ``modes`` as a top-level module.
"""
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "custom_components" / "battery_badger")
)
