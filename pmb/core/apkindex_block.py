# Copyright 2024 Stefan Hansson
# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import dataclass

from pmb.core.arch import Arch


@dataclass
class ApkindexBlock:
    """
    "depends" is not set for packages without any dependencies, e.g. ``musl``.

    "timestamp" and "origin" are not set for virtual packages (#1273).
    We use that information to skip these virtual packages in parse().
    """

    arch: Arch
    depends: list[str]
    origin: str | None
    pkgname: str
    provides: list[str]
    provider_priority: int | None
    timestamp: str | None
    version: str
