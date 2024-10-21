# Copyright 2024 Stefan Hansson
# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import dataclass

from pmb.core.arch import Arch


@dataclass
class ApkindexBlock:
    """
    "timestamp" and "origin" are not set for virtual packages (#1273).
    We use that information to skip these virtual packages in parse().
    """

    #: the architecture of the package
    arch: Arch
    #: dependencies for the package
    depends: list[str]
    #: the origin name of the package
    origin: str | None
    #: package name
    pkgname: str
    #: what this package provides
    provides: list[str]
    #: provider priority for the package
    provider_priority: int | None
    #: unix timestamp of the package build date/time
    timestamp: str | None
    #: package version
    version: str
