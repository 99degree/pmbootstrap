# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from typing import Dict
from pmb.parse.deviceinfo import Deviceinfo
from pmb.types import PmbArgs
import pmb.flasher
import pmb.chroot.initfs
import pmb.helpers.args


def check_partition_blacklist(deviceinfo: Deviceinfo, key, value):
    if not key.startswith("$PARTITION_"):
        return

    name = deviceinfo.name
    if value in (deviceinfo.partition_blacklist or "").split(","):
        raise RuntimeError("'" + value + "'" + " partition is blacklisted " +
                           "from being flashed! See the " + name + " device " +
                           "wiki page for more information.")


def run(deviceinfo: Deviceinfo, method: str, action: str, flavor=None):
    pmb.flasher.init(deviceinfo.codename, method)

    # Verify action
    cfg = pmb.config.flashers[method]
    if not isinstance(cfg["actions"], dict):
        raise TypeError(f"Flashers misconfigured! {method} key 'actions' should be a dictionary")
    if action not in cfg["actions"]:
        raise RuntimeError("action " + action + " is not"
                           " configured for method " + method + "!"
                           " You can use the '--method' option to specify a"
                           " different flash method. See also:"
                           " <https://wiki.postmarketos.org/wiki/"
                           "Deviceinfo_flash_methods>")

    # Variable setup
    # FIXME: handle argparsing and pass in only the args we need.
    args = pmb.helpers.args.please_i_really_need_args()
    fvars = pmb.flasher.variables(args, flavor, method)

    # vbmeta flasher requires vbmeta partition to be explicitly specified
    if action == "flash_vbmeta" and not fvars["$PARTITION_VBMETA"]:
        raise RuntimeError("Your device does not have 'vbmeta' partition"
                           " specified; set"
                           " 'deviceinfo_flash_fastboot_partition_vbmeta'"
                           " or 'deviceinfo_flash_heimdall_partition_vbmeta'"
                           " in deviceinfo file. See also:"
                           " <https://wiki.postmarketos.org/wiki/"
                           "Deviceinfo_reference>")

    # dtbo flasher requires dtbo partition to be explicitly specified
    if action == "flash_dtbo" and not fvars["$PARTITION_DTBO"]:
        raise RuntimeError("Your device does not have 'dtbo' partition"
                           " specified; set"
                           " 'deviceinfo_flash_fastboot_partition_dtbo'"
                           " in deviceinfo file. See also:"
                           " <https://wiki.postmarketos.org/wiki/"
                           "Deviceinfo_reference>")

    if args.no_reboot and ("flash" not in action or method != "heimdall-bootimg"):
        raise RuntimeError("The '--no-reboot' option is only"
                           " supported when flashing with heimall-bootimg.")

    if args.resume and ("flash" not in action or method != "heimdall-bootimg"):
        raise RuntimeError("The '--resume' option is only"
                           " supported when flashing with heimall-bootimg.")

    # Run the commands of each action
    for command in cfg["actions"][action]:
        # Variable replacement
        for key, value in fvars.items():
            for i in range(len(command)):
                if key in command[i]:
                    if value is None:
                        raise RuntimeError(f"Variable {key} found in action"
                                           f" {action} for method {method},"
                                           " but the value for this variable"
                                           " is None! Is that missing in your"
                                           " deviceinfo?")
                    check_partition_blacklist(deviceinfo, key, value)
                    command[i] = command[i].replace(key, value)

        # Remove empty strings
        command = [x for x in command if x != '']
        # Run the action
        pmb.chroot.root(command, output="interactive")
