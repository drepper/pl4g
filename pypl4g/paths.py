"""Location of the ``share`` directory.

``share`` holds the data files that are shared between compiler implementations,
notably the diagnostic catalog.  It is therefore found without relying on any
particular Python packaging arrangement.
"""

import os
from pathlib import Path


def share_dir() -> Path:
    """Return the directory holding the shared data files.

    The search order is the ``PL4G_SHAREDIR`` environment variable, then the
    ``share`` directory beside the package, then the installed package data.
    """
    override = os.environ.get("PL4G_SHAREDIR")
    if override is not None:
        return Path(override)
    candidate = Path(__file__).resolve().parent.parent / "share"
    if candidate.is_dir():
        return candidate
    from importlib import resources

    return Path(str(resources.files("pypl4g") / "share"))


def share_file(name: str) -> Path:
    """Return the path of the shared data file *name*."""
    return share_dir() / name
