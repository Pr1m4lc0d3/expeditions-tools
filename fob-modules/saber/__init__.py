"""Tools for reading, authoring and checking Expeditions map data.

    from expeditions import dds, terrain, project, inventory

Everything here was derived by driving the real editor and reading the real
game data on 2026-09-13. Where a rule looks arbitrary, it is written down
because getting it wrong produced a failure that reported nothing.
"""
from . import dds, inventory, project, terrain  # noqa: F401

__all__ = ["dds", "terrain", "project", "inventory"]
