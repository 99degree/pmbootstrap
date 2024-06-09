# Copyright 2024 Caleb Connolly
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from pmb import commands
import pmb.chroot

class Shutdown(commands.Command):
    def __init__(self):
        pass

    def run(self):
        pmb.chroot.shutdown()

