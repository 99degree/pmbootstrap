#!/bin/sh -e

set -x

if [ "$(id -u)" = 0 ]; then
	exec su "${TESTUSER:-build}" -c "sh -e $0"
fi

pmbootstrap() {
	./pmbootstrap.py --details-to-stdout "$@"
}

# Make sure that the work folder format is up to date, and that there are no
# mounts from aborted test cases (pmbootstrap#1595)
echo "Initializing pmbootstrap"
yes '' | ./pmbootstrap.py --details-to-stdout init

pmbootstrap work_migrate
pmbootstrap -q shutdown

# TODO: make device configurable?
device="qemu-amd64"
# TODO: make UI configurable?
ui="phosh"
pmbootstrap config device "$device"
pmbootstrap config ui "$ui"

# NOTE: --no-image is used because building images makes pmb try to
# "modprobe loop". This fails in CI.
echo "Building $ui image for $device"
pmbootstrap -y install --zap --password 147147 --no-image

echo "Building $ui image for $device, with FDE"
pmbootstrap -y install --zap --password 147147 --fde --no-image
