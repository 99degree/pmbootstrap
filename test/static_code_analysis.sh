#!/bin/sh
# Copyright 2021 Oliver Smith
# SPDX-License-Identifier: AGPL-3.0-or-later
set -e
DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$DIR/.."

# Make sure that the work folder format is up to date, and that there are no
# mounts from aborted test cases (#1595)
./pmbootstrap.py work_migrate
./pmbootstrap.py -q shutdown

# Install needed packages
echo "Initializing Alpine chroot (details: 'pmbootstrap log')"
./pmbootstrap.py -q chroot -- apk -q add \
	shellcheck \
	python3 \
	py3-flake8 || return 1

rootfs_native="$(./pmbootstrap.py config work)/chroot_native"
command="$rootfs_native/lib/ld-musl-$(uname -m).so.1"
command="$command --library-path=$rootfs_native/lib:$rootfs_native/usr/lib"
shellcheck_command="$command $rootfs_native/usr/bin/shellcheck"
flake8_command="$command $rootfs_native/usr/bin/python3 $rootfs_native/usr/bin/flake8"

# Shell: shellcheck
find . -name '*.sh' |
while read -r file; do
	echo "Test with shellcheck: $file"
	cd "$DIR/../$(dirname "$file")"
	$shellcheck_command -e SC1008 -x "$(basename "$file")"
done

# Python: flake8
# E501: max line length
# F401: imported, but not used, does not make sense in __init__ files
# E402: module import not on top of file, not possible for testcases
# E722: do not use bare except
cd "$DIR"/..
echo "Test with flake8: *.py"
# Note: omitting a virtualenv if it is here (e.g. gitlab CI)
py_files="$(find . -not -path '*/venv/*' -name '*.py')"
_ignores="E501,E402,E722,W504,W605"
# shellcheck disable=SC2086
$flake8_command --exclude=__init__.py --ignore "$_ignores" $py_files
# shellcheck disable=SC2086
$flake8_command --filename=__init__.py --ignore "F401,$_ignores" $py_files

# Enforce max line length of 79 characters (#1986). We are iteratively fixing
# up the source files to adhere to this rule, so ignore the ones that are not
# yet fixed. Eventually, E501 can be removed from _ignores above, and this
# whole block can be removed.
echo "Test with flake8: *.py (E501)"
exc_files="./pmb/aportgen/busybox_static.py"
exc_files="$exc_files,./pmb/aportgen/grub_efi.py"
exc_files="$exc_files,./pmb/aportgen/linux.py"
exc_files="$exc_files,./pmb/aportgen/musl.py"
# shellcheck disable=SC2086
$flake8_command --select="E501" --exclude=$exc_files $py_files

# Done
echo "Success!"
