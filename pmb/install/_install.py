# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.core.arch import Arch
from pmb.helpers import logging
import os
import re
import glob
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

import pmb.build
import pmb.chroot
import pmb.chroot.apk
import pmb.chroot.other
import pmb.chroot.initfs
import pmb.config
import pmb.config.pmaports
from pmb.parse.deviceinfo import Deviceinfo
from pmb.core import Config
from pmb.types import PartitionLayout, PmbArgs
import pmb.helpers.devices
from pmb.helpers.mount import mount_device_rootfs
import pmb.helpers.run
import pmb.helpers.other
import pmb.helpers.package
import pmb.install.blockdevice
import pmb.install.recovery
import pmb.install.ui
import pmb.install
from pmb.core import Chroot, ChrootType
from pmb.core.context import get_context

# Keep track of the packages we already visited in get_recommends() to avoid
# infinite recursion
get_recommends_visited: list[str] = []
get_selected_providers_visited: list[str] = []


def get_subpartitions_size(chroot: Chroot):
    """
    Calculate the size of the boot and root subpartition.

    :param suffix: the chroot suffix, e.g. "rootfs_qemu-amd64"
    :returns: (boot, root) the size of the boot and root
              partition as integer in MiB
    """
    config = get_context().config
    boot = int(config.boot_size)

    # Estimate root partition size, then add some free space. The size
    # calculation is not as trivial as one may think, and depending on the
    # file system etc it seems to be just impossible to get it right.
    root = pmb.helpers.other.folder_size(chroot.path) / 1024
    root *= 1.20
    root += 50 + int(config.extra_space)
    return (boot, root)


def get_nonfree_packages(device):
    """
    Get any legacy non-free subpackages in the APKBUILD.
    Also see: https://postmarketos.org/edge/2024/02/15/default-nonfree-fw/

    :returns: list of non-free packages to be installed. Example:
              ["device-nokia-n900-nonfree-firmware"]
    """
    # Read subpackages
    device_path = pmb.helpers.devices.find_path(device, "APKBUILD")
    if not device_path:
        raise RuntimeError(f"Device package not found for {device}")

    apkbuild = pmb.parse.apkbuild(device_path)
    subpackages = apkbuild["subpackages"]

    # Check for firmware and userland
    ret = []
    prefix = "device-" + device + "-nonfree-"
    if prefix + "firmware" in subpackages:
        ret += [prefix + "firmware"]
    if prefix + "userland" in subpackages:
        ret += [prefix + "userland"]
    return ret


def get_kernel_package(config: Config):
    """
    Get the device's kernel subpackage based on the user's choice in
    "pmbootstrap init".

    :param device: code name, e.g. "sony-amami"
    :returns: [] or the package in a list, e.g.
              ["device-sony-amami-kernel-mainline"]
    """
    # Empty list: single kernel devices / "none" selected
    kernels = pmb.parse._apkbuild.kernels(config.device)
    if not kernels or config.kernel == "none":
        return []

    # Sanity check
    if config.kernel not in kernels:
        raise RuntimeError(
            "Selected kernel (" + config.kernel + ") is not"
            " valid for device " + config.device + ". Please"
            " run 'pmbootstrap init' to select a valid kernel."
        )

    # Selected kernel subpackage
    return ["device-" + config.device + "-kernel-" + config.kernel]


def copy_files_from_chroot(args: PmbArgs, chroot: Chroot):
    """
    Copy all files from the rootfs chroot to /mnt/install, except
    for the home folder (because /home will contain some empty
    mountpoint folders).

    :param suffix: the chroot suffix, e.g. "rootfs_qemu-amd64"
    """
    # Mount the device rootfs
    logging.info(f"(native) copy {chroot} to /mnt/install/")
    mountpoint = mount_device_rootfs(chroot)
    mountpoint_outside = Chroot.native() / mountpoint

    # Remove empty qemu-user binary stub (where the binary was bind-mounted)
    arch_qemu = pmb.parse.deviceinfo().arch.qemu()
    qemu_binary = mountpoint_outside / f"usr/bin/qemu-{arch_qemu}-static"
    if os.path.exists(qemu_binary):
        pmb.helpers.run.root(["rm", qemu_binary])

    # Remove apk progress fifo
    fifo = chroot / "tmp/apk_progress_fifo"
    if os.path.exists(fifo):
        pmb.helpers.run.root(["rm", fifo])

    # Get all folders inside the device rootfs (except for home)
    folders: list[str] = []
    for path in mountpoint_outside.glob("*"):
        if path.name == "home":
            continue
        folders.append(path.name)

    # Update or copy all files
    if args.rsync:
        pmb.chroot.apk.install(["rsync"], Chroot.native())
        rsync_flags = "-a"
        if args.verbose:
            rsync_flags += "vP"
        pmb.chroot.root(
            ["rsync", rsync_flags, "--delete"] + folders + ["/mnt/install/"], working_dir=mountpoint
        )
        pmb.chroot.root(["rm", "-rf", "/mnt/install/home"])
    else:
        pmb.chroot.root(["cp", "-a"] + folders + ["/mnt/install/"], working_dir=mountpoint)


def create_home_from_skel(filesystem: str, user: str):
    """
    Create /home/{user} from /etc/skel
    """
    rootfs = Chroot.native() / "mnt/install"
    # In btrfs, home subvol & home dir is created in format.py
    if filesystem != "btrfs":
        pmb.helpers.run.root(["mkdir", rootfs / "home"])

    home = rootfs / "home" / user
    if (rootfs / "etc/skel").exists():
        pmb.helpers.run.root(["cp", "-a", (rootfs / "etc/skel"), home])
    else:
        pmb.helpers.run.root(["mkdir", home])
    pmb.helpers.run.root(["chown", "-R", "10000", home])


def configure_apk(args: PmbArgs):
    """
    Copy over all official keys, and the keys used to compile local packages
    (unless --no-local-pkgs is set). Then copy the corresponding APKINDEX files
    and remove the /mnt/pmbootstrap/packages repository.
    """
    # Official keys
    keys_dir = pmb.config.apk_keys_path

    # Official keys + local keys
    if args.install_local_pkgs:
        keys_dir = get_context().config.work / "config_apk_keys"

    # Copy over keys
    rootfs = Chroot.native() / "mnt/install"
    for key in keys_dir.glob("*.pub"):
        pmb.helpers.run.root(["cp", key, rootfs / "etc/apk/keys/"])

    # Copy over the corresponding APKINDEX files from cache
    index_files = pmb.helpers.repo.apkindex_files(
        arch=pmb.parse.deviceinfo().arch, user_repository=False
    )
    for f in index_files:
        pmb.helpers.run.root(["cp", f, rootfs / "var/cache/apk/"])

    # Disable pmbootstrap repository
    pmb.chroot.root(
        ["sed", "-i", r"/\/mnt\/pmbootstrap\/packages/d", "/mnt/install/etc/apk/repositories"]
    )
    pmb.helpers.run.user(["cat", rootfs / "etc/apk/repositories"])


def set_user(config: Config):
    """
    Create user with UID 10000 if it doesn't exist.
    Usually the ID for the first user created is 1000, but higher ID is
    chosen here to not cause issues with existing installations. Historically,
    this was done to avoid conflict with Android UIDs/GIDs, but pmOS has since
    dropped support for hybris/Halium.
    """
    chroot = Chroot.rootfs(config.device)
    if not pmb.chroot.user_exists(config.user, chroot):
        pmb.chroot.root(["adduser", "-D", "-u", "10000", config.user], chroot)

    pmaports_cfg = pmb.config.pmaports.read_config()
    groups = []
    groups += pmaports_cfg.get(
        "install_user_groups", "audio,input,netdev,plugdev,video,wheel"
    ).split(",")
    groups += pmb.install.ui.get_groups(config)

    for group in groups:
        pmb.chroot.root(["addgroup", "-S", group], chroot, check=False)
        pmb.chroot.root(["addgroup", config.user, group], chroot)


def setup_login_chpasswd_user_from_arg(args: PmbArgs, user: str, chroot: Chroot):
    """
    Set the user's password from what the user passed as --password. Make an
    effort to not have the password end up in the log file by writing it to
    a temp file, instead of "echo user:$pass | chpasswd". The user should of
    course only use this with a test password anyway, but let's be nice and try
    to have the user protected from accidentally posting their password in
    any case.

    :param suffix: of the chroot, where passwd will be execute (either the
                   rootfs_{args.device} or installer_{args.device}
    """
    path = "/tmp/pmbootstrap_chpasswd_in"
    path_outside = chroot / path

    with open(path_outside, "w", encoding="utf-8") as handle:
        handle.write(f"{user}:{args.password}")

    pmb.chroot.root(["sh", "-c", f"cat {shlex.quote(path)} | chpasswd"], chroot)

    os.unlink(path_outside)


def is_root_locked(chroot: Chroot):
    """
    Figure out from /etc/shadow if root is already locked. The output of this
    is stored in the log, so use grep to only log the line for root, not the
    line for the user which contains a hash of the user's password.

    :param suffix: either rootfs_{args.device} or installer_{args.device}
    """
    shadow_root = pmb.chroot.root(
        ["grep", "^root:!:", "/etc/shadow"], chroot, output_return=True, check=False
    )
    return shadow_root.startswith("root:!:")


def setup_login(args: PmbArgs, config: Config, chroot: Chroot):
    """
    Loop until the password for user has been set successfully, and disable
    root login.

    :param suffix: of the chroot, where passwd will be execute (either the
                   rootfs_{args.device} or installer_{args.device}
    """
    if not args.on_device_installer:
        # User password
        logging.info(f" *** SET LOGIN PASSWORD FOR: '{config.user}' ***")
        if args.password:
            setup_login_chpasswd_user_from_arg(args, config.user, chroot)
        else:
            while True:
                try:
                    pmb.chroot.root(["passwd", config.user], chroot, output="interactive")
                    break
                except RuntimeError:
                    logging.info("WARNING: Failed to set the password. Try it" " one more time.")

    # Disable root login
    if is_root_locked(chroot):
        logging.debug(f"({chroot}) root is already locked")
    else:
        logging.debug(f"({chroot}) locking root")
        pmb.chroot.root(["passwd", "-l", "root"], chroot)


def copy_ssh_keys(config: Config):
    """
    If requested, copy user's SSH public keys to the device if they exist
    """
    if not config.ssh_keys:
        return
    keys = []
    for key in glob.glob(os.path.expanduser(config.ssh_key_glob)):
        with open(key) as infile:
            keys += infile.readlines()

    if not len(keys):
        logging.info(
            "NOTE: Public SSH keys not found. Since no SSH keys "
            "were copied, you will need to use SSH password "
            "authentication!"
        )
        return

    authorized_keys = Chroot.native() / "tmp/authorized_keys"
    outfile = open(authorized_keys, "w")
    for key in keys:
        outfile.write(f"{key}")
    outfile.close()

    target = Chroot.native() / "mnt/install/home/" / config.user / ".ssh"
    pmb.helpers.run.root(["mkdir", target])
    pmb.helpers.run.root(["chmod", "700", target])
    pmb.helpers.run.root(["cp", authorized_keys, target / "authorized_keys"])
    pmb.helpers.run.root(["rm", authorized_keys])
    pmb.helpers.run.root(["chown", "-R", "10000:10000", target])


def setup_keymap(config: Config):
    """
    Set the keymap with the setup-keymap utility if the device requires it
    """
    chroot = Chroot(ChrootType.ROOTFS, config.device)
    deviceinfo = pmb.parse.deviceinfo(device=config.device)
    if not deviceinfo.keymaps or deviceinfo.keymaps.strip() == "":
        logging.info("NOTE: No valid keymap specified for device")
        return
    options = deviceinfo.keymaps.split(" ")
    if config.keymap != "" and config.keymap is not None and config.keymap in options:
        layout, variant = config.keymap.split("/")
        pmb.chroot.root(["setup-keymap", layout, variant], chroot, output="interactive")

        # Check xorg config
        xconfig = None
        if (chroot / "etc/X11/xorg.conf.d").exists():
            xconfig = pmb.chroot.root(
                ["grep", "-rl", "XkbLayout", "/etc/X11/xorg.conf.d/"],
                chroot,
                check=False,
                output_return=True,
            )
        if xconfig:
            # Nokia n900 (RX-51) randomly merges some keymaps so we
            # have to specify a composite keymap for a few countries. See:
            # https://gitlab.freedesktop.org/xkeyboard-config/xkeyboard-config/-/blob/master/symbols/nokia_vndr/rx-51
            if variant == "rx51_fi" or variant == "rx51_se":
                layout = "fise"
            if variant == "rx51_da" or variant == "rx51_no":
                layout = "dano"
            if variant == "rx51_pt" or variant == "rx51_es":
                layout = "ptes"
            # Multiple files can contain the keyboard layout, take last
            xconfig = xconfig.splitlines()[-1]
            old_text = 'Option *\\"XkbLayout\\" *\\".*\\"'
            new_text = 'Option \\"XkbLayout\\" \\"' + layout + '\\"'
            pmb.chroot.root(["sed", "-i", "s/" + old_text + "/" + new_text + "/", xconfig], chroot)
    else:
        logging.info("NOTE: No valid keymap specified for device")


def setup_timezone(chroot: Chroot, timezone: str):
    # We don't care about the arch since it's built for all!
    alpine_conf = pmb.helpers.package.get("alpine-conf", Arch.native())
    version = alpine_conf["version"].split("-r")[0]

    setup_tz_cmd = ["setup-timezone"]
    # setup-timezone will, by default, copy the timezone to /etc/zoneinfo
    # and disregard tzdata, to save space. If we actually have tzdata
    # installed, make sure that setup-timezone makes use of it, since
    # there's no space to be saved.
    if "tzdata" in pmb.chroot.apk.installed(chroot):
        setup_tz_cmd += ["-i"]
    if not pmb.parse.version.check_string(version, ">=3.14.0"):
        setup_tz_cmd += ["-z"]
    setup_tz_cmd += [timezone]
    pmb.chroot.root(setup_tz_cmd, chroot)


def setup_hostname(device: str, hostname: str | None):
    """
    Set the hostname and update localhost address in /etc/hosts
    """
    # Default to device name. If device name is not a valid hostname then
    # default to a static default.
    if not hostname:
        hostname = pmb.helpers.other.normalize_hostname(device)
        if not pmb.helpers.other.validate_hostname(hostname):
            # A valid host name, see:
            # https://datatracker.ietf.org/doc/html/rfc1035#section-2.3.1
            hostname = "postmarketos-device"
    elif not pmb.helpers.other.validate_hostname(hostname):
        # Invalid hostname set by the user e.g., via pmb init, this should
        # fail so they can fix it
        raise RuntimeError(
            "Hostname '" + hostname + "' is not valid, please"
            " run 'pmbootstrap init' to configure it."
        )

    suffix = Chroot(ChrootType.ROOTFS, device)
    # Generate /etc/hostname
    pmb.chroot.root(["sh", "-c", "echo " + shlex.quote(hostname) + " > /etc/hostname"], suffix)
    # Update /etc/hosts
    regex = (
        r"s/^127\.0\.0\.1.*/127.0.0.1\t" + re.escape(hostname) + " localhost.localdomain localhost/"
    )
    pmb.chroot.root(["sed", "-i", "-e", regex, "/etc/hosts"], suffix)


def setup_appstream(offline: bool, chroot: Chroot):
    """
    If alpine-appstream-downloader has been downloaded, execute it to have
    update AppStream data on new installs
    """
    installed_pkgs = pmb.chroot.apk.installed(chroot)

    if "alpine-appstream-downloader" not in installed_pkgs or offline:
        return

    if not pmb.chroot.root(
        ["alpine-appstream-downloader", "/mnt/appstream-data"], chroot, check=False
    ):
        pmb.chroot.root(["mkdir", "-p", "/var/lib/swcatalog"], chroot)
        pmb.chroot.root(
            [
                "cp",
                "-r",
                "/mnt/appstream-data/icons",
                "/mnt/appstream-data/xml",
                "-t",
                "/var/lib/swcatalog",
            ],
            chroot,
        )


def disable_sshd(chroot: Chroot):
    # check=False: rc-update doesn't exit with 0 if already disabled
    pmb.chroot.root(["rc-update", "del", "sshd", "default"], chroot, check=False)

    # Verify that it's gone
    sshd_files = pmb.helpers.run.root(
        ["find", "-name", "sshd"], output_return=True, working_dir=chroot / "etc/runlevels"
    )
    if sshd_files:
        raise RuntimeError(f"Failed to disable sshd service: {sshd_files}")


def print_sshd_info(args: PmbArgs):
    logging.info("")  # make the note stand out
    logging.info("*** SSH DAEMON INFORMATION ***")

    if not args.ondev_no_rootfs:
        if args.no_sshd:
            logging.info("SSH daemon is disabled (--no-sshd).")
        else:
            logging.info("SSH daemon is enabled (disable with --no-sshd).")
            logging.info(
                f"Login as '{get_context().config.user}' with the password given"
                " during installation."
            )

    if args.on_device_installer:
        # We don't disable sshd in the installer OS. If the device is reachable
        # on the network by default (e.g. Raspberry Pi), one can lock down the
        # installer OS down by disabling the debug user (see wiki page).
        logging.info(
            "SSH daemon is enabled in the installer OS, to allow" " debugging the installer image."
        )
        logging.info("More info: https://postmarketos.org/ondev-debug")


def disable_firewall(chroot: Chroot):
    # check=False: rc-update doesn't exit with 0 if already disabled
    pmb.chroot.root(["rc-update", "del", "nftables", "default"], chroot, check=False)

    # Verify that it's gone
    nftables_files = pmb.helpers.run.root(
        ["find", "-name", "nftables"], output_return=True, working_dir=chroot / "etc/runlevels"
    )
    if nftables_files:
        raise RuntimeError(f"Failed to disable firewall: {nftables_files}")


def print_firewall_info(disabled: bool, arch: Arch):
    pmaports_cfg = pmb.config.pmaports.read_config()
    pmaports_ok = pmaports_cfg.get("supported_firewall", None) == "nftables"

    # Find kernel pmaport (will not be found if Alpine kernel is used)
    apkbuild_found = False
    apkbuild_has_opt = False

    kernel = get_kernel_package(get_context().config)
    if kernel:
        _, kernel_apkbuild = pmb.build.get_apkbuild(kernel[0])
        if kernel_apkbuild:
            opts = kernel_apkbuild["options"]
            apkbuild_has_opt = "pmb:kconfigcheck-nftables" in opts
            apkbuild_found = True

    # Print the note and make it stand out
    logging.info("")
    logging.info("*** FIREWALL INFORMATION ***")

    if not pmaports_ok:
        logging.info("Firewall is not supported in checked out pmaports" " branch.")
    elif disabled:
        logging.info("Firewall is disabled (--no-firewall).")
    elif not apkbuild_found:
        logging.info(
            "Firewall is enabled, but may not work (couldn't"
            " determine if kernel supports nftables)."
        )
    elif apkbuild_has_opt:
        logging.info("Firewall is enabled and supported by kernel.")
    else:
        logging.info(
            "Firewall is enabled, but will not work (no support in" " kernel config for nftables)."
        )
        logging.info("If/when the kernel supports it in the future, it" " will work automatically.")

    logging.info("For more information: https://postmarketos.org/firewall")


def generate_binary_list(args: PmbArgs, chroot: Chroot, step):
    """
    Perform three checks prior to writing binaries to disk: 1) that binaries
    exist, 2) that binaries do not extend into the first partition, 3) that
    binaries do not overlap each other.

    :param suffix: of the chroot, which holds the firmware files (either the
                   rootfs_{args.device} or installer_{args.device}
    :param step: partition step size in bytes
    """
    binary_ranges: dict[int, int] = {}
    binary_list = []
    binaries = (pmb.parse.deviceinfo().sd_embed_firmware or "").split(",")

    for binary_offset in binaries:
        binary, _offset = binary_offset.split(":")
        try:
            offset = int(_offset)
        except ValueError:
            raise RuntimeError("Value for firmware binary offset is " f"not valid: {offset}")
        binary_path = chroot / "usr/share" / binary
        if not os.path.exists(binary_path):
            raise RuntimeError(
                "The following firmware binary does not "
                f"exist in the {chroot} chroot: "
                f"/usr/share/{binary}"
            )
        # Insure that embedding the firmware will not overrun the
        # first partition
        boot_part_start = pmb.parse.deviceinfo().boot_part_start or "2048"
        max_size = (int(boot_part_start) * 512) - (offset * step)
        binary_size = os.path.getsize(binary_path)
        if binary_size > max_size:
            raise RuntimeError(
                "The firmware is too big to embed in the "
                f"disk image {binary_size}B > {max_size}B"
            )
        # Insure that the firmware does not conflict with any other firmware
        # that will be embedded
        binary_start = offset * step
        binary_end = binary_start + binary_size
        for start, end in binary_ranges.items():
            if (binary_start >= start and binary_start < end) or (
                binary_end > start and binary_end <= end
            ):
                raise RuntimeError(
                    "The firmware overlaps with at least one " f"other firmware image: {binary}"
                )

        binary_ranges[binary_start] = binary_end
        binary_list.append((binary, offset))

    return binary_list


def embed_firmware(args: PmbArgs, suffix: Chroot):
    """
    This method will embed firmware, located at /usr/share, that are specified
    by the "sd_embed_firmware" deviceinfo parameter into the SD card image
    (e.g. u-boot). Binaries that would overwrite the first partition are not
    accepted, and if multiple binaries are specified then they will be checked
    for collisions with each other.

    :param suffix: of the chroot, which holds the firmware files (either the
                   rootfs_{args.device} or installer_{args.device}
    """
    if not pmb.parse.deviceinfo().sd_embed_firmware:
        return

    step = 1024
    if pmb.parse.deviceinfo().sd_embed_firmware_step_size:
        try:
            step = int(pmb.parse.deviceinfo().sd_embed_firmware_step_size or "invalid")
        except ValueError:
            raise RuntimeError(
                "Value for " "deviceinfo_sd_embed_firmware_step_size " f"is not valid: {step}"
            )

    device_rootfs = mount_device_rootfs(suffix)
    binary_list = generate_binary_list(args, suffix, step)

    # Write binaries to disk
    for binary, offset in binary_list:
        binary_file = os.path.join("/usr/share", binary)
        logging.info(
            f"Embed firmware {binary} in the SD card image at offset {offset} with"
            f" step size {step}"
        )
        filename = os.path.join(device_rootfs, binary_file.lstrip("/"))
        pmb.chroot.root(
            ["dd", "if=" + filename, "of=/dev/install", "bs=" + str(step), "seek=" + str(offset)]
        )


def write_cgpt_kpart(args: PmbArgs, layout, suffix: Chroot):
    """
    Write the kernel to the ChromeOS kernel partition.

    :param layout: partition layout from get_partition_layout()
    :param suffix: of the chroot, which holds the image file to be flashed
    """
    if not pmb.parse.deviceinfo().cgpt_kpart or not args.install_cgpt:
        return

    device_rootfs = mount_device_rootfs(suffix)
    filename = f"{device_rootfs}{pmb.parse.deviceinfo().cgpt_kpart}"
    pmb.chroot.root(["dd", f"if={filename}", f"of=/dev/installp{layout['kernel']}"])


def sanity_check_boot_size():
    default = Config().boot_size
    config = get_context().config
    if int(config.boot_size) >= int(default):
        return
    logging.error(
        "ERROR: your pmbootstrap has a small/invalid boot_size of"
        f" {config.boot_size} configured, probably because the config"
        " has been created with an old version."
    )
    logging.error(
        "This can lead to problems later on, we recommend setting it" f" to {default} MiB."
    )
    logging.error(f"Run 'pmbootstrap config boot_size {default}' and try again.")
    sys.exit(1)


def sanity_check_disk(args: PmbArgs):
    device = args.disk
    device_name = os.path.basename(device)
    if not os.path.exists(device):
        raise RuntimeError(f"{device} doesn't exist, is the disk plugged?")
    if os.path.isdir(f"/sys/class/block/{device_name}"):
        with open(f"/sys/class/block/{device_name}/ro") as handle:
            ro = handle.read()
        if ro == "1\n":
            raise RuntimeError(f"{device} is read-only, maybe a locked SD card?")


def sanity_check_disk_size(args: PmbArgs):
    device = args.disk
    devpath = os.path.realpath(device)
    sysfs = "/sys/class/block/{}/size".format(devpath.replace("/dev/", ""))
    if not os.path.isfile(sysfs):
        # This is a best-effort sanity check, continue if it's not checkable
        return

    with open(sysfs) as handle:
        raw = handle.read()

    # Size is in 512-byte blocks
    size = int(raw.strip())
    human = f"{size / 2 / 1024 / 1024:.2f} GiB"

    # Warn if the size is larger than 100GiB
    if not args.assume_yes and size > (100 * 2 * 1024 * 1024):
        if not pmb.helpers.cli.confirm(
            f"WARNING: The target disk ({devpath}) "
            "is larger than a usual SD card "
            "(>100GiB). Are you sure you want to "
            f"overwrite this {human} disk?",
            no_assumptions=True,
        ):
            raise RuntimeError("Aborted.")


def get_ondev_pkgver(args: PmbArgs):
    arch = pmb.parse.deviceinfo().arch
    package = pmb.helpers.package.get("postmarketos-ondev", arch)
    return package["version"].split("-r")[0]


def sanity_check_ondev_version(args: PmbArgs):
    ver_pkg = get_ondev_pkgver(args)
    ver_min = pmb.config.ondev_min_version
    if pmb.parse.version.compare(ver_pkg, ver_min) == -1:
        raise RuntimeError(
            "This version of pmbootstrap requires"
            f" postmarketos-ondev version {ver_min} or"
            " higher. The postmarketos-ondev found in pmaports"
            f" / in the binary packages has version {ver_pkg}."
        )


def get_partition_layout(reserve, kernel):
    """
    :param reserve: create an empty partition between root and boot (pma#463)
    :param kernel: create a separate kernel partition before all other
                   partitions, e.g. for the ChromeOS devices with cgpt
    :returns: the partition layout, e.g. without reserve and kernel:
              {"kernel": None, "boot": 1, "reserve": None, "root": 2}
    """
    ret: PartitionLayout = {
        "kernel": None,
        "boot": 1,
        "reserve": None,
        "root": 2,
    }

    if kernel:
        ret["kernel"] = 1
        ret["boot"] += 1
        ret["root"] += 1

    if reserve:
        ret["reserve"] = ret["root"]
        ret["root"] += 1
    return ret


def get_uuid(args: PmbArgs, partition: Path):
    """
    Get UUID of a partition

    :param partition: block device for getting UUID from
    """
    return pmb.chroot.root(
        [
            "blkid",
            "-s",
            "UUID",
            "-o",
            "value",
            partition,
        ],
        output_return=True,
    ).rstrip()


def create_crypttab(args: PmbArgs, layout, chroot: Chroot):
    """
    Create /etc/crypttab config

    :param layout: partition layout from get_partition_layout()
    :param suffix: of the chroot, which crypttab will be created to
    """

    luks_uuid = get_uuid(args, Path("/dev") / f"installp{layout['root']}")

    crypttab = f"root UUID={luks_uuid} none luks\n"

    (chroot / "tmp/crypttab").open("w").write(crypttab)
    pmb.chroot.root(["mv", "/tmp/crypttab", "/etc/crypttab"], chroot)


def create_fstab(args: PmbArgs, layout, chroot: Chroot):
    """
    Create /etc/fstab config

    :param layout: partition layout from get_partition_layout()
    :param chroot: of the chroot, which fstab will be created to
    """

    # Do not install fstab into target rootfs when using on-device
    # installer. Provide fstab only to installer suffix
    if args.on_device_installer and chroot.type == ChrootType.ROOTFS:
        return

    boot_dev = Path(f"/dev/installp{layout['boot']}")
    root_dev = Path(f"/dev/installp{layout['root']}")

    boot_mount_point = f"UUID={get_uuid(args, boot_dev)}"
    root_mount_point = (
        "/dev/mapper/root" if args.full_disk_encryption else f"UUID={get_uuid(args, root_dev)}"
    )

    boot_options = "nodev,nosuid,noexec"
    boot_filesystem = pmb.parse.deviceinfo().boot_filesystem or "ext2"
    if boot_filesystem in ("fat16", "fat32"):
        boot_filesystem = "vfat"
        boot_options += ",umask=0077,nosymfollow,codepage=437,iocharset=ascii"
    root_filesystem = pmb.install.get_root_filesystem(args)

    if root_filesystem == "btrfs":
        # btrfs gets separate subvolumes for root, var and home
        fstab = f"""
# <file system> <mount point> <type> <options> <dump> <pass>
{root_mount_point} / btrfs subvol=@,compress=zstd:2,ssd 0 0
{root_mount_point} /home btrfs subvol=@home,compress=zstd:2,ssd 0 0
{root_mount_point} /root btrfs subvol=@root,compress=zstd:2,ssd 0 0
{root_mount_point} /srv btrfs subvol=@srv,compress=zstd:2,ssd 0 0
{root_mount_point} /var btrfs subvol=@var,ssd 0 0
{root_mount_point} /.snapshots btrfs subvol=@snapshots,compress=zstd:2,ssd 0 0

{boot_mount_point} /boot {boot_filesystem} {boot_options} 0 0
""".lstrip()

    else:
        fstab = f"""
# <file system> <mount point> <type> <options> <dump> <pass>
{root_mount_point} / {root_filesystem} defaults 0 0
{boot_mount_point} /boot {boot_filesystem} {boot_options} 0 0
""".lstrip()

    with (chroot / "tmp/fstab").open("w") as f:
        f.write(fstab)
    pmb.chroot.root(["mv", "/tmp/fstab", "/etc/fstab"], chroot)


def install_system_image(
    args: PmbArgs,
    size_reserve,
    chroot: Chroot,
    step,
    steps,
    boot_label="pmOS_boot",
    root_label="pmOS_root",
    split=False,
    disk: Path | None = None,
):
    """
    :param size_reserve: empty partition between root and boot in MiB (pma#463)
    :param suffix: the chroot suffix, where the rootfs that will be installed
                   on the device has been created (e.g. "rootfs_qemu-amd64")
    :param step: next installation step
    :param steps: total installation steps
    :param boot_label: label of the boot partition (e.g. "pmOS_boot")
    :param root_label: label of the root partition (e.g. "pmOS_root")
    :param split: create separate images for boot and root partitions
    :param disk: path to disk block device (e.g. /dev/mmcblk0) or None
    """
    config = get_context().config
    device = chroot.name
    # Partition and fill image file/disk block device
    logging.info(f"*** ({step}/{steps}) PREPARE INSTALL BLOCKDEVICE ***")
    pmb.helpers.mount.umount_all(chroot.path)
    (size_boot, size_root) = get_subpartitions_size(chroot)
    layout = get_partition_layout(
        size_reserve, pmb.parse.deviceinfo().cgpt_kpart and args.install_cgpt
    )
    if not args.rsync:
        pmb.install.blockdevice.create(args, size_boot, size_root, size_reserve, split, disk)
        if not split:
            if pmb.parse.deviceinfo().cgpt_kpart and args.install_cgpt:
                pmb.install.partition_cgpt(layout, size_boot, size_reserve)
            else:
                pmb.install.partition(layout, size_boot, size_reserve)
    if not split:
        pmb.install.partitions_mount(device, layout, disk)

    pmb.install.format(args, layout, boot_label, root_label, disk)

    # Since we shut down the chroot we need to mount it again
    pmb.chroot.mount(chroot)

    # Create /etc/fstab and /etc/crypttab
    logging.info("(native) create /etc/fstab")
    create_fstab(args, layout, chroot)
    if args.full_disk_encryption:
        logging.info("(native) create /etc/crypttab")
        create_crypttab(args, layout, chroot)

    # Run mkinitfs to pass UUIDs to cmdline
    logging.info(f"({chroot}) mkinitfs")
    pmb.chroot.root(["mkinitfs"], chroot)

    # Clean up after running mkinitfs in chroot
    pmb.helpers.mount.umount_all(chroot.path)
    pmb.helpers.run.root(["rm", chroot / "in-pmbootstrap"])
    pmb.chroot.remove_mnt_pmbootstrap(chroot)

    # Just copy all the files
    logging.info(f"*** ({step + 1}/{steps}) FILL INSTALL BLOCKDEVICE ***")
    copy_files_from_chroot(args, chroot)
    create_home_from_skel(args.filesystem, config.user)
    configure_apk(args)
    copy_ssh_keys(config)

    # Don't try to embed firmware and cgpt on split images since there's no
    # place to put it and it will end up in /dev of the chroot instead
    if not split:
        embed_firmware(args, chroot)
        write_cgpt_kpart(args, layout, chroot)

    if disk:
        logging.info(f"Unmounting disk {disk} (this may take a while " "to sync, please wait)")
    pmb.chroot.shutdown(True)

    # Convert rootfs to sparse using img2simg
    sparse = args.sparse
    if sparse is None:
        sparse = pmb.parse.deviceinfo().flash_sparse == "true"

    if sparse and not split and not disk:
        workdir = Path("/home/pmos/rootfs")
        logging.info("(native) make sparse rootfs")
        pmb.chroot.apk.install(["android-tools"], Chroot.native())
        sys_image = device + ".img"
        sys_image_sparse = device + "-sparse.img"
        pmb.chroot.user(["img2simg", sys_image, sys_image_sparse], working_dir=workdir)
        pmb.chroot.user(["mv", "-f", sys_image_sparse, sys_image], working_dir=workdir)

        # patch sparse image for Samsung devices if specified
        samsungify_strategy = pmb.parse.deviceinfo().flash_sparse_samsung_format
        if samsungify_strategy:
            logging.info("(native) convert sparse image into Samsung's sparse image format")
            pmb.chroot.apk.install(["sm-sparse-image-tool"], Chroot.native())
            sys_image = f"{device}.img"
            sys_image_patched = f"{device}-patched.img"
            pmb.chroot.user(
                [
                    "sm_sparse_image_tool",
                    "samsungify",
                    "--strategy",
                    samsungify_strategy,
                    sys_image,
                    sys_image_patched,
                ],
                working_dir=workdir,
            )
            pmb.chroot.user(["mv", "-f", sys_image_patched, sys_image], working_dir=workdir)


def print_flash_info(device: str, deviceinfo: Deviceinfo, split: bool, have_disk: bool):
    """Print flashing information, based on the deviceinfo data and the
    pmbootstrap arguments."""
    logging.info("")  # make the note stand out
    logging.info("*** FLASHING INFORMATION ***")

    # System flash information
    method = deviceinfo.flash_method
    flasher = pmb.config.flashers.get(method, {})
    flasher_actions = flasher.get("actions", {})
    if not isinstance(flasher_actions, dict):
        raise TypeError(f"flasher actions must be a dictionary, got: {flasher_actions}")
    requires_split = flasher.get("split", False)

    if method == "none":
        logging.info(
            "Refer to the installation instructions of your device,"
            " or the generic install instructions in the wiki."
        )
        logging.info("https://wiki.postmarketos.org/wiki/Installation_guide" "#pmbootstrap_flash")
        return

    logging.info("Run the following to flash your installation to the" " target device:")

    if "flash_rootfs" in flasher_actions and not have_disk and bool(split) == requires_split:
        logging.info("* pmbootstrap flasher flash_rootfs")
        logging.info("  Flashes the generated rootfs image to your device:")
        if split:
            logging.info(f"  {Chroot.native() / 'home/pmos/rootfs' / device}-root.img")
        else:
            logging.info(f"  {Chroot.native() / 'home/pmos/rootfs' / device}.img")
            logging.info(
                "  (NOTE: This file has a partition table, which"
                " contains /boot and / subpartitions. That way we"
                " don't need to change the partition layout on your"
                " device.)"
            )

    # if current flasher supports vbmeta and partition is explicitly specified
    # in deviceinfo
    if "flash_vbmeta" in flasher_actions and (
        deviceinfo.flash_fastboot_partition_vbmeta or deviceinfo.flash_heimdall_partition_vbmeta
    ):
        logging.info("* pmbootstrap flasher flash_vbmeta")
        logging.info("  Flashes vbmeta image with verification disabled flag.")

    # if current flasher supports dtbo and partition is explicitly specified
    # in deviceinfo
    if "flash_dtbo" in flasher_actions and (
        deviceinfo.flash_fastboot_partition_dtbo or deviceinfo.flash_heimdall_partition_dtbo
    ):
        logging.info("* pmbootstrap flasher flash_dtbo")
        logging.info("  Flashes dtbo image.")

    # Most flash methods operate independently of the boot partition.
    # (e.g. an Android boot image is generated). In that case, "flash_kernel"
    # works even when partitions are split or installing to disk. This is not
    # possible if the flash method requires split partitions.
    if "flash_kernel" in flasher_actions and (not requires_split or split):
        logging.info("* pmbootstrap flasher flash_kernel")
        logging.info("  Flashes the kernel + initramfs to your device:")
        if requires_split:
            logging.info(f"  {Chroot.native() / 'home/pmos/rootfs' / device}-boot.img")
        else:
            logging.info(f"  {Chroot(ChrootType.ROOTFS, device) / 'boot'}")

    if "flash_boot" in flasher_actions and (Chroot.rootfs(device) / "boot/boot.img").exists():
        logging.info("* pmbootstrap flasher flash_boot")
        logging.info("  Flashes the generated Android boot image to your device:")
        logging.info(f"  {Chroot.rootfs(device) / 'boot/boot.img'}")
        logging.info("  (NOTE: This is not necessary if using a custom bootloader like U-Boot)")

    if "boot" in flasher_actions:
        logging.info(
            "  (NOTE: " + method + " also supports booting"
            " the kernel/initramfs directly without flashing."
            " Use 'pmbootstrap flasher boot' to do that.)"
        )

    if (
        "flash_lk2nd" in flasher_actions
        and (Chroot(ChrootType.ROOTFS, device) / "boot/lk2nd.img").exists()
    ):
        logging.info(
            "* Your device supports and may even require"
            " flashing lk2nd. You should flash it before"
            " flashing anything else. Use 'pmbootstrap flasher"
            " flash_lk2nd' to do that."
        )

    # Export information
    logging.info(
        "* If the above steps do not work, you can also create"
        " symlinks to the generated files with 'pmbootstrap export'"
        " and flash outside of pmbootstrap."
    )


def install_recovery_zip(args: PmbArgs, device: str, arch: Arch, steps):
    logging.info(f"*** ({steps}/{steps}) CREATING RECOVERY-FLASHABLE ZIP ***")
    chroot = Chroot(ChrootType.BUILDROOT, arch)
    mount_device_rootfs(Chroot.rootfs(device), chroot)
    pmb.install.recovery.create_zip(args, chroot, device)

    # Flash information
    logging.info("*** FLASHING INFORMATION ***")
    logging.info("Flashing with the recovery zip is explained here:")
    logging.info("https://postmarketos.org/recoveryzip")


def install_on_device_installer(args: PmbArgs, step, steps):
    # Generate the rootfs image
    config = get_context().config
    if not args.ondev_no_rootfs:
        suffix_rootfs = Chroot.rootfs(config.device)
        install_system_image(args, 0, suffix_rootfs, step=step, steps=steps, split=True)
        step += 2

    # Prepare the installer chroot
    logging.info(f"*** ({step}/{steps}) CREATE ON-DEVICE INSTALLER ROOTFS ***")
    step += 1
    packages = (
        [f"device-{config.device}", "postmarketos-ondev"]
        + get_kernel_package(config)
        + get_nonfree_packages(config.device)
    )

    chroot_installer = Chroot(ChrootType.INSTALLER, config.device)
    pmb.chroot.apk.install(packages, chroot_installer)

    # Move rootfs image into installer chroot
    img_path_dest = chroot_installer / "var/lib/rootfs.img"
    if not args.ondev_no_rootfs:
        img = f"{config.device}-root.img"
        img_path_src = Chroot.native() / "home/pmos/rootfs" / img
        logging.info(f"({chroot_installer}) add {img} as /var/lib/rootfs.img")
        pmb.install.losetup.umount(img_path_src)
        pmb.helpers.run.root(["mv", img_path_src, img_path_dest])

    # Run ondev-prepare, so it may generate nice configs from the channel
    # properties (e.g. to display the version number), or transform the image
    # file into another format. This can all be done without pmbootstrap
    # changes in the postmarketos-ondev package.
    logging.info(f"({chroot_installer}) ondev-prepare")
    channel = pmb.config.pmaports.read_config()["channel"]
    channel_cfg = pmb.config.pmaports.read_config_channel()
    env = {
        "ONDEV_CHANNEL": channel,
        "ONDEV_CHANNEL_BRANCH_APORTS": channel_cfg["branch_aports"],
        "ONDEV_CHANNEL_BRANCH_PMAPORTS": channel_cfg["branch_pmaports"],
        "ONDEV_CHANNEL_DESCRIPTION": channel_cfg["description"],
        "ONDEV_CHANNEL_MIRRORDIR_ALPINE": channel_cfg["mirrordir_alpine"],
        "ONDEV_CIPHER": args.cipher,
        "ONDEV_PMBOOTSTRAP_VERSION": pmb.__version__,
        "ONDEV_UI": config.ui,
    }
    pmb.chroot.root(["ondev-prepare"], chroot_installer, env=env)

    # Copy files specified with 'pmbootstrap install --ondev --cp'
    if args.ondev_cp:
        for host_src, chroot_dest in args.ondev_cp:
            host_dest = chroot_installer / chroot_dest
            logging.info(f"({chroot_installer}) add {host_src} as" f" {chroot_dest}")
            pmb.helpers.run.root(["install", "-Dm644", host_src, host_dest])

    # Remove $DEVICE-boot.img (we will generate a new one if --split was
    # specified, otherwise the separate boot image is not needed)
    if not args.ondev_no_rootfs:
        img_boot = f"{config.device}-boot.img"
        logging.info(f"(native) rm {img_boot}")
        pmb.chroot.root(["rm", f"/home/pmos/rootfs/{img_boot}"])

    # Disable root login
    setup_login(args, config, chroot_installer)

    # Generate installer image
    size_reserve = round(os.path.getsize(img_path_dest) / 1024 / 1024) + 200
    pmaports_cfg = pmb.config.pmaports.read_config()
    boot_label = pmaports_cfg.get("supported_install_boot_label", "pmOS_inst_boot")
    install_system_image(
        args,
        size_reserve,
        chroot_installer,
        step,
        steps,
        boot_label,
        "pmOS_install",
        args.split,
        args.disk,
    )


def get_selected_providers(args: PmbArgs, packages):
    """
    Look through the specified packages and see which providers were selected
    in "pmbootstrap init". Install those as extra packages to select them
    instead of the default provider. This function is called recursively on the
    dependencies of the given packages.

    :param packages: the packages that have selectable providers (_pmb_select)
    :param initial: used internally when the function calls itself
    :return: additional provider packages to install
    """
    global get_selected_providers_visited

    ret = []

    for package in packages:
        if package in get_selected_providers_visited:
            logging.verbose(f"get_selected_providers: {package}: already visited")
            continue
        get_selected_providers_visited += [package]

        # Note that this ignores packages that don't exist. This means they
        # aren't in pmaports. This is fine, with the assumption that
        # installation will fail later in some other method if they truly don't
        # exist in any repo.
        apkbuild = pmb.helpers.pmaports.get(package, subpackages=False, must_exist=False)
        if not apkbuild:
            continue
        for select in apkbuild["_pmb_select"]:
            if select in get_context().config.providers:
                ret += [get_context().config.providers[select]]
                logging.verbose(f"{package}: install selected_providers:" f" {', '.join(ret)}")
            else:
                for default in apkbuild["_pmb_default"]:
                    # default: e.g. "postmarketos-base-ui-audio-pipewire"
                    # select: e.g. "postmarketos-base-ui-audio"
                    if default.startswith(f"{select}-"):
                        ret += [default]
        # Also iterate through dependencies to collect any providers they have
        depends = apkbuild["depends"]
        if depends:
            ret += get_selected_providers(args, depends)

    return ret


def get_recommends(args: PmbArgs, packages) -> Sequence[str]:
    """
    Look through the specified packages and collect additional packages
    specified under _pmb_recommends in them. This is recursive, so it will dive
    into packages that are listed under recommends to collect any packages they
    might also have listed under their own _pmb_recommends.

    Recursion is only done into packages found in pmaports.

    If running with pmbootstrap install --no-recommends, this function returns
    an empty list.

    :param packages: list of packages of which we want to get the recommends
    :param initial: used internally when the function calls itself
    :returns: list of pkgnames, e.g. ["chatty", "gnome-contacts"]
    """
    global get_recommends_visited

    ret: list[str] = []
    if not args.install_recommends:
        return ret

    for package in packages:
        if package in get_recommends_visited:
            logging.verbose(f"get_recommends: {package}: already visited")
            continue
        get_recommends_visited += [package]

        # Note that this ignores packages that don't exist. This means they
        # aren't in pmaports. This is fine, with the assumption that
        # installation will fail later in some other method if they truly don't
        # exist in any repo.
        apkbuild = pmb.helpers.pmaports.get(package, must_exist=False)
        if not apkbuild:
            continue
        if package in apkbuild["subpackages"]:
            # Just focus on the subpackage
            apkbuild = apkbuild["subpackages"][package]
            # The subpackage is None if the subpackage does not have a function
            # in the APKBUILD (uses the default function), e.g. for most openrc
            # subpackages. See pmb.parse._apkbuild._parse_subpackage().
            if not apkbuild:
                continue
        recommends = apkbuild["_pmb_recommends"]
        if recommends:
            logging.debug(f"{package}: install _pmb_recommends:" f" {', '.join(recommends)}")
            ret += recommends
            # Call recursively in case recommends have pmb_recommends of their
            # own.
            ret += get_recommends(args, recommends)
        # Also iterate through dependencies to collect any recommends they have
        depends = apkbuild["depends"]
        if depends:
            ret += get_recommends(args, depends)

    return ret


def create_device_rootfs(args: PmbArgs, step, steps):
    # list all packages to be installed (including the ones specified by --add)
    # and upgrade the installed packages/apkindexes
    context = get_context()
    config = context.config
    device = context.config.device
    logging.info(f'*** ({step}/{steps}) CREATE DEVICE ROOTFS ("{device}")' " ***")

    chroot = Chroot(ChrootType.ROOTFS, device)
    pmb.chroot.init(chroot)
    # Create user before installing packages, so post-install scripts of
    # pmaports can figure out the username (legacy reasons: pmaports#820)
    set_user(context.config)

    # Fill install_packages
    install_packages = pmb.config.install_device_packages + ["device-" + device]
    if not args.install_base:
        install_packages = [p for p in install_packages if p != "postmarketos-base"]
    if config.ui.lower() != "none":
        install_packages += ["postmarketos-ui-" + config.ui]

    # Add additional providers of base/device/UI package
    install_packages += get_selected_providers(args, install_packages)

    install_packages += get_kernel_package(config)
    install_packages += get_nonfree_packages(device)
    if context.config.ui.lower() != "none":
        if context.config.ui_extras:
            install_packages += ["postmarketos-ui-" + config.ui + "-extras"]
    if context.config.extra_packages.lower() != "none":
        install_packages += context.config.extra_packages.split(",")
    if args.add:
        install_packages += args.add.split(",")
    locale_is_set = config.locale != Config().locale
    if locale_is_set:
        install_packages += ["lang", "musl-locales"]

    pmaports_cfg = pmb.config.pmaports.read_config()
    # postmarketos-base supports a dummy package for blocking unl0kr install
    # when not required
    if pmaports_cfg.get("supported_base_nofde", None):
        # The ondev installer *could* enable fde at runtime, so include it
        # explicitly in the rootfs until there's a mechanism to selectively
        # install it when the ondev installer is running.
        # Always install it when --fde is specified.
        if args.full_disk_encryption or args.on_device_installer:
            # Pick the most suitable unlocker depending on the packages
            # selected for installation
            unlocker = pmb.parse.depends.package_provider(
                "postmarketos-fde-unlocker", install_packages, chroot
            )
            if unlocker["pkgname"] not in install_packages:
                install_packages += [unlocker["pkgname"]]
        else:
            install_packages += ["postmarketos-base-nofde"]

    pmb.helpers.repo.update(pmb.parse.deviceinfo().arch)

    # Install uninstallable "dependencies" by default
    install_packages += get_recommends(args, install_packages)

    # Install the base-systemd package first to make sure presets are available
    # when services are installed later
    if pmb.config.other.is_systemd_selected(context.config):
        pmb.chroot.apk.install(["postmarketos-base-systemd"], chroot)

    # Install all packages to device rootfs chroot (and rebuild the initramfs,
    # because that doesn't always happen automatically yet, e.g. when the user
    # installed a hook without pmbootstrap - see #69 for more info)
    # Packages will be built if necessary as part of this step
    pmb.chroot.apk.install(install_packages, chroot)
    flavor = pmb.chroot.other.kernel_flavor_installed(chroot)
    pmb.chroot.initfs.build(flavor, chroot)

    # Set the user password
    setup_login(args, config, chroot)

    # Set the keymap if the device requires it
    setup_keymap(config)

    # Set timezone
    setup_timezone(chroot, config.timezone)

    # Set locale
    if locale_is_set:
        # 10locale-pmos.sh gets sourced before 20locale.sh from
        # alpine-baselayout by /etc/profile. Since they don't override the
        # locale if it exists, it warranties we have preference
        line = f"export LANG=${{LANG:-{shlex.quote(config.locale)}}}"
        pmb.chroot.root(
            ["sh", "-c", f"echo {shlex.quote(line)}" " > /etc/profile.d/10locale-pmos.sh"], chroot
        )

    # Set the hostname as the device name
    setup_hostname(device, config.hostname)

    setup_appstream(context.offline, chroot)

    if args.no_sshd:
        disable_sshd(chroot)
    if args.no_firewall:
        disable_firewall(chroot)


def install(args: PmbArgs):
    device = get_context().config.device
    chroot = Chroot(ChrootType.ROOTFS, device)
    deviceinfo = pmb.parse.deviceinfo()
    # Sanity checks
    sanity_check_boot_size()
    if not args.android_recovery_zip and args.disk:
        sanity_check_disk(args)
        sanity_check_disk_size(args)
    if args.on_device_installer:
        sanity_check_ondev_version(args)

    # Number of steps for the different installation methods.
    if args.no_image:
        steps = 2
    elif args.android_recovery_zip:
        steps = 3
    elif args.on_device_installer:
        steps = 4 if args.ondev_no_rootfs else 7
    else:
        steps = 4

    if args.zap:
        pmb.chroot.zap(False)

    # Install required programs in native chroot
    step = 1
    logging.info(f"*** ({step}/{steps}) PREPARE NATIVE CHROOT ***")
    pmb.chroot.init(Chroot.native())
    pmb.chroot.apk.install(pmb.config.install_native_packages, Chroot.native(), build=False)
    step += 1

    if not args.ondev_no_rootfs:
        create_device_rootfs(args, step, steps)
        step += 1

    if args.no_image:
        return
    elif args.android_recovery_zip:
        return install_recovery_zip(args, device, deviceinfo.arch, steps)

    if args.on_device_installer:
        # Runs install_system_image twice
        install_on_device_installer(args, step, steps)
    else:
        install_system_image(args, 0, chroot, step, steps, split=args.split, disk=args.disk)

    print_flash_info(
        device, deviceinfo, args.split, True if args.disk and args.disk.is_absolute() else False
    )
    print_sshd_info(args)
    print_firewall_info(args.no_firewall, deviceinfo.arch)

    # Leave space before 'chroot still active' note
    logging.info("")
