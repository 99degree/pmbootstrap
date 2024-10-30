# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.core.context import get_context
from pmb.parse.deviceinfo import Deviceinfo
import pmb.helpers.run
import pmb.aportgen.core
import pmb.parse.apkindex


def generate_apkbuild(pkgname: str, deviceinfo: Deviceinfo, patches: list[str]) -> None:
    device = "-".join(pkgname.split("-")[1:])
    carch = deviceinfo.arch.kernel()

    makedepends = [
        "bash",
        "bc",
        "bison",
        "devicepkg-dev",
        "findutils",
        "flex",
        "openssl-dev",
        "perl",
    ]

    build = """
            unset LDFLAGS
            make O="$_outdir" ARCH="$_carch" CC="${CC:-gcc}" \\
                KBUILD_BUILD_VERSION="$((pkgrel + 1 ))-postmarketOS\""""

    package = """
            downstreamkernel_package "$builddir" "$pkgdir" "$_carch\" \\
                "$_flavor" "$_outdir\""""

    if deviceinfo.header_version == "2":
        package += """

            make dtbs_install O="$_outdir" ARCH="$_carch" \\
                INSTALL_DTBS_PATH="$pkgdir\"/boot/dtbs"""

    if deviceinfo.bootimg_qcdt == "true":
        build += """\n
            # Master DTB (deviceinfo_bootimg_qcdt)"""
        vendors = ["spreadtrum", "exynos", "other"]
        soc_vendor = pmb.helpers.cli.ask("SoC vendor", vendors, vendors[-1], complete=vendors)
        if soc_vendor == "spreadtrum":
            makedepends.append("dtbtool-sprd")
            build += """
            dtbTool-sprd -p "$_outdir/scripts/dtc/" \\
                -o "$_outdir/arch/$_carch/boot"/dt.img \\
                "$_outdir/arch/$_carch/boot/dts/\""""
        elif soc_vendor == "exynos":
            codename = "-".join(pkgname.split("-")[2:])
            makedepends.append("dtbtool-exynos")
            build += """
            dtbTool-exynos -o "$_outdir/arch/$_carch/boot"/dt.img \\
                $(find "$_outdir/arch/$_carch/boot/dts/\""""
            build += f" -name *{codename}*.dtb)"
        else:
            makedepends.append("dtbtool")
            build += """
            dtbTool -o "$_outdir/arch/$_carch/boot"/dt.img \\
                "$_outdir/arch/$_carch/boot/\""""
        package += """
            install -Dm644 "$_outdir/arch/$_carch/boot"/dt.img \\
                "$pkgdir"/boot/dt.img"""

    makedepends.sort()
    makedepends_fmt = ("\n" + " " * 12).join(makedepends)
    patches_str = ("\n" + " " * 12).join(patches)
    content = f"""\
        # Reference: <https://postmarketos.org/vendorkernel>
        # Kernel config based on: arch/{carch}/configs/(CHANGEME!)

        pkgname={pkgname}
        pkgver=3.x.x
        pkgrel=0
        pkgdesc="{deviceinfo.name} kernel fork"
        arch="{deviceinfo.arch}"
        _carch="{carch}"
        _flavor="{device}"
        url="https://kernel.org"
        license="GPL-2.0-only"
        options="!strip !check !tracedeps pmb:cross-native"
        makedepends="
            {makedepends_fmt}
        "

        # Source
        _repository="(CHANGEME!)"
        _commit="ffffffffffffffffffffffffffffffffffffffff"
        _config="config-$_flavor.$arch"
        source="
            $pkgname-$_commit.tar.gz::https://github.com/LineageOS/$_repository/archive/$_commit.tar.gz
            $_config
            {patches_str}
        "
        builddir="$srcdir/$_repository-$_commit"
        _outdir="out"

        prepare() {{
            default_prepare
            . downstreamkernel_prepare
        }}

        build() {{{build}
        }}

        package() {{{package}
        }}

        sha512sums="(run 'pmbootstrap checksum {pkgname}' to fill)"
        """

    # Write the file
    with (get_context().config.work / "aportgen/APKBUILD").open("w", encoding="utf-8") as hndl:
        for line in content.rstrip().split("\n"):
            hndl.write(line[8:].replace(" " * 4, "\t") + "\n")


def generate(pkgname: str) -> None:
    device = "-".join(pkgname.split("-")[1:])
    deviceinfo = pmb.parse.deviceinfo(device)
    work = get_context().config.work

    # Symlink commonly used patches
    pmb.helpers.run.user(["mkdir", "-p", work / "aportgen"])
    patches = [
        "gcc7-give-up-on-ilog2-const-optimizations.patch",
        "gcc8-fix-put-user.patch",
        "gcc10-extern_YYLOC_global_declaration.patch",
        "kernel-use-the-gnu89-standard-explicitly.patch",
    ]
    for patch in patches:
        pmb.helpers.run.user(
            ["ln", "-s", "../../.shared-patches/linux/" + patch, (work / "aportgen" / patch)]
        )

    generate_apkbuild(pkgname, deviceinfo, patches)
