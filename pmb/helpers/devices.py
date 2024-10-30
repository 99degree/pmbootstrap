# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
from pmb.core.pkgrepo import pkgrepo_glob_one, pkgrepo_iglob


def find_path(codename: str, file: str = "") -> Path | None:
    """Find path to device APKBUILD under `device/*/device-`.

    :param codename: device codename
    :param file: file to look for (e.g. APKBUILD or deviceinfo), may be empty
    :returns: path to APKBUILD
    """
    g = pkgrepo_glob_one(f"device/*/device-{codename}/{file}")
    if not g:
        return None

    return g


def list_codenames(vendor=None, archived=True):
    """Get all devices, for which aports are available.

    :param vendor: vendor name to choose devices from, or None for all vendors
    :param archived: include archived devices
    :returns: ["first-device", "second-device", ...]
    """
    ret = []
    for path in pkgrepo_iglob("device/*/device-*"):
        if not archived and "archived" in path.parts:
            continue
        device = os.path.basename(path).split("-", 1)[1]
        if (vendor is None) or device.startswith(vendor + "-"):
            ret.append(device)
    return ret


def list_vendors() -> set[str]:
    """Get all device vendors, for which aports are available.

    :returns: {"vendor1", "vendor2", ...}
    """
    ret = set()
    for path in pkgrepo_iglob("device/*/device-*"):
        vendor = path.name.split("-", 2)[1]
        ret.add(vendor)
    return ret
