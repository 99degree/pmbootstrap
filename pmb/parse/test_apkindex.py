from pathlib import Path
from pmb.core.arch import Arch
import pytest

import pmb.parse.apkindex

from .apkindex import parse as parse_apkindex, clear_cache as clear_apkindex_cache

example_apkindex = """
C:Q1p+nGf5oBAmbU9FQvV4MhfEmWqVE=
P:postmarketos-base-ui-networkmanager
V:29-r1
A:aarch64
S:4185
I:5376
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:busctl dnsmasq-dnssec-dbus>=2.90-r1 gojq networkmanager networkmanager-cli networkmanager-tui networkmanager-wifi networkmanager-wwan networkmanager-dnsmasq

C:Q1j8i5C7tOHbhrd5OTtot1u1ZtteU=
P:postmarketos-base-ui-networkmanager-usb-tethering
V:29-r1
A:aarch64
S:3986
I:5206
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:chrony dbus haveged nftables openssh-server-pam pipewire postmarketos-artwork-icons postmarketos-base shadow tzdata util-linux wireless-regdb wireplumber
i:postmarketos-base-ui-networkmanager=29-r1

C:Q10FDERw8SRwuk6ITfWNyJ4GbVE2I=
P:postmarketos-base-ui-openrc
V:29-r1
A:aarch64
S:1570
I:1
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:chrony-openrc dbus-openrc haveged-openrc util-linux-openrc /bin/sh
i:postmarketos-base-ui=29-r1 openrc

C:Q1inMHA1+gsS8U50j8odasUNy5TVw=
P:postmarketos-base-ui-openrc-settingsd
V:29-r1
A:aarch64
S:1824
I:103
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:chrony dbus haveged nftables openssh-server-pam pipewire postmarketos-artwork-icons postmarketos-base shadow tzdata util-linux wireless-regdb wireplumber /bin/sh
i:postmarketos-base-ui=29-r1 openrc-settingsd-openrc

C:Q1iB2PiTO0w29T4kRUSjqLOyKcCwY=
P:postmarketos-base-ui-qt-tweaks
V:29-r1
A:aarch64
S:1661
I:47
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:chrony dbus haveged nftables openssh-server-pam pipewire postmarketos-artwork-icons postmarketos-base shadow tzdata util-linux wireless-regdb wireplumber

C:Q1aQ5UEOC902h2atx0fqDhahpkDY8=
P:postmarketos-base-ui-qt-wayland
V:29-r1
A:aarch64
S:1687
I:86
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:chrony dbus haveged nftables openssh-server-pam pipewire postmarketos-artwork-icons postmarketos-base shadow tzdata util-linux wireless-regdb wireplumber

C:Q16q7k1Z5HAJe5qqeXfTIn62QQoBs=
P:postmarketos-base-ui-tinydm
V:29-r1
A:aarch64
S:2264
I:571
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:chrony dbus haveged nftables openssh-server-pam pipewire postmarketos-artwork-icons postmarketos-base shadow tzdata util-linux wireless-regdb wireplumber
p:postmarketos-base-tinydm=29-r1
i:postmarketos-base-ui=29-r1 tinydm-openrc

C:Q11IrmIOlqZX5BzKhLsce+U+S+N3I=
P:postmarketos-base-ui-wayland
V:29-r1
A:aarch64
S:1361
I:0
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:chrony dbus haveged nftables openssh-server-pam pipewire postmarketos-artwork-icons postmarketos-base shadow tzdata util-linux wireless-regdb wireplumber
i:postmarketos-base-ui=29-r1 wayland-server-libs

C:Q1bDAQG9F9mi7Mi5CT3Csiq4QU52w=
P:postmarketos-base-ui-wifi-iwd
V:29-r1
A:aarch64
S:1650
I:26
T:Use iwd as the WiFi backend (but may not work with all devices)
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
k:90
D:iwd
p:postmarketos-base-ui-wifi=29-r1

C:Q1s7shLQ3sKNxKABRyQRBQ11RwkuU=
P:postmarketos-base-ui-wifi-iwd-openrc
V:29-r1
A:aarch64
S:1405
I:1
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:iwd-openrc openrc /bin/sh
i:postmarketos-base-ui-wifi-iwd=29-r1 openrc

C:Q1C23f0J5PBrNAuZiAhzCJhVpNe5c=
P:postmarketos-base-ui-wifi-wpa_supplicant
V:29-r1
A:aarch64
S:1262
I:0
T:Use wpa_supplicant as the WiFi backend.
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
k:100
D:wpa_supplicant
p:postmarketos-base-ui-wifi=29-r1

C:Q1LfWeOxxgIM3UE/QQBczMpJw17hY=
P:postmarketos-base-ui-wifi-wpa_supplicant-openrc
V:29-r1
A:aarch64
S:1691
I:37
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:wpa_supplicant-openrc openrc /bin/sh
i:postmarketos-base-ui-wifi-wpa_supplicant=29-r1 openrc

C:Q1yB3CVUFMOjnLOOEAUIUUpJJV8g0=
P:postmarketos-base-ui-x11
V:29-r1
A:aarch64
S:1587
I:22
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:libinput xf86-input-libinput xf86-video-fbdev
p:postmarketos-base-x11=29-r1
i:postmarketos-base-ui=29-r1 xorg-server

C:Q1TgE1jgGt1PUb/Zwbi6rDRt3b8go=
P:postmarketos-initramfs
V:3.3.5-r2
A:aarch64
S:16530
I:143360
T:Base files for the postmarketOS initramfs / initramfs-extra
U:https://postmarketos.org
L:GPL-2.0-or-later
o:postmarketos-initramfs
m:Caleb Connolly <caleb@postmarketos.org>
t:1728049308
c:f3a4285732781d5577bad06046b7bbeaf132ce1f-dirty
k:10
D:blkid btrfs-progs buffyboard busybox-extras bzip2 cryptsetup device-mapper devicepkg-utils>=0.2.0 dosfstools e2fsprogs e2fsprogs-extra f2fs-tools font-terminus iskey kmod libinput-libs lz4 multipath-tools parted postmarketos-fde-unlocker postmarketos-mkinitfs>=2.2 udev unudhcpd util-linux-misc xz
p:postmarketos-ramdisk=3.3.5-r2"""


@pytest.fixture
def valid_apkindex_file(tmp_path) -> Path:
    # FIXME: use tmpfile fixture from !2453
    tmpfile = tmp_path / "APKINDEX.1"
    print(tmp_path)
    f = open(tmpfile, "w")
    f.write(example_apkindex)
    f.close()

    return tmpfile


def test_apkindex_parse(valid_apkindex_file) -> None:
    tmpfile = valid_apkindex_file
    blocks = parse_apkindex(tmpfile, True)
    for k, v in blocks.items():
        print(f"{k}: {v}")

    # Even though there's only 14 entries, there are some virtual packages
    assert len(blocks) == 18

    # Check that the postmarketos-ramdisk virtual package is handled correctly
    # and that it's one provider (postmarketos-initramfs) is declared
    assert "postmarketos-ramdisk" in blocks.keys()
    assert "postmarketos-initramfs" in blocks["postmarketos-ramdisk"].keys()
    assert (
        blocks["postmarketos-ramdisk"]["postmarketos-initramfs"]
        == blocks["postmarketos-initramfs"]["postmarketos-initramfs"]
    )

    initramfs = blocks["postmarketos-initramfs"]["postmarketos-initramfs"]
    assert initramfs.pkgname == "postmarketos-initramfs"
    assert initramfs.provides == ["postmarketos-ramdisk"]
    assert initramfs.provider_priority == 10
    assert initramfs.depends == [
        "blkid",
        "btrfs-progs",
        "buffyboard",
        "busybox-extras",
        "bzip2",
        "cryptsetup",
        "device-mapper",
        "devicepkg-utils",
        "dosfstools",
        "e2fsprogs",
        "e2fsprogs-extra",
        "f2fs-tools",
        "font-terminus",
        "iskey",
        "kmod",
        "libinput-libs",
        "lz4",
        "multipath-tools",
        "parted",
        "postmarketos-fde-unlocker",
        "postmarketos-mkinitfs",
        "udev",
        "unudhcpd",
        "util-linux-misc",
        "xz",
    ]

    tinydm = blocks["postmarketos-base-ui-tinydm"]["postmarketos-base-ui-tinydm"]
    # Without the version!
    assert tinydm.provides == ["postmarketos-base-tinydm"]
    assert tinydm.version == "29-r1"
    assert tinydm.arch == Arch.aarch64

    wayland = blocks["postmarketos-base-ui-wayland"]["postmarketos-base-ui-wayland"]
    # Doesn't provide an explicit version
    assert wayland.provides == []
    assert wayland.origin == "postmarketos-base-ui"

    networkmanager = blocks["postmarketos-base-ui-networkmanager"][
        "postmarketos-base-ui-networkmanager"
    ]
    assert networkmanager.provider_priority is None


def test_apkindex_parse_bad_priority(tmp_path) -> None:
    tmpfile = tmp_path / "APKINDEX.2"
    f = open(tmpfile, "w")
    # A snippet of the above example but with the provider_priority
    # of postmarketos-initramfs set to a non-integer value
    f.write(
        """
C:Q1yB3CVUFMOjnLOOEAUIUUpJJV8g0=
P:postmarketos-base-ui-x11
V:29-r1
A:aarch64
S:1587
I:22
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
o:postmarketos-base-ui
m:Clayton Craft <clayton@craftyguy.net>
t:1729538699
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:libinput xf86-input-libinput xf86-video-fbdev
p:postmarketos-base-x11=29-r1
i:postmarketos-base-ui=29-r1 xorg-server

C:Q1TgE1jgGt1PUb/Zwbi6rDRt3b8go=
P:postmarketos-initramfs
V:3.3.5-r2
A:aarch64
S:16530
I:143360
T:Base files for the postmarketOS initramfs / initramfs-extra
U:https://postmarketos.org
L:GPL-2.0-or-later
o:postmarketos-initramfs
m:Caleb Connolly <caleb@postmarketos.org>
t:1728049308
c:f3a4285732781d5577bad06046b7bbeaf132ce1f-dirty
k:beep
D:blkid btrfs-progs buffyboard busybox-extras bzip2 cryptsetup device-mapper devicepkg-utils>=0.2.0 dosfstools e2fsprogs e2fsprogs-extra f2fs-tools font-terminus iskey kmod libinput-libs lz4 multipath-tools parted postmarketos-fde-unlocker postmarketos-mkinitfs>=2.2 udev unudhcpd util-linux-misc xz
p:postmarketos-ramdisk=3.3.5-r2"""
    )
    f.close()

    # We expect a RuntimeError to be raised when provider_priority is not
    # an integer
    with pytest.raises(RuntimeError):
        parse_apkindex(tmpfile, True)


def test_apkindex_parse_missing_optionals(tmp_path) -> None:
    tmpfile = tmp_path / "APKINDEX.3"
    f = open(tmpfile, "w")
    # A snippet of the above example but with a missing timestamp
    # and origin fields
    f.write(
        """
C:Q1yB3CVUFMOjnLOOEAUIUUpJJV8g0=
P:postmarketos-base-ui-x11
V:29-r1
A:aarch64
S:1587
I:22
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
m:Clayton Craft <clayton@craftyguy.net>
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:libinput xf86-input-libinput xf86-video-fbdev
p:postmarketos-base-x11=29-r1
i:postmarketos-base-ui=29-r1 xorg-server"""
    )
    f.close()

    # We expect parsing to succeed when the timestamp is missing
    parse_apkindex(tmpfile, True)


def test_apkindex_parse_trailing_newline(tmp_path) -> None:
    tmpfile = tmp_path / "APKINDEX.4"
    f = open(tmpfile, "w")
    # A snippet of the above example but with additional
    # trailing newlines
    f.write(
        """
C:Q1yB3CVUFMOjnLOOEAUIUUpJJV8g0=
P:postmarketos-base-ui-x11
V:29-r1
A:aarch64
S:1587
I:22
T:Meta package for minimal postmarketOS UI base
U:https://postmarketos.org
L:GPL-3.0-or-later
m:Clayton Craft <clayton@craftyguy.net>
c:901cb9520450a1e88ded95ac774e83f6b2cfbba3-dirty
D:libinput xf86-input-libinput xf86-video-fbdev
p:postmarketos-base-x11=29-r1
i:postmarketos-base-ui=29-r1 xorg-server


"""
    )
    f.close()

    # We expect parsing to succeed when the timestamp is missing
    parse_apkindex(tmpfile, True)


def test_apkindex_parse_cache_hit(valid_apkindex_file, monkeypatch) -> None:
    # First parse normally, filling the cache
    parse_apkindex(valid_apkindex_file)

    # Mock that always asserts when called
    def mock_parse_next_block(path, lines):
        assert False

    # parse_next_block() is only called on cache miss
    monkeypatch.setattr(pmb.parse.apkindex, "parse_next_block", mock_parse_next_block)

    # Now we expect the cache to be hit and thus the mock won't be called, so no assertion error
    parse_apkindex(valid_apkindex_file)

    # Now we clear the cache, the mock should be called and we'll assert
    clear_apkindex_cache(valid_apkindex_file)

    with pytest.raises(AssertionError):
        parse_apkindex(valid_apkindex_file)
