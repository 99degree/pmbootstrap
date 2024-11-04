# Copyright 2023 Robert Yang
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.core.arch import Arch
from pmb.core.context import Context
from pmb.helpers import logging
import os
from pathlib import Path
import re

import pmb.aportgen
import pmb.aportgen.core
import pmb.build
import pmb.build.autodetect
import pmb.chroot
from pmb.types import Env, PathString, PmbArgs
import pmb.helpers
import pmb.helpers.mount
import pmb.helpers.pmaports
import pmb.parse
from pmb.core import Chroot
from pmb.core.context import get_context


def match_kbuild_out(word):
    """Look for paths in the following formats:
      "<prefix>/<kbuild_out>/arch/<arch>/boot"
      "<prefix>/<kbuild_out>/include/config/kernel.release"

    :param word: space separated string cut out from a line from an APKBUILD
                 function body that might be the kbuild output path
    :returns: kernel build output directory.
              empty string when a separate build output directory isn't used.
              None, when no output directory is found.
    """
    prefix = '^\\"?\\$({?builddir}?|{?srcdir}?)\\"?/'
    kbuild_out = "(.*\\/)*"

    postfix = '(arch\\/.*\\/boot.*)\\"?$'
    match = re.match(prefix + kbuild_out + postfix, word)

    if match is None:
        postfix = '(include\\/config\\/kernel\\.release)\\"?$'
        match = re.match(prefix + kbuild_out + postfix, word)

    if match is None:
        return None

    groups = match.groups()
    if groups is None or len(groups) != 3:
        return None

    logging.debug("word = " + str(word))
    logging.debug("regex match groups = " + str(groups))
    out_dir = groups[1]
    return "" if out_dir is None else out_dir.strip("/")


def find_kbuild_output_dir(function_body):
    """Guess what the kernel build output directory is.

    Parses each line of the function word by word, looking for paths which
    contain the kbuild output directory.

    :param function_body: contents of a function from the kernel APKBUILD
    :returns: kbuild output dir
              None, when output dir is not found
    """
    guesses = []
    for line in function_body:
        for item in line.split():
            # Guess that any APKBUILD using downstreamkernel_package
            # uses the default kbuild out directory.
            if item == "downstreamkernel_package":
                guesses.append("")
                break
            kbuild_out = match_kbuild_out(item)
            if kbuild_out is not None:
                guesses.append(kbuild_out)
                break

    # Check if guesses are all the same
    it = iter(guesses)
    first = next(it, None)
    if first is None:
        raise RuntimeError(
            "Couldn't find a kbuild out directory. Is your "
            "APKBUILD messed up? If not, then consider "
            "adjusting the patterns in pmb/build/envkernel.py "
            "to work with your APKBUILD, or submit an issue."
        )
    if all(first == rest for rest in it):
        return first
    raise RuntimeError(
        "Multiple kbuild out directories found. Can you modify "
        "your APKBUILD so it only has one output path? If you "
        "can't resolve it, please open an issue."
    )


def modify_apkbuild(pkgname: str, aport: Path) -> None:
    """Modify kernel APKBUILD to package build output from envkernel.sh."""
    work = get_context().config.work
    apkbuild_path = aport / "APKBUILD"
    apkbuild = pmb.parse.apkbuild(apkbuild_path)
    if os.path.exists(work / "aportgen"):
        pmb.helpers.run.user(["rm", "-r", work / "aportgen"])

    pmb.helpers.run.user(["mkdir", work / "aportgen"])
    pmb.helpers.run.user(["cp", "-r", apkbuild_path, work / "aportgen"])

    pkgver = pmb.build._package.get_pkgver(apkbuild["pkgver"], original_source=False)
    fields = {
        "pkgver": pkgver,
        "pkgrel": "0",
        "subpackages": "",
        "builddir": "/home/pmos/build/src",
    }

    pmb.aportgen.core.rewrite(pkgname, apkbuild_path, fields=fields)


def run_abuild(
    context: Context, pkgname: str, arch: Arch, apkbuild_path: Path, kbuild_out: str
) -> None:
    """
    Prepare build environment and run abuild.

    :param pkgname: package name of a linux kernel aport
    :param arch: architecture for the kernel
    :param apkbuild_path: path to APKBUILD of the kernel aport
    :param kbuild_out: kernel build system output sub-directory
    """
    chroot = Chroot.native()
    build_path = Path("/home/pmos/build")
    kbuild_out_source = "/mnt/linux/.output"

    # If the kernel was cross-compiled on the host rather than with the envkernel
    # helper, we can still use the envkernel logic to package the artifacts for
    # development, making it easy to quickly sideload a new kernel or pmbootstrap
    # to create a boot image.

    pmb.helpers.mount.bind(Path("."), chroot / "mnt/linux")

    if not os.path.exists(chroot / kbuild_out_source):
        raise RuntimeError(
            "No '.output' dir found in your kernel source dir. "
            "Compile the " + context.config.device + " kernel first and "
            "then try again. See https://postmarketos.org/envkernel"
            "for details. If building on your host and only using "
            "--envkernel for packaging, make sure you have O=.output "
            "as an argument to make."
        )

    # Create working directory for abuild
    pmb.build.copy_to_buildpath(pkgname)

    # FIXME: duplicated from pmb.build._package.run_aports()
    # This is needed to set up the package output directory for
    # abuild and shouldn't really be done here.
    channel = pmb.config.pmaports.read_config()["channel"]
    pkgdir = context.config.work / "packages" / channel
    if not pkgdir.exists():
        pmb.helpers.run.root(["mkdir", "-p", pkgdir])
        pmb.helpers.run.root(
            [
                "chown",
                "-R",
                f"{pmb.config.chroot_uid_user}:{pmb.config.chroot_uid_user}",
                pkgdir.parent,
            ]
        )

    pmb.chroot.rootm(
        [
            ["mkdir", "-p", "/home/pmos/packages"],
            ["rm", "-f", "/home/pmos/packages/pmos"],
            ["ln", "-sf", f"/mnt/pmbootstrap/packages/{channel}", "/home/pmos/packages/pmos"],
        ],
        chroot,
    )

    # Create symlink from abuild working directory to envkernel build directory
    if kbuild_out != "":
        if os.path.islink(chroot / "mnt/linux" / kbuild_out) and os.path.lexists(
            chroot / "mnt/linux" / kbuild_out
        ):
            pmb.chroot.root(["rm", Path("/mnt/linux", kbuild_out)])
        pmb.chroot.root(["ln", "-s", "/mnt/linux", build_path / "src"])
    pmb.chroot.root(["ln", "-s", kbuild_out_source, build_path / "src" / kbuild_out])

    cmd: list[PathString] = ["cp", apkbuild_path, chroot / build_path / "APKBUILD"]
    pmb.helpers.run.root(cmd)

    # Create the apk package
    env: Env = {
        "CARCH": str(arch),
        "CHOST": str(arch),
        "CBUILD": str(Arch.native()),
        "SUDO_APK": "abuild-apk --no-progress",
    }
    cmd = ["abuild", "rootpkg"]
    pmb.chroot.user(cmd, working_dir=build_path, env=env)

    # Clean up bindmount
    pmb.helpers.mount.umount_all(chroot / "mnt/linux")

    # Clean up symlinks
    if kbuild_out != "":
        if os.path.islink(chroot / "mnt/linux" / kbuild_out) and os.path.lexists(
            chroot / "mnt/linux" / kbuild_out
        ):
            pmb.chroot.root(["rm", Path("/mnt/linux", kbuild_out)])
    pmb.chroot.root(["rm", build_path / "src"])


def package_kernel(args: PmbArgs) -> None:
    """Frontend for 'pmbootstrap build --envkernel': creates a package from envkernel output."""
    pkgname = args.packages[0]
    if len(args.packages) > 1 or not pkgname.startswith("linux-"):
        raise RuntimeError("--envkernel needs exactly one linux-* package as " "argument.")

    aport = pmb.helpers.pmaports.find(pkgname)
    context = get_context()

    modify_apkbuild(pkgname, aport)
    apkbuild_path = context.config.work / "aportgen/APKBUILD"

    arch = pmb.parse.deviceinfo().arch
    apkbuild = pmb.parse.apkbuild(apkbuild_path, check_pkgname=False)
    if apkbuild["_outdir"]:
        kbuild_out = apkbuild["_outdir"]
    else:
        function_body = pmb.parse.function_body(aport / "APKBUILD", "package")
        kbuild_out = find_kbuild_output_dir(function_body)
    if "pmb:cross-native" in apkbuild["options"]:
        chroot = Chroot.native()
    else:
        chroot = pmb.build.autodetect.chroot(apkbuild, arch)

    # Install package dependencies
    depends = pmb.build.get_depends(context, apkbuild)
    pmb.build.init(chroot)
    if arch.cpu_emulation_required():
        depends.append(f"binutils-{arch}")
    pmb.chroot.apk.install(depends, chroot)

    output = pmb.build.output_path(
        arch, apkbuild["pkgname"], apkbuild["pkgver"], apkbuild["pkgrel"]
    )
    message = f"({chroot}) build {output}"
    logging.info(message)

    try:
        run_abuild(context, pkgname, arch, apkbuild_path, kbuild_out)
    except Exception as e:
        pmb.helpers.mount.umount_all(Chroot.native() / "mnt/linux")
        raise e
    pmb.build.other.index_repo(arch)
