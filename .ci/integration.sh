#!/bin/sh -e

echo "\$@:" "$@"

if [ "$(id -u)" = 0 ]; then
	exec su "${TESTUSER:-build}" -c "sh -ec '$0 $*'"
fi

test="$(basename "$0")"

usage() {
	echo "Usage: $test $1"
	exit 1
}

pmbootstrap() {
	printf "\033[0;32m\$ pmbootstrap %s\033[0m\n" "$*"
	./pmbootstrap.py --details-to-stdout -y "$@"
}

# Make sure that the work folder format is up to date, and that there are no
# mounts from aborted test cases (pmbootstrap#1595)
echo "Initializing pmbootstrap"
yes '' | ./pmbootstrap.py --details-to-stdout init

pmbootstrap work_migrate
pmbootstrap -q shutdown

# Default for tests where the device doesn't matter
pmbootstrap config device qemu-amd64

# Tests the bootimg analysis functionality.
analyze_bootimg() {
	pmbootstrap bootimg_analyze test/data/htc-primou-boot.img
}

# A test that builds normal and FDE images for the given device/ui
build_images() {
	device="$1"
	ui="$2"
	if [ -z "$ui" ] || [ -z "$device" ]; then
		usage "<device> <ui>"
	fi
	pmbootstrap config device "$device"
	pmbootstrap config ui "$ui"

	# NOTE: --no-image is used because building images makes pmb try to
	# "modprobe loop". This fails in CI.
	echo "Building $ui image for $device"
	pmbootstrap install --zap --password 147147 --no-image

	echo "Building $ui image for $device, with FDE"
	pmbootstrap install --zap --password 147147 --fde --no-image
}

force_build() {
	arch="$1"
	shift
	packages="$*"
	if [ -z "$arch" ] || [ -z "$packages" ]; then
		usage "<arch> <packages...>"
	fi

	echo "Force building $packages for $arch"
	# shellcheck disable=SC2086
	pmbootstrap build --force --arch "$arch" $packages
}

strict_build() {
	arch="$1"
	shift
	packages="$*"
	if [ -z "$arch" ] || [ -z "$packages" ]; then
		usage "<arch> <packages...>"
	fi

	echo "Strict building $packages for $arch"
	# shellcheck disable=SC2086
	pmbootstrap build --strict --arch "$arch" $packages
}

force_strict_build() {
	arch="$1"
	shift
	packages="$*"
	if [ -z "$arch" ] || [ -z "$packages" ]; then
		usage "<arch> <packages...>"
	fi

	echo "Force building $packages for $arch"
	# shellcheck disable=SC2086
	pmbootstrap build --force --strict --arch "$arch" $packages
}

bump_autobuild() {
	device="$1"
	package="$2"
	if [ -z "$device" ] || [ -z "$package" ]; then
		usage "<device> <package>"
	fi

	pmbootstrap config device "$device"

	echo "Bumping pkgrel of $package"
	# shellcheck disable=SC2086
	pmbootstrap pkgrel_bump $package

	echo "Ensuring package is built during install"
	pmbootstrap config ui none
	# shellcheck disable=SC2086
	pmbootstrap install --no-image --password 147147 --add $package
	pkgs="$(find "$(./pmbootstrap.py config work)/packages/" -type f)"
	if ! echo "$pkgs" | grep -q "$package"; then
		echo "Package $package not found in built packages:"
		echo "$pkgs"
		exit 1
	fi
}

# Run the test
echo "Running $test $*"
"$test" "$@"
echo "Test $test passed!"
