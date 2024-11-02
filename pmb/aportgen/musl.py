# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import pmb.aportgen.core
import pmb.build
import pmb.chroot.apk
import pmb.helpers.run
import pmb.parse.apkindex
from pmb.core import Chroot
from pmb.core.arch import Arch
from pmb.core.context import get_context


def generate(pkgname: str) -> None:
    arch = Arch.from_str(pkgname.split("-")[1])

    # Parse musl version from APKINDEX
    package_data = pmb.parse.apkindex.package("musl")
    if package_data is None:
        raise RuntimeError("Couldn't find package musl!")
    version = package_data.version
    pkgver = version.split("-r")[0]
    pkgrel = version.split("-r")[1]

    # Prepare aportgen tempdir inside and outside of chroot
    work = get_context().config.work
    tempdir = Path("/tmp/aportgen")
    aportgen = work / "aportgen"
    pmb.chroot.root(["rm", "-rf", tempdir])
    pmb.helpers.run.user(["mkdir", "-p", aportgen, Chroot.native() / tempdir])

    # Write the APKBUILD
    channel_cfg = pmb.config.pmaports.read_config_channel()
    mirrordir = channel_cfg["mirrordir_alpine"]
    apkbuild_path = Chroot.native() / tempdir / "APKBUILD"
    apk_name = f"$srcdir/musl-$pkgver-r$pkgrel-$_arch-{mirrordir}.apk"
    apk_dev_name = f"$srcdir/musl-dev-$pkgver-r$pkgrel-$_arch-{mirrordir}.apk"
    with open(apkbuild_path, "w", encoding="utf-8") as handle:
        apkbuild = f"""\
            # Automatically generated aport, do not edit!
            # Generator: pmbootstrap aportgen {pkgname}

            # Stub for apkbuild-lint
            if [ -z "$(type -t arch_to_hostspec)" ]; then
                arch_to_hostspec() {{ :; }}
            fi

            pkgname={pkgname}
            pkgver={pkgver}
            pkgrel={pkgrel}
            arch="{pmb.aportgen.get_cross_package_arches(pkgname)}"
            subpackages="musl-dev-{arch}:package_dev"

            _arch="{arch}"
            _mirror="{pmb.config.aportgen_mirror_alpine}"

            url="https://musl-libc.org"
            license="MIT"
            options="!check !strip"
            pkgdesc="the musl library (lib c) implementation for $_arch"

            _target="$(arch_to_hostspec $_arch)"

            source="
                musl-$pkgver-r$pkgrel-$_arch-{mirrordir}.apk::$_mirror/{mirrordir}/main/$_arch/musl-$pkgver-r$pkgrel.apk
                musl-dev-$pkgver-r$pkgrel-$_arch-{mirrordir}.apk::$_mirror/{mirrordir}/main/$_arch/musl-dev-$pkgver-r$pkgrel.apk
            "

            package() {{
                mkdir -p "$pkgdir/usr/$_target"
                cd "$pkgdir/usr/$_target"
                # Use 'busybox tar' to avoid 'tar: Child returned status 141'
                # on some machines (builds.sr.ht, gitlab-ci). See pmaports#26.
                busybox tar -xf {apk_name}
                rm .PKGINFO .SIGN.*
            }}
            package_dev() {{
                mkdir -p "$subpkgdir/usr/$_target"
                cd "$subpkgdir/usr/$_target"
                # Use 'busybox tar' to avoid 'tar: Child returned status 141'
                # on some machines (builds.sr.ht, gitlab-ci). See pmaports#26.
                busybox tar -xf {apk_dev_name}
                rm .PKGINFO .SIGN.*

                # symlink everything from /usr/$_target/usr/*
                # to /usr/$_target/* so the cross-compiler gcc does not fail
                # to build.
                for _dir in include lib; do
                    mkdir -p "$subpkgdir/usr/$_target/$_dir"
                    cd "$subpkgdir/usr/$_target/usr/$_dir"
                    for i in *; do
                        cd "$subpkgdir/usr/$_target/$_dir"
                        ln -s /usr/$_target/usr/$_dir/$i $i
                    done
                done
            }}
        """
        for line in apkbuild.split("\n"):
            handle.write(line[12:].replace(" " * 4, "\t") + "\n")

    # Generate checksums
    pmb.build.init_abuild_minimal()
    pmb.chroot.root(["chown", "-R", "pmos:pmos", tempdir])
    pmb.chroot.user(["abuild", "checksum"], working_dir=tempdir)
    pmb.helpers.run.user(["cp", apkbuild_path, aportgen])
