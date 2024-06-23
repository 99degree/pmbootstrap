# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import json
from collections.abc import Sequence
from pmb.core.arch import Arch
from pmb.helpers import logging
import os
from pathlib import Path
import sys
from typing import Any

import pmb.aportgen
import pmb.build
import pmb.build.autodetect
import pmb.chroot
import pmb.chroot.apk
import pmb.chroot.initfs
import pmb.chroot.other
import pmb.ci
import pmb.config
from pmb.core import Config
from pmb.types import PmbArgs
import pmb.export
import pmb.flasher
import pmb.helpers.aportupgrade
import pmb.helpers.devices
import pmb.helpers.git
import pmb.helpers.lint
import pmb.helpers.logging
import pmb.helpers.pkgrel_bump
import pmb.helpers.pmaports
import pmb.helpers.repo
import pmb.helpers.repo_missing
import pmb.helpers.run
import pmb.helpers.status
import pmb.install
import pmb.install.blockdevice
import pmb.netboot
import pmb.parse
import pmb.qemu
import pmb.sideload
from pmb.core import ChrootType, Chroot
from pmb.core.context import get_context


def _parse_flavor(device: str, autoinstall=True):
    """Verify the flavor argument if specified, or return a default value.

    :param autoinstall: make sure that at least one kernel flavor is installed
    """
    # Install a kernel and get its "flavor", where flavor is a pmOS-specific
    # identifier that is typically in the form
    # "postmarketos-<manufacturer>-<device/chip>", e.g.
    # "postmarketos-qcom-sdm845"
    chroot = Chroot(ChrootType.ROOTFS, device)
    flavor = pmb.chroot.other.kernel_flavor_installed(chroot, autoinstall)

    if not flavor:
        raise RuntimeError(
            f"No kernel flavors installed in chroot '{chroot}'! Please let"
            " your device package depend on a package starting with 'linux-'."
        )
    return flavor


def _parse_suffix(args: PmbArgs) -> Chroot:
    deviceinfo = pmb.parse.deviceinfo()
    if args.image:
        rootfs = Chroot.native() / f"home/pmos/rootfs/{deviceinfo.codename}.img"
        return Chroot(ChrootType.IMAGE, str(rootfs))
    if "rootfs" in args and args.rootfs:
        return Chroot(ChrootType.ROOTFS, get_context().config.device)
    elif args.buildroot:
        if args.buildroot == "device":
            return Chroot.buildroot(deviceinfo.arch)
        else:
            return Chroot.buildroot(Arch.from_str(args.buildroot))
    elif args.suffix:
        (_t, s) = args.suffix.split("_")
        t: ChrootType = ChrootType(_t)
        return Chroot(t, s)
    else:
        return Chroot(ChrootType.NATIVE)


def _install_ondev_verify_no_rootfs(device: str, ondev_cp: list[tuple[str, str]]):
    chroot_dest = "/var/lib/rootfs.img"
    dest = Chroot(ChrootType.INSTALLER, device) / chroot_dest
    if dest.exists():
        return

    if ondev_cp:
        for _, chroot_dest_cp in ondev_cp:
            if chroot_dest_cp == chroot_dest:
                return

    raise ValueError(
        f"--no-rootfs set, but rootfs.img not found in install"
        " chroot. Either run 'pmbootstrap install' without"
        " --no-rootfs first to let it generate the postmarketOS"
        " rootfs once, or supply a rootfs file with:"
        f" --cp os.img:{chroot_dest}"
    )


def aportgen(args: PmbArgs):
    for package in args.packages:
        logging.info("Generate aport: " + package)
        pmb.aportgen.generate(package, args.fork_alpine)


def build(args: PmbArgs):
    # Strict mode: zap everything
    if args.strict:
        pmb.chroot.zap(False)

    if args.envkernel:
        pmb.build.envkernel.package_kernel(args)
        return

    # Set src and force
    src = os.path.realpath(os.path.expanduser(args.src[0])) if args.src else None
    force = True if src else get_context().force
    if src and not os.path.exists(src):
        raise RuntimeError("Invalid path specified for --src: " + src)

    # Ensure repo_bootstrap is done for all arches we intend to build for
    for package in args.packages:
        arch_package = args.arch or pmb.build.autodetect.arch(package)
        pmb.helpers.pmaports.require_bootstrap(arch_package, f"build {package} for {arch_package}")

    context = get_context()
    # Build all packages
    built = pmb.build.packages(
        context, args.packages, args.arch, force, strict=args.strict, src=src
    )

    # Notify about packages that weren't built
    for package in set(args.packages) - set(built):
        logging.info(
            "NOTE: Package '" + package + "' is up to date. Use"
            " 'pmbootstrap build " + package + " --force'"
            " if needed."
        )


def build_init(args: PmbArgs):
    chroot = _parse_suffix(args)
    pmb.build.init(chroot)


def checksum(args: PmbArgs):
    for package in args.packages:
        if args.verify:
            pmb.build.checksum.verify(package)
        else:
            pmb.build.checksum.update(package)


def sideload(args: PmbArgs):
    arch = args.arch
    user = get_context().config.user
    host = args.host
    pmb.sideload.sideload(args, user, host, args.port, arch, args.install_key, args.packages)


def netboot(args: PmbArgs):
    if args.action_netboot == "serve":
        device = get_context().config.device
        pmb.netboot.start_nbd_server(device, args.replace)


def chroot(args: PmbArgs):
    # Suffix
    chroot = _parse_suffix(args)
    user = args.user
    if (
        user
        and chroot != Chroot.native()
        and chroot.type not in [ChrootType.BUILDROOT, ChrootType.IMAGE]
    ):
        raise RuntimeError("--user is only supported for native or" " buildroot_* chroots.")
    if args.xauth and chroot != Chroot.native():
        raise RuntimeError("--xauth is only supported for native chroot.")

    if chroot.type == ChrootType.IMAGE:
        pmb.chroot.mount(chroot)

    # apk: check minimum version, install packages
    pmb.chroot.apk.check_min_version(chroot)
    if args.add:
        pmb.chroot.apk.install(args.add.split(","), chroot)

    pmb.chroot.init(chroot)

    # Xauthority
    env = {}
    if args.xauth:
        pmb.chroot.other.copy_xauthority(args)
        env["DISPLAY"] = os.environ.get("DISPLAY")
        env["XAUTHORITY"] = "/home/pmos/.Xauthority"

    # Install blockdevice
    if args.install_blockdev:
        logging.warning(
            "--install-blockdev is deprecated for the chroot command"
            " and will be removed in a future release. If you need this"
            " for some reason, please open an issue on"
            " https://gitlab.com/postmarketOS/pmbootstrap.git"
        )
        size_boot = 128  # 128 MiB
        size_root = 4096  # 4 GiB
        size_reserve = 2048  # 2 GiB
        pmb.install.blockdevice.create_and_mount_image(args, size_boot, size_root, size_reserve)

    pmb.chroot.apk.update_repository_list(chroot, user_repository=True)

    # Run the command as user/root
    if user:
        logging.info(f"({chroot}) % su pmos -c '" + " ".join(args.command) + "'")
        pmb.chroot.user(args.command, chroot, output=args.output, env=env)
    else:
        logging.info(f"({chroot}) % " + " ".join(args.command))
        pmb.chroot.root(args.command, chroot, output=args.output, env=env)


def config(args: PmbArgs):
    keys = Config.keys()
    if args.name and args.name not in keys:
        logging.info("NOTE: Valid config keys: " + ", ".join(keys))
        raise RuntimeError("Invalid config key: " + args.name)

    # Reload the config because get_context().config has been overwritten
    # by any rogue cmdline arguments.
    config = pmb.config.load(args.config)
    if args.reset:
        if args.name is None:
            raise RuntimeError("config --reset requires a name to be given.")
        def_value = Config.get_default(args.name)
        setattr(config, args.name, def_value)
        logging.info(f"Config changed to default: {args.name}='{def_value}'")
        pmb.config.save(args.config, config)
    elif args.value is not None:
        setattr(config, args.name, args.value)
        logging.info("Config changed: " + args.name + "='" + args.value + "'")
        pmb.config.save(args.config, config)
    elif args.name:
        if hasattr(config, args.name):
            value = getattr(config, args.name)
        else:
            value = ""

        def to_shell_friendly_representation(value: Any) -> str:
            friendly_representation: str

            if isinstance(value, list) and len(value) == 1:
                value = value[0]

            if isinstance(value, Path):
                friendly_representation = value.as_posix()
            else:
                friendly_representation = str(value)

            return friendly_representation

        print(to_shell_friendly_representation(value))
    else:
        # Serialize the entire config including default values for
        # the user. Even though the defaults aren't actually written
        # to disk.
        cfg = pmb.config.serialize(config, skip_defaults=False)
        cfg.write(sys.stdout)

    # Don't write the "Done" message
    pmb.helpers.logging.disable()


def repo_missing(args: PmbArgs):
    missing = pmb.helpers.repo_missing.generate(args.arch, args.overview, args.package, args.built)
    print(json.dumps(missing, indent=4))


def initfs(args: PmbArgs):
    pmb.chroot.initfs.frontend(args)


def install(args: PmbArgs):
    config = get_context().config
    device = config.device
    deviceinfo = pmb.parse.deviceinfo(device)
    if args.no_fde:
        logging.warning("WARNING: --no-fde is deprecated," " as it is now the default.")
    if args.rsync and args.full_disk_encryption:
        raise ValueError("Installation using rsync is not compatible with full" " disk encryption.")
    if args.rsync and not args.disk:
        raise ValueError("Installation using rsync only works with --disk.")

    if args.rsync and args.filesystem == "btrfs":
        raise ValueError(
            "Installation using rsync" " is not currently supported on btrfs filesystem."
        )

    pmb.helpers.pmaports.require_bootstrap(
        deviceinfo.arch, f"do 'pmbootstrap install' for {deviceinfo.arch}" " (deviceinfo_arch)"
    )

    # On-device installer checks
    # Note that this can't be in the mutually exclusive group that has most of
    # the conflicting options, because then it would not work with --disk.
    if args.on_device_installer:
        if args.full_disk_encryption:
            raise ValueError(
                "--on-device-installer cannot be combined with"
                " --fde. The user can choose to encrypt their"
                " installation later in the on-device installer."
            )
        if args.android_recovery_zip:
            raise ValueError(
                "--on-device-installer cannot be combined with"
                " --android-recovery-zip (patches welcome)"
            )
        if args.no_image:
            raise ValueError("--on-device-installer cannot be combined with" " --no-image")
        if args.rsync:
            raise ValueError("--on-device-installer cannot be combined with" " --rsync")
        if args.filesystem:
            raise ValueError("--on-device-installer cannot be combined with" " --filesystem")

        if deviceinfo.cgpt_kpart:
            raise ValueError("--on-device-installer cannot be used with" " ChromeOS devices")
    else:
        if args.ondev_cp:
            raise ValueError("--cp can only be combined with --ondev")
        if args.ondev_no_rootfs:
            raise ValueError(
                "--no-rootfs can only be combined with --ondev." " Do you mean --no-image?"
            )
    if args.ondev_no_rootfs:
        _install_ondev_verify_no_rootfs(device, args.ondev_cp)

    # On-device installer overrides
    if args.on_device_installer:
        # To make code for the on-device installer not needlessly complex, just
        # hardcode "user" as username here. (The on-device installer will set
        # a password for the user, disable SSH password authentication,
        # optionally add a new user for SSH that must not have the same
        # username etc.)
        if config.user != "user":
            logging.warning(
                f"WARNING: custom username '{config.user}' will be"
                " replaced with 'user' for the on-device"
                " installer."
            )
            config.user = "user"

    if not args.disk and args.split is None:
        # Default to split if the flash method requires it
        flasher = pmb.config.flashers.get(deviceinfo.flash_method, {})
        if flasher.get("split", False):
            args.split = True

    # Android recovery zip related
    if args.android_recovery_zip and args.filesystem:
        raise ValueError(
            "--android-recovery-zip cannot be combined with" " --filesystem (patches welcome)"
        )
    if args.android_recovery_zip and args.full_disk_encryption:
        logging.info(
            "WARNING: --fde is rarely used in combination with"
            " --android-recovery-zip. If this does not work, consider"
            " using another method (e.g. installing via netcat)"
        )
        logging.info(
            "WARNING: the kernel of the recovery system (e.g. TWRP)"
            f" must support the cryptsetup cipher '{args.cipher}'."
        )
        logging.info(
            "If you know what you are doing, consider setting a"
            " different cipher with 'pmbootstrap install --cipher=..."
            " --fde --android-recovery-zip'."
        )

    # Don't install locally compiled packages and package signing keys
    if not args.install_local_pkgs:
        # Implies that we don't build outdated packages (overriding the answer
        # in 'pmbootstrap init')
        config.build_pkgs_on_install = False

        # Safest way to avoid installing local packages is having none
        if (config.work / "packages").glob("*"):
            raise ValueError(
                "--no-local-pkgs specified, but locally built"
                " packages found. Consider 'pmbootstrap zap -p'"
                " to delete them."
            )

    # Verify that the root filesystem is supported by current pmaports branch
    pmb.install.get_root_filesystem(args)

    pmb.install.install(args)


def flasher(args: PmbArgs):
    pmb.flasher.frontend(args)


def export(args: PmbArgs):
    pmb.export.frontend(args)


def update(args: PmbArgs):
    existing_only = not args.non_existing
    if not pmb.helpers.repo.update(args.arch, True, existing_only):
        logging.info(
            "No APKINDEX files exist, so none have been updated."
            " The pmbootstrap command downloads the APKINDEX files on"
            " demand."
        )
        logging.info(
            "If you want to force downloading the APKINDEX files for"
            " all architectures (not recommended), use:"
            " pmbootstrap update --non-existing"
        )


def newapkbuild(args: PmbArgs):
    # Check for SRCURL usage
    is_url = False
    for prefix in ["http://", "https://", "ftp://"]:
        if args.pkgname_pkgver_srcurl.startswith(prefix):
            is_url = True
            break

    # Sanity check: -n is only allowed with SRCURL
    if args.pkgname and not is_url:
        raise RuntimeError(
            "You can only specify a pkgname (-n) when using" " SRCURL as last parameter."
        )

    # Passthrough: Strings (e.g. -d "my description")
    pass_through = []
    for entry in pmb.config.newapkbuild_arguments_strings:
        value = getattr(args, entry[1])
        if value:
            pass_through += [entry[0], value]

    # Passthrough: Switches (e.g. -C for CMake)
    for entry in (
        pmb.config.newapkbuild_arguments_switches_pkgtypes
        + pmb.config.newapkbuild_arguments_switches_other
    ):
        if getattr(args, entry[1]) is True:
            pass_through.append(entry[0])

    # Passthrough: PKGNAME[-PKGVER] | SRCURL
    pass_through.append(args.pkgname_pkgver_srcurl)
    pmb.build.newapkbuild(args.folder, pass_through, args.force)


def deviceinfo_parse(args: PmbArgs):
    # Default to all devices
    devices = args.devices
    if not devices:
        devices = pmb.helpers.devices.list_codenames()

    # Iterate over all devices
    kernel = args.deviceinfo_parse_kernel
    for device in devices:
        print(f"{device}, with kernel={kernel}:")
        print(json.dumps(pmb.parse.deviceinfo(device, kernel), indent=4, sort_keys=True))


def apkbuild_parse(args: PmbArgs):
    # Default to all packages
    packages: Sequence[str] = args.packages
    if not packages:
        packages = pmb.helpers.pmaports.get_list()

    # Iterate over all packages
    for package in packages:
        print(package + ":")
        aport = pmb.helpers.pmaports.find(package)
        print(json.dumps(pmb.parse.apkbuild(aport), indent=4, sort_keys=True))


def apkindex_parse(args: PmbArgs):
    result = pmb.parse.apkindex.parse(args.apkindex_path)
    if args.package:
        if args.package not in result:
            raise RuntimeError(f"Package not found in the APKINDEX: {args.package}")
        result = result[args.package]
    print(json.dumps(result, indent=4))


def pkgrel_bump(args: PmbArgs):
    would_bump = True
    if args.auto:
        would_bump = pmb.helpers.pkgrel_bump.auto(args, args.dry)
    else:
        # Each package must exist
        for package in args.packages:
            pmb.helpers.pmaports.find(package)

        # Increase pkgrel
        for package in args.packages:
            pmb.helpers.pkgrel_bump.package(args, package, dry=args.dry)

    if args.dry and would_bump:
        logging.info("Pkgrels of package(s) would have been bumped!")
        sys.exit(1)


def aportupgrade(args: PmbArgs):
    if args.all or args.all_stable or args.all_git:
        pmb.helpers.aportupgrade.upgrade_all(args)
    else:
        # Each package must exist
        for package in args.packages:
            pmb.helpers.pmaports.find(package)

        # Check each package for a new version
        for package in args.packages:
            pmb.helpers.aportupgrade.upgrade(args, package)


def qemu(args: PmbArgs):
    pmb.qemu.run(args)


def stats(args: PmbArgs):
    # Chroot suffix
    chroot = Chroot.buildroot(args.arch or Arch.native())

    # Install ccache and display stats
    pmb.chroot.apk.install(["ccache"], chroot)
    logging.info(f"({chroot}) % ccache -s")
    pmb.chroot.user(["ccache", "-s"], chroot, output="stdout")


def work_migrate(args: PmbArgs):
    # do nothing (pmb/__init__.py already did the migration)
    pmb.helpers.logging.disable()


def zap(args: PmbArgs):
    pmb.chroot.zap(
        dry=args.dry,
        http=args.http,
        distfiles=args.distfiles,
        pkgs_local=args.pkgs_local,
        pkgs_local_mismatch=args.pkgs_local_mismatch,
        pkgs_online_mismatch=args.pkgs_online_mismatch,
        rust=args.rust,
        netboot=args.netboot,
    )

    # Don't write the "Done" message
    pmb.helpers.logging.disable()


def bootimg_analyze(args: PmbArgs):
    bootimg = pmb.parse.bootimg(args.path)
    tmp_output = "Put these variables in the deviceinfo file of your device:\n"
    for line in pmb.aportgen.device.generate_deviceinfo_fastboot_content(bootimg).split("\n"):
        tmp_output += "\n" + line.lstrip()
    logging.info(tmp_output)


def lint(args: PmbArgs):
    packages: Sequence[str] = args.packages
    if not packages:
        packages = pmb.helpers.pmaports.get_list()

    pmb.helpers.lint.check(packages)


def status(args: PmbArgs) -> None:
    pmb.helpers.status.print_status(args)

    # Do not print the DONE! line
    sys.exit(0)


def ci(args: PmbArgs):
    topdir = pmb.helpers.git.get_topdir(Path.cwd())
    if not os.path.exists(topdir):
        logging.error(
            "ERROR: change your current directory to a git"
            " repository (e.g. pmbootstrap, pmaports) before running"
            " 'pmbootstrap ci'."
        )
        exit(1)

    scripts_available = pmb.ci.get_ci_scripts(topdir)
    scripts_available = pmb.ci.sort_scripts_by_speed(scripts_available)
    if not scripts_available:
        logging.error(
            "ERROR: no supported CI scripts found in current git"
            " repository, see https://postmarketos.org/pmb-ci"
        )
        exit(1)

    scripts_selected = {}
    if args.scripts:
        if args.all:
            raise RuntimeError("Combining --all with script names doesn't" " make sense")
        for script in args.scripts:
            if script not in scripts_available:
                logging.error(
                    f"ERROR: script '{script}' not found in git"
                    " repository, found these:"
                    f" {', '.join(scripts_available.keys())}"
                )
                exit(1)
            scripts_selected[script] = scripts_available[script]
    elif args.all:
        scripts_selected = scripts_available

    if args.fast:
        for script, script_data in scripts_available.items():
            if "slow" not in script_data["options"]:
                scripts_selected[script] = script_data

    if not pmb.helpers.git.clean_worktree(topdir):
        logging.warning("WARNING: this git repository has uncommitted changes")

    if not scripts_selected:
        scripts_selected = pmb.ci.ask_which_scripts_to_run(scripts_available)

    pmb.ci.run_scripts(topdir, scripts_selected)
