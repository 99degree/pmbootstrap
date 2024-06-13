# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import datetime
import enum
from typing import Any, Callable, Dict, List, Optional, Set, TypedDict
from pmb.core.arch import Arch
from pmb.core.context import Context
from pmb.core.pkgrepo import pkgrepo_paths, pkgrepo_relative_path
from pmb.helpers import logging
from pathlib import Path

import pmb.build
import pmb.build.autodetect
import pmb.chroot
import pmb.chroot.apk
import pmb.config.pmaports
import pmb.helpers.pmaports
import pmb.helpers.repo
import pmb.helpers.mount
import pmb.helpers.package
import pmb.parse
import pmb.parse.apkindex
from pmb.helpers.exceptions import BuildFailedError

from pmb.core import Chroot
from pmb.core.context import get_context

class BootstrapStage(enum.IntEnum):
    """
    Pass a BOOTSTRAP= environment variable with the given value to abuild. See
    bootstrap_1 etc. at https://postmarketos.org/pmaports.cfg for details.
    """
    NONE = 0
    # We don't need explicit representations of the other numbers.


def check_build_for_arch(pkgname: str, arch: Arch):
    """Check if pmaport can be built or exists as binary for a specific arch.

    :returns: * True when it can be built
              * False when it can't be built, but exists in a binary repo
                (e.g. temp/mesa can't be built for x86_64, but Alpine has it)
    :raises: RuntimeError if the package can't be built for the given arch and
             does not exist as binary package.
    """
    context = get_context()
    # Check for pmaport with arch
    if pmb.helpers.package.check_arch(pkgname, arch, False):
        return True

    # Check for binary package
    binary = pmb.parse.apkindex.package(pkgname, arch, False)
    if binary:
        pmaport = pmb.helpers.pmaports.get(pkgname)
        pmaport_version = pmaport["pkgver"] + "-r" + pmaport["pkgrel"]
        logging.debug(pkgname + ": found pmaport (" + pmaport_version + ") and"
                      " binary package (" + binary["version"] + ", from"
                      " postmarketOS or Alpine), but pmaport can't be built"
                      f" for {arch} -> using binary package")
        return False

    # No binary package exists and can't build it
    logging.info("NOTE: You can edit the 'arch=' line inside the APKBUILD")
    if context.command == "build":
        logging.info("NOTE: Alternatively, use --arch to build for another"
                     " architecture ('pmbootstrap build --arch=armhf " +
                     pkgname + "')")
    raise RuntimeError(f"Can't build '{pkgname}' for architecture {arch}")


def get_depends(context: Context, apkbuild):
    """Alpine's abuild always builds/installs the "depends" and "makedepends" of a package
    before building it.

    We used to only care about "makedepends"
    and it's still possible to ignore the depends with --ignore-depends.

    :returns: list of dependency pkgnames (eg. ["sdl2", "sdl2_net"])
    """
    # Read makedepends and depends
    ret = list(apkbuild["makedepends"])
    if "!check" not in apkbuild["options"]:
        ret += apkbuild["checkdepends"]
    if not context.ignore_depends:
        ret += apkbuild["depends"]
    ret = sorted(set(ret))

    # Don't recurse forever when a package depends on itself (#948)
    for pkgname in ([apkbuild["pkgname"]] +
                    list(apkbuild["subpackages"].keys())):
        if pkgname in ret:
            logging.verbose(apkbuild["pkgname"] + ": ignoring dependency on"
                            " itself: " + pkgname)
            ret.remove(pkgname)

    # FIXME: is this needed? is this sensible?
    ret = list(filter(lambda x: not x.startswith("!"), ret))
    return ret


def get_pkgver(original_pkgver, original_source=False, now=None):
    """Get the original pkgver when using the original source.

    Otherwise, get the pkgver with an appended suffix of current date and time.
    For example: ``_p20180218550502``
    When appending the suffix, an existing suffix (e.g. ``_git20171231``) gets
    replaced.

    :param original_pkgver: unmodified pkgver from the package's APKBUILD.
    :param original_source: the original source is used instead of overriding
                            it with --src.
    :param now: use a specific date instead of current date (for test cases)
    """
    if original_source:
        return original_pkgver

    # Append current date
    no_suffix = original_pkgver.split("_", 1)[0]
    now = now if now else datetime.datetime.now()
    new_suffix = "_p" + now.strftime("%Y%m%d%H%M%S")
    return no_suffix + new_suffix


def override_source(apkbuild, pkgver, src, chroot: Chroot=Chroot.native()):
    """Mount local source inside chroot and append new functions (prepare() etc.)
    to the APKBUILD to make it use the local source.
    """
    if not src:
        return

    # Mount source in chroot
    mount_path = "/mnt/pmbootstrap/source-override/"
    mount_path_outside = chroot / mount_path
    pmb.helpers.mount.bind(src, mount_path_outside, umount=True)

    # Delete existing append file
    append_path = "/tmp/APKBUILD.append"
    append_path_outside = chroot / append_path
    if append_path_outside.exists():
        pmb.chroot.root(["rm", append_path], chroot)

    # Add src path to pkgdesc, cut it off after max length
    pkgdesc = ("[" + src + "] " + apkbuild["pkgdesc"])[:127]

    # Appended content
    append = """
             # ** Overrides below appended by pmbootstrap for --src **

             pkgver=\"""" + pkgver + """\"
             pkgdesc=\"""" + pkgdesc + """\"
             _pmb_src_copy="/tmp/pmbootstrap-local-source-copy"

             # Empty $source avoids patching in prepare()
             _pmb_source_original="$source"
             source=""
             sha512sums=""

             fetch() {
                 # Update source copy
                 msg "Copying source from host system: """ + src + """\"
                 rsync -a --exclude=".git/" --delete --ignore-errors --force \\
                     \"""" + mount_path + """\" "$_pmb_src_copy" || true

                 # Link local source files (e.g. kernel config)
                 mkdir "$srcdir"
                 local s
                 for s in $_pmb_source_original; do
                     is_remote "$s" || ln -sf "$startdir/$s" "$srcdir/"
                 done
             }

             unpack() {
                 ln -sv "$_pmb_src_copy" "$builddir"
             }
             """

    # Write and log append file
    with open(append_path_outside, "w", encoding="utf-8") as handle:
        for line in append.split("\n"):
            handle.write(line[13:].replace(" " * 4, "\t") + "\n")
    pmb.chroot.user(["cat", append_path], chroot)

    # Append it to the APKBUILD
    apkbuild_path = "/home/pmos/build/APKBUILD"
    shell_cmd = ("cat " + apkbuild_path + " " + append_path + " > " +
                 append_path + "_")
    pmb.chroot.user(["sh", "-c", shell_cmd], chroot)
    pmb.chroot.user(["mv", append_path + "_", apkbuild_path], chroot)


def mount_pmaports(chroot: Chroot=Chroot.native()) -> Dict[str, Path]:
    """
    Mount pmaports.git in chroot.

    :param chroot: chroot to target
    :returns: Dictionary mapping pkgrepo name to dest path
    """
    dest_paths = {}
    for repo in pkgrepo_paths(skip_extras=True):
        destination = Path("/mnt") / repo.name
        outside_destination = chroot / destination
        pmb.helpers.mount.bind(repo, outside_destination, umount=True)
        dest_paths[repo.name] = destination
        
    return dest_paths


def link_to_git_dir(chroot: Chroot):
    """ Make ``/home/pmos/build/.git`` point to the .git dir from pmaports.git, with a
    symlink so abuild does not fail (#1841).

    abuild expects the current working directory to be a subdirectory of a
    cloned git repository (e.g. main/openrc from aports.git). If git is
    installed, it will try to get the last git commit from that repository, and
    place it in the resulting apk (.PKGINFO) as well as use the date from that
    commit as SOURCE_DATE_EPOCH (for reproducible builds).

    With that symlink, we actually make it use the last git commit from
    pmaports.git for SOURCE_DATE_EPOCH and have that in the resulting apk's
    .PKGINFO.
    """
    # Mount pmaports.git in chroot, in case the user did not use pmbootstrap to
    # clone it (e.g. how we build on sourcehut). Do this here and not at the
    # initialization of the chroot, because the pmaports dir may not exist yet
    # at that point. Use umount=True, so we don't have an old path mounted
    # (some tests change the pmaports dir).
    dest_paths = mount_pmaports(chroot)

    # Create .git symlink
    pmb.chroot.user(["mkdir", "-p", "/home/pmos/build"], chroot)
    pmb.chroot.user(["ln", "-sf", dest_paths["pmaports"] / ".git",
                           "/home/pmos/build/.git"], chroot)


def output_path(arch: Arch, pkgname: str, pkgver: str, pkgrel: str) -> Path:
    # Yeahp, you can just treat an Arch like a path!
    return arch / f"{pkgname}-{pkgver}-r{pkgrel}.apk"


def run_abuild(context: Context, apkbuild, channel, arch: Arch, strict=False, force=False, cross=None,
               suffix: Chroot=Chroot.native(), src=None, bootstrap_stage=BootstrapStage.NONE):
    """
    Set up all environment variables and construct the abuild command (all
    depending on the cross-compiler method and target architecture), copy
    the aport to the chroot and execute abuild.

    :param cross: None, "native", or "crossdirect"
    :param src: override source used to build the package with a local folder
    :param bootstrap_stage: pass a BOOTSTRAP= env var with the value to abuild
    :returns: (output, cmd, env), output is the destination apk path relative
              to the package folder ("x86_64/hello-1-r2.apk"). cmd and env are
              used by the test case, and they are the full abuild command and
              the environment variables dict generated in this function.
    """
    # Sanity check
    if cross == "native" and "!tracedeps" not in apkbuild["options"]:
        logging.info("WARNING: Option !tracedeps is not set, but we're"
                     " cross-compiling in the native chroot. This will"
                     " probably fail!")
    pkgdir = context.config.work / "packages" / channel
    if not pkgdir.exists():
        pmb.helpers.run.root(["mkdir", "-p", pkgdir])
        pmb.helpers.run.root(["chown", "-R", f"{pmb.config.chroot_uid_user}:{pmb.config.chroot_uid_user}",
                              pkgdir.parent])

    pmb.chroot.rootm([["mkdir", "-p", "/home/pmos/packages"],
                      ["rm", "-f", "/home/pmos/packages/pmos"],
                      ["ln", "-sf", f"/mnt/pmbootstrap/packages/{channel}",
                     "/home/pmos/packages/pmos"]], suffix)

    # Environment variables
    env = {"CARCH": arch,
           "SUDO_APK": "abuild-apk --no-progress"}
    if cross == "native":
        hostspec = arch.alpine_triple()
        env["CROSS_COMPILE"] = hostspec + "-"
        env["CC"] = hostspec + "-gcc"
    if cross == "crossdirect":
        env["PATH"] = ":".join([f"/native/usr/lib/crossdirect/{arch}",
                                pmb.config.chroot_path])
    if not context.ccache:
        env["CCACHE_DISABLE"] = "1"

    # Use sccache without crossdirect (crossdirect uses it via rustc.sh)
    if context.ccache and cross != "crossdirect":
        env["RUSTC_WRAPPER"] = "/usr/bin/sccache"

    # Cache binary objects from go in this path (like ccache)
    env["GOCACHE"] = "/home/pmos/.cache/go-build"

    # Cache go modules (git repositories). Usually these should be bundled and
    # it should not be required to download them at build time, in that case
    # the APKBUILD sets the GOPATH (and therefore indirectly GOMODCACHE). But
    # e.g. when using --src they are not bundled, in that case it makes sense
    # to point GOMODCACHE at pmbootstrap's work dir so the modules are only
    # downloaded once.
    if context.go_mod_cache:
        env["GOMODCACHE"] = "/home/pmos/go/pkg/mod"

    if bootstrap_stage:
        env["BOOTSTRAP"] = str(bootstrap_stage)

    # Build the abuild command
    cmd = ["abuild", "-D", "postmarketOS"]
    if strict or "pmb:strict" in apkbuild["options"]:
        if not strict:
            logging.debug(apkbuild["pkgname"] + ": 'pmb:strict' found in"
                          " options, building in strict mode")
        cmd += ["-r"]  # install depends with abuild
    else:
        cmd += ["-d"]  # do not install depends with abuild
    if force:
        cmd += ["-f"]

    # Copy the aport to the chroot and build it
    pmb.build.copy_to_buildpath(apkbuild["pkgname"], suffix)
    override_source(apkbuild, apkbuild["pkgver"], src, suffix)
    link_to_git_dir(suffix)
    pmb.chroot.user(cmd, suffix, Path("/home/pmos/build"), env=env)


def finish(apkbuild, channel, arch, output: Path, chroot: Chroot, strict=False):
    """Various finishing tasks that need to be done after a build."""
    # Verify output file
    out_dir = (get_context().config.work / "packages" / channel)
    if not (out_dir / output).exists():
        raise RuntimeError(f"Package not found after build: {(out_dir / output)}")

    # Clear APKINDEX cache (we only parse APKINDEX files once per session and
    # cache the result for faster dependency resolving, but after we built a
    # package we need to parse it again)
    pmb.parse.apkindex.clear_cache(out_dir / arch / "APKINDEX.tar.gz")

    # Uninstall build dependencies (strict mode)
    if strict or "pmb:strict" in apkbuild["options"]:
        logging.info(f"({chroot}) uninstall build dependencies")
        pmb.chroot.user(["abuild", "undeps"], chroot, Path("/home/pmos/build"),
                        env={"SUDO_APK": "abuild-apk --no-progress"})
        # If the build depends contain postmarketos-keys or postmarketos-base,
        # abuild will have removed the postmarketOS repository key (pma#1230)
        pmb.chroot.init_keys()

_package_cache: Dict[str, List[str]] = {}

def is_cached_or_cache(arch: Arch, pkgname: str) -> bool:
    """Check if a package is in the built packages cache, if not
    then mark it as built. We must mark as built before building
    to break cyclical dependency loops."""
    global _package_cache
    if arch not in _package_cache:
        _package_cache[str(arch)] = []

    ret = pkgname in _package_cache[str(arch)]
    if not ret:
        _package_cache[str(arch)].append(pkgname)
    else:
        logging.debug(f"{arch}/{pkgname}: already built")
    return ret


def get_apkbuild(pkgname):
    """Parse the APKBUILD path for pkgname.

    When there is none, try to find it in the binary package APKINDEX files or raise an exception.

    :param pkgname: package name to be built, as specified in the APKBUILD
    :returns: None or parsed APKBUILD
    """

    # Get pmaport, skip upstream only packages
    pmaport, apkbuild = pmb.helpers.pmaports.get_with_path(pkgname, False)
    if pmaport:
        pmaport = pkgrepo_relative_path(pmaport)[0]
        return pmaport, apkbuild

    return None, None


class BuildQueueItem(TypedDict):
        name: str
        arch: Arch # Arch to build for
        aports: str
        apkbuild: Dict[str, Any]
        output_path: Path
        channel: str
        depends: List[str]
        cross: str
        chroot: Chroot


# arch is set if we should build for a specific arch
def process_package(context: Context, queue_build: Callable, pkgname: str,
                    arch: Optional[Arch], fallback_arch: Arch, force: bool) -> List[str]:
    # Only build when APKBUILD exists
    base_aports, base_apkbuild = get_apkbuild(pkgname)
    if not base_apkbuild:
        if pmb.parse.apkindex.providers(pkgname, fallback_arch, False):
            return []
        raise RuntimeError(f"{pkgname}: Could not find aport, and"
                        " could not find this package in any APKINDEX!")

    if arch is None:
        arch = pmb.build.autodetect.arch(base_apkbuild)

    if is_cached_or_cache(arch, pkgname) and not force:
        logging.verbose(f"Skipping build for {arch}/{pkgname}, already built")
        return []

    logging.debug(f"{arch}/{pkgname}: Generating dependency tree")
    # Add the package to the build queue
    depends = get_depends(context, base_apkbuild)

    will_build_base = False
    if (pmb.build.is_necessary(arch, base_apkbuild) or force) and check_build_for_arch(pkgname, arch):
        will_build_base = True

    parent = pkgname
    while len(depends):
        dep = depends.pop(0)
        if is_cached_or_cache(arch, pmb.helpers.package.remove_operators(dep)):
            continue
        cross = None

        aports, apkbuild = get_apkbuild(dep)
        if not apkbuild:
            continue

        if context.no_depends:
            pmb.helpers.repo.update(arch)
            cross = pmb.build.autodetect.crosscompile(apkbuild, arch)
            _dep_arch = Arch.native() if cross == "native" else arch
            if not pmb.parse.apkindex.package(dep, _dep_arch, False):
                raise RuntimeError("Missing binary package for dependency '" +
                                dep + "' of '" + parent + "', but"
                                " pmbootstrap won't build any depends since"
                                " it was started with --no-depends.")

        if pmb.build.is_necessary(arch, apkbuild):
            if context.no_depends:
                raise RuntimeError(f"Binary package for dependency '{dep}'"
                                   f" of '{parent}' is outdated, but"
                                   f" pmbootstrap won't build any depends"
                                   f" since it was started with --no-depends.")

            deps = get_depends(context, apkbuild)
            if will_build_base:
                queue_build(aports, apkbuild, deps, cross)
            else:
                logging.info(f"@YELLOW@SKIP:@END@ {arch}/{dep}: is a dependency of"
                             f" {pkgname} which isn't marked for build. Call with"
                             f" --force or consider building {dep} manually")

            logging.verbose(f"{arch}/{dep}: Inserting {len(deps)} dependencies")
            depends = deps + depends
            parent = dep

    # Queue the package itself after it's dependencies
    if will_build_base:
        queue_build(base_aports, base_apkbuild, depends)

    return depends


def packages(context: Context, pkgnames: List[str], arch: Optional[Arch]=None, force=False, strict=False,
            src=None, bootstrap_stage=BootstrapStage.NONE, log_callback: Optional[Callable]=None) -> List[str]:
    """
    Build a package and its dependencies with Alpine Linux' abuild.

    :param pkgname: package name to be built, as specified in the APKBUILD
    :param arch: architecture we're building for (default: native)
    :param force: always build, even if not necessary
    :param strict: avoid building with irrelevant dependencies installed by
                   letting abuild install and uninstall all dependencies.
    :param src: override source used to build the package with a local folder
    :param bootstrap_stage: pass a BOOTSTRAP= env var with the value to abuild
    :param log_callback: function to call before building each package instead of
                         logging. It should accept a single BuildQueueItem parameter.
    :returns: None if the build was not necessary
              output path relative to the packages folder ("armhf/ab-1-r2.apk")
    """
    global _package_cache

    build_queue: List[BuildQueueItem] = []
    built_packages: Set[str] = set()

    # Add a package to the build queue, fetch it's dependency, and
    # add record build helpers to installed (e.g. sccache)
    def queue_build(aports: Path, apkbuild: Dict[str, Any], depends: List[str], cross: Optional[str] = None) -> List[str]:
        # Skip if already queued
        name = apkbuild["pkgname"]
        if any(item["name"] == name for item in build_queue):
            return []

        pkg_arch = pmb.build.autodetect.arch(apkbuild) if arch is None else arch
        chroot = pmb.build.autodetect.chroot(apkbuild, pkg_arch)
        cross = cross or pmb.build.autodetect.crosscompile(apkbuild, pkg_arch)
        build_queue.append({
            "name": name,
            "arch": pkg_arch,
            "aports": aports.name, # the pmaports source repo (e.g. "systemd")
            "apkbuild": apkbuild,
            "output_path": output_path(pkg_arch, apkbuild["pkgname"],
                                       apkbuild["pkgver"], apkbuild["pkgrel"]),
            "channel":  pmb.config.pmaports.read_config(aports)["channel"],
            "depends": depends,
            "chroot": chroot,
            "cross": cross
        })

        # If we just queued a package that was request to be built explicitly then
        # record it, since we return which packages we actually built
        if apkbuild["pkgname"] in pkgnames:
            built_packages.add(apkbuild["pkgname"])

        return depends

    if src and len(pkgnames) > 1:
        raise RuntimeError("Can't build multiple packages with --src")

    logging.verbose(f"Attempting to build: {', '.join(pkgnames)}")

    # We sorta-kind maybe supported building packages for multiple architectures in
    # a single called to packages(). We need to do a check to make sure that the user
    # didn't specify a package that doesn't exist, and we can't just check the source repo
    # since we might get called with some perhaps bogus packages that do exist in the binary
    # repo but not in the source one, but we need to error if we get a package that doesn't
    # exist anywhere, as something is clearly wrong for that to happen.
    # The problem is the APKINDEX parsing code doesn't have a way to check all architectures
    # so we need this hack.
    fallback_arch = arch if arch is not None else pmb.build.autodetect.arch(pkgnames[0])
    # Get existing binary package indexes
    pmb.helpers.repo.update(fallback_arch)

    # Process the packages we've been asked to build, queuing up any
    # dependencies that need building as well as the package itself
    all_dependencies: List[str] = []
    for pkgname in pkgnames:
        all_dependencies += process_package(context, queue_build, pkgname, arch, fallback_arch, force)

    if not len(build_queue):
        return []

    qlen = len(build_queue)
    logging.info(f"@BLUE@{qlen}@END@ package{'s' if qlen > 1 else ''} to build")
    for item in build_queue:
        logging.debug(f"@BLUE@*@END@ {item['channel']}/{item['name']}")

    cross = None

    while len(build_queue):
        pkg = build_queue.pop(0)
        chroot = pkg["chroot"]
        pkg_arch = pkg["arch"]

        channel = pkg["channel"]
        output = pkg["output_path"]
        if not log_callback:
            logging.info(f"*** Building {channel}/{output} ***")
        else:
            log_callback(pkg)

        # One time chroot initialization
        if pmb.build.init(chroot):
            pmb.build.other.configure_abuild(chroot)
            pmb.build.other.configure_ccache(chroot)
            if "rust" in all_dependencies or "cargo" in all_dependencies:
                pmb.chroot.apk.install(["sccache"], chroot)
            if src:
                pmb.chroot.apk.install(["rsync"], chroot)

        # We only need to init cross compiler stuff once
        if not cross:
            cross = pmb.build.autodetect.crosscompile(pkg["apkbuild"], pkg_arch)
            if cross:
                pmb.build.init_compiler(context, all_dependencies, cross, pkg_arch)
            if cross == "crossdirect":
                pmb.chroot.mount_native_into_foreign(chroot)

        if not strict and "pmb:strict" not in pkg["apkbuild"]["options"] and len(pkg["depends"]):
            pmb.chroot.apk.install(pkg["depends"], chroot)

        # Build and finish up
        try:
            run_abuild(context, pkg["apkbuild"], channel, pkg_arch, strict, force, cross,
                                            chroot, src, bootstrap_stage)
        except RuntimeError:
            raise BuildFailedError(f"Couldn't build {output}!")
        finish(pkg["apkbuild"], channel, pkg_arch, output, chroot, strict)

    # Clear package cache for the next run
    _package_cache = {}

    return list(built_packages)
