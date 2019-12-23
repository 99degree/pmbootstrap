#!/bin/sh
# Copyright 2019 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
#
# usage example:
# $ cd ~/code/linux
# $ source ~/code/pmbootstrap/helpers/envkernel.sh

check_kernel_folder() {
	[ -e "Kbuild" ] && return
	echo "ERROR: This folder is not a linux source tree: $PWD"
	return 1
}


clean_kernel_src_dir() {

	if [ -f ".config" ] || [ -d "include/config" ]; then
		echo "Source directory is not clean, running 'make mrproper'."

		tmp_dir=""
		if [ -d ".output" ]; then
			echo " * Preserving existing build output."
			tmp_dir=$(mktemp -d)
			sudo mv ".output" "$tmp_dir"
		fi;

		# backslash is prefixed to disable the alias
		# shellcheck disable=SC1001
		\make mrproper

		if [ ! -z "$tmp_dir" ]; then
			sudo mv "$tmp_dir/.output" ".output"
			sudo rmdir "$tmp_dir"
		fi;
	fi;
}


export_pmbootstrap_dir() {
	# Get pmbootstrap dir based on this script's location
	# See also: <https://stackoverflow.com/a/29835459>
	# shellcheck disable=SC2039
	if [ -n "${BASH_SOURCE[0]}" ]; then
		script_dir="$(dirname "${BASH_SOURCE[0]}")"
	else
		script_dir="$(dirname "$1")"
	fi

	# Fail with debug information
	# shellcheck disable=SC2155
	export pmbootstrap_dir=$(realpath "$script_dir/..")
	if ! [ -e "$pmbootstrap_dir/pmbootstrap.py" ]; then
		echo "ERROR: Failed to get the script's location with your shell."
		echo "Please adjust export_pmbootstrap_dir in envkernel.sh. Debug info:"
		echo "\$1: $1"
		echo "\$pmbootstrap_dir: $pmbootstrap_dir"
		return 1
	fi
}


set_alias_pmbootstrap() {
	pmbootstrap="$pmbootstrap_dir"/pmbootstrap.py
	# shellcheck disable=SC2139
	alias pmbootstrap="\"$pmbootstrap\""
	if [ -e ~/.config/pmbootstrap.cfg ]; then
		"$pmbootstrap" work_migrate
	else
		echo "NOTE: First run of pmbootstrap, running 'pmbootstrap init'"
		"$pmbootstrap" init
	fi
}


export_chroot_device_deviceinfo() {
	chroot="$("$pmbootstrap" config work)/chroot_native"
	device="$("$pmbootstrap" config device)"
	deviceinfo="$pmbootstrap_dir/aports/device/device-$device/deviceinfo"
	export chroot device deviceinfo
}


check_device() {
	[ -e "$deviceinfo" ] && return
	echo "ERROR: Please select a valid device in 'pmbootstrap init'"
	return 1
}


initialize_chroot() {
	gcc_pkgname="gcc"
	if [ "$gcc6_arg" = "1" ]; then
		gcc_pkgname="gcc6"
	fi
	if [ "$gcc4_arg" = "1" ]; then
		gcc_pkgname="gcc4"
	fi

	# Don't initialize twice
	flag="$chroot/tmp/envkernel/${gcc_pkgname}_setup_done"
	[ -e "$flag" ] && return

	# Install needed packages
	echo "Initializing Alpine chroot (details: 'pmbootstrap log')"

	# shellcheck disable=SC2154
	"$pmbootstrap" -q chroot -- apk -q add \
		abuild \
		bc \
		binutils-"$deviceinfo_arch" \
		binutils \
		bison \
		findutils \
		flex \
		"$gcc_pkgname"-"$deviceinfo_arch" \
		"$gcc_pkgname" \
		linux-headers \
		openssl-dev \
		make \
		musl-dev \
		ncurses-dev \
		perl \
		xz || return 1

	# Create /mnt/linux
	sudo mkdir -p "$chroot/mnt/linux"

	# Mark as initialized
	mkdir -p "$chroot/tmp/envkernel"
	touch "$flag"
}


mount_kernel_source() {
	if [ -e "$chroot/mnt/linux/Kbuild" ]; then
		sudo umount "$chroot/mnt/linux"
	fi
	sudo mount --bind "$PWD" "$chroot/mnt/linux"
}


create_output_folder() {
	[ -d "$chroot/mnt/linux/.output" ] && return
	mkdir -p ".output"
	"$pmbootstrap" -q chroot -- chown -R pmos:pmos "/mnt/linux/.output"
}


set_alias_make() {
	# Cross compiler prefix
	# shellcheck disable=SC1090
	prefix="$(. "$chroot/usr/share/abuild/functions.sh";
		arch_to_hostspec "$deviceinfo_arch")"

	# Kernel architecture
	case "$deviceinfo_arch" in
		aarch64*) arch="arm64" ;;
		arm*) arch="arm" ;;
		x86_64) arch="x86_64" ;;
		x86) arch="x86" ;;
	esac

	# Check if it's a cross compile
	host_arch="$(uname -m)"
	is_cc=1
	# Match arm* architectures
	# shellcheck disable=SC2039
	arch_substr="${host_arch:0:3}"
	if [ "$arch" = "$host_arch" ] || \
		([ "$arch_substr" = "arm" ] && [ "$arch_substr" = "$arch" ]); then
		is_cc=0
	fi

	if [ "$gcc6_arg" = "1" ]; then
		cc="gcc6-${prefix}-gcc"
		hostcc="gcc6-gcc"
		cross_compiler="/usr/bin/gcc6-$prefix-"
	elif [ "$gcc4_arg" = "1" ]; then
		cc="gcc4-${prefix}-gcc"
		hostcc="gcc4-gcc"
		cross_compiler="/usr/bin/gcc4-$prefix-"
	else
		cc="${prefix}-gcc"
		hostcc="gcc"
		cross_compiler="/usr/bin/$prefix-"
	fi

	# Build make command
	cmd="echo '*** pmbootstrap envkernel.sh active for $PWD! ***';"
	cmd="$cmd pmbootstrap -q chroot --user --"
	cmd="$cmd ARCH=$arch"
	if [ "$is_cc" = 1 ]; then
		cmd="$cmd CROSS_COMPILE=$cross_compiler"
	fi
	cmd="$cmd make -C /mnt/linux O=/mnt/linux/.output"
	cmd="$cmd CC=$cc HOSTCC=$hostcc"

	# Avoid "+" suffix in kernel version if the working directory is dirty.
	# (Otherwise we will generate a package that uses different paths...)
	# Note: Set CONFIG_LOCALVERSION_AUTO=n in kernel config additionally
	cmd="$cmd LOCALVERSION="

	# shellcheck disable=SC2139
	alias make="$cmd"
	unset cmd

	# Build run-script command
	cmd="_run_script() {"
	cmd="$cmd echo '*** pmbootstrap envkernel.sh active for $PWD! ***';"
	cmd="$cmd _script=\"\$1\";"
	cmd="$cmd if [ -e \"\$_script\" ]; then"
	cmd="$cmd 	echo \"Running \$_script in the chroot native /mnt/linux/\";"
	cmd="$cmd 	pmbootstrap -q chroot --user -- sh -c \"cd /mnt/linux;"
	cmd="$cmd 	srcdir=/mnt/linux/ builddir=/mnt/linux/.output"
	cmd="$cmd 	./\"\$_script\"\";"
	cmd="$cmd else"
	cmd="$cmd 	echo \"Error: \$_script not found.\";"
	cmd="$cmd fi;"
	cmd="$cmd };"
	cmd="$cmd _run_script \"\$@\""
	# shellcheck disable=SC2139
	alias run-script="$cmd"
	unset cmd
}


set_alias_pmbroot_kernelroot() {
	# shellcheck disable=SC2139
	alias pmbroot="cd '$pmbootstrap_dir'"
	# shellcheck disable=SC2139
	alias kernelroot="cd '$PWD'"
}


cross_compiler_version() {
	pmbootstrap chroot --user -- "${cross_compiler}gcc"  --version \
		2> /dev/null | grep "^.*gcc " | \
		awk -F'[()]' '{ print $1 "("$2")" }'
}


print_usage() {
	# shellcheck disable=SC2039
	if [ -n "${BASH_SOURCE[0]}" ]; then
		echo "usage: source $(basename "${BASH_SOURCE[0]}")"
	else
		echo "usage: source $(basename "$1")"
	fi
	echo "optional arguments:"
	echo "    --fish        Print fish alias syntax (internally used)"
	echo "    --gcc6        Use GCC6 cross compiler"
	echo "    --gcc4        Use GCC4 cross compiler"
	echo "    --help        Show this help message"
}


parse_args() {
	unset fish_arg
	unset gcc6_arg
	unset gcc4_arg

	while [ "${1:-}" != "" ]; do
		case $1 in
		--fish)
			fish_arg="$1"
			shift
			;;
		--gcc6)
			gcc6_arg=1
			shift
			;;
		--gcc4)
			gcc4_arg=1
			shift
			;;
		--help)
			shift
			return 0
			;;
		*)
			echo "Invalid argument: $1"
			shift
			return 0
			;;
		esac
	done

	return 1
}


main() {
	# Stop executing once a function fails
	# shellcheck disable=SC1090
	if check_kernel_folder \
		&& clean_kernel_src_dir \
		&& export_pmbootstrap_dir "$1" \
		&& set_alias_pmbootstrap \
		&& export_chroot_device_deviceinfo \
		&& check_device \
		&& . "$deviceinfo" \
		&& initialize_chroot \
		&& mount_kernel_source \
		&& create_output_folder \
		&& set_alias_make \
		&& set_alias_pmbroot_kernelroot; then

		# Success
		echo "pmbootstrap envkernel.sh activated successfully."
		echo " * kernel source:  $PWD"
		echo " * output folder:  $PWD/.output"
		echo " * architecture:   $arch ($device is $deviceinfo_arch)"
		echo " * cross compile:  $(cross_compiler_version)"
		echo " * aliases: make, kernelroot, pmbootstrap, pmbroot," \
			"run-script (see 'type make' etc.)"
	else
		# Failure
		echo "See also: <https://postmarketos.org/troubleshooting>"
		return 1
	fi
}


# Print fish alias syntax (when called from envkernel.fish)
fish_compat() {
	[ "$1" = "--fish" ] || return
	for name in make kernelroot pmbootstrap pmbroot; do
		echo "alias $(alias $name | sed 's/=/ /')"
	done
}

if parse_args "$@"; then
	print_usage "$0"
	return 1
fi

# Run main() with all output redirected to stderr
# Afterwards print fish compatible syntax to stdout
main "$0" >&2 && fish_compat "$fish_arg"
