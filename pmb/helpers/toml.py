# Copyright 2024 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

from pmb.meta import Cache
from pmb.helpers.exceptions import NonBugError

try:
    # Python >= 3.11
    from tomllib import load, TOMLDecodeError  # novermin
except ImportError:
    # Python < 3.11
    from tomli import load, TOMLDecodeError  # type:ignore[import-not-found,no-redef,assignment]


@Cache("path")
def load_toml_file(path: Path) -> dict:
    """Read a toml file into a dict and show the path on error."""
    with open(path, mode="rb") as f:
        try:
            return load(f)
        except TOMLDecodeError as e:
            raise NonBugError(f"{path}: {e}")
