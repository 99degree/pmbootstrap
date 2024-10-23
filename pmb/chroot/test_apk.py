from pathlib import Path

import pytest

from pmb.core.arch import Arch
from pmb.core.context import get_context
from pmb.parse.apkindex import ApkindexBlock

from .apk import packages_get_locally_built_apks
import pmb.config.pmaports


@pytest.fixture
def apk_mocks(monkeypatch) -> dict | None:
    def _pmaports_config(_aports=None):
        return {
            "channel": "edge",
        }

    monkeypatch.setattr(pmb.config.pmaports, "read_config", _pmaports_config)

    def _apkindex_package(
        _package: str, _arch: Arch, _must_exist: bool = False, indexes=None
    ) -> ApkindexBlock | None:
        if _package == "package1":
            return ApkindexBlock(
                arch=_arch,
                depends=["package2"],
                origin=None,
                pkgname=_package,
                provides=[],
                provider_priority=None,
                timestamp=None,
                version="5.5-r0",
            )
        if _package == "package2":
            return ApkindexBlock(
                arch=_arch,
                depends=[],
                origin=None,
                pkgname=_package,
                provides=[],
                provider_priority=None,
                timestamp=None,
                version="5.5-r0",
            )
        if _package == "package3":
            return ApkindexBlock(
                arch=_arch,
                depends=["package1", "package4"],
                origin=None,
                pkgname=_package,
                provides=[],
                provider_priority=None,
                timestamp=None,
                version="5.5-r0",
            )
        # Test recursive dependency
        if _package == "package4":
            return ApkindexBlock(
                arch=_arch,
                depends=["package3"],
                origin=None,
                pkgname=_package,
                provides=[],
                provider_priority=None,
                timestamp=None,
                version="5.5-r0",
            )

        return None

    monkeypatch.setattr(pmb.parse.apkindex, "package", _apkindex_package)
    return None


def create_apk(pkgname: str, arch: Arch) -> Path:
    apk_file = get_context().config.work / "packages" / "edge" / arch / f"{pkgname}-5.5-r0.apk"
    apk_file.parent.mkdir(parents=True, exist_ok=True)
    apk_file.touch()
    return apk_file


def test_get_local_apks(pmb_args, apk_mocks) -> None:
    """Ensure packages_get_locally_built_apks() returns paths for local apks"""

    pkgname = "package1"
    arch = Arch.x86_64

    apk_file = create_apk(pkgname, arch)

    local = packages_get_locally_built_apks([pkgname, "fake-package"], arch)
    print(local)
    assert len(local) == 1
    assert local[0].parts[-2:] == apk_file.parts[-2:]

    create_apk("package2", arch)
    create_apk("package3", arch)
    create_apk("package4", arch)

    # Test recursive dependencies
    local = packages_get_locally_built_apks(["package3"], arch)
    print(local)
    assert len(local) == 4
