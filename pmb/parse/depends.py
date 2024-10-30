# Copyright 2023 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
from pmb.helpers import logging
import pmb.chroot
import pmb.chroot.apk
import pmb.helpers.pmaports
import pmb.parse.apkindex
from pmb.core import Chroot
from pmb.core.context import get_context


def package_provider(
    pkgname: str, pkgnames_install: list[str], suffix: Chroot = Chroot.native()
) -> pmb.core.apkindex_block.ApkindexBlock | None:
    """
    :param pkgnames_install: packages to be installed
    :returns: ApkindexBlock object or None (no provider found)
    """
    # Get all providers
    arch = suffix.arch
    providers = pmb.parse.apkindex.providers(pkgname, arch, False)

    # 0. No provider
    if len(providers) == 0:
        return None

    # 1. Only one provider
    logging.verbose(f"{pkgname}: provided by: {', '.join(providers)}")
    if len(providers) == 1:
        return list(providers.values())[0]

    # 2. Provider with the same package name
    if pkgname in providers:
        logging.verbose(f"{pkgname}: choosing package of the same name as " "provider")
        return providers[pkgname]

    # 3. Pick a package that will be installed anyway
    for provider_pkgname, provider in providers.items():
        if provider_pkgname in pkgnames_install:
            logging.verbose(
                f"{pkgname}: choosing provider '{provider_pkgname}"
                "', because it will be installed anyway"
            )
            return provider

    # 4. Pick a package that is already installed
    installed = pmb.chroot.apk.installed(suffix)
    for provider_pkgname, provider in providers.items():
        if provider_pkgname in installed:
            logging.verbose(
                f"{pkgname}: choosing provider '{provider_pkgname}"
                f"', because it is installed in the '{suffix}' "
                "chroot already"
            )
            return provider

    # 5. Pick an explicitly selected provider
    provider_pkgname = get_context().config.providers.get(pkgname, "")
    if provider_pkgname in providers:
        logging.verbose(
            f"{pkgname}: choosing provider '{provider_pkgname}', "
            "because it was explicitly selected."
        )
        return providers[provider_pkgname]

    # 6. Pick the provider(s) with the highest priority
    providers = pmb.parse.apkindex.provider_highest_priority(providers, pkgname)
    if len(providers) == 1:
        return list(providers.values())[0]

    # 7. Pick the shortest provider. (Note: Normally apk would fail here!)
    return pmb.parse.apkindex.provider_shortest(providers, pkgname)
