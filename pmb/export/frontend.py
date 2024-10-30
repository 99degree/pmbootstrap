from pmb.core.context import get_context
from pmb.helpers import logging
import os

from pmb.types import PmbArgs
import pmb.helpers.run
import pmb.helpers.frontend
import pmb.chroot.initfs
import pmb.export
from pmb.core import Chroot, ChrootType


def frontend(args: PmbArgs) -> None:  # FIXME: ARGS_REFACTOR
    config = get_context().config
    # Create the export folder
    target = args.export_folder
    if not os.path.exists(target):
        pmb.helpers.run.user(["mkdir", "-p", target])

    # Rootfs image note
    chroot = Chroot.native()
    rootfs_dir = chroot / "home/pmos/rootfs" / config.device
    if not rootfs_dir.glob("*.img"):
        logging.info(
            "NOTE: To export the rootfs image, run 'pmbootstrap"
            " install' first (without the 'disk' parameter)."
        )

    # Rebuild the initramfs, just to make sure (see #69)
    flavor = pmb.helpers.frontend._parse_flavor(config.device, args.autoinstall)
    if args.autoinstall:
        pmb.chroot.initfs.build(flavor, Chroot(ChrootType.ROOTFS, config.device))

    # Do the export, print all files
    logging.info(f"Export symlinks to: {target}")
    if args.odin_flashable_tar:
        pmb.export.odin(config.device, flavor, target)
    pmb.export.symlinks(flavor, target)
