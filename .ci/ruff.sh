#!/bin/sh -e
# Description: lint all python scripts
# https://postmarketos.org/pmb-ci

if [ "$(id -u)" = 0 ]; then
	set -x
	apk -q add ruff
	exec su "${TESTUSER:-build}" -c "sh -e $0"
fi

DID_FAIL=0

set -x

# __init__.py with additional ignore:
# F401: imported, but not used
# shellcheck disable=SC2046
ruff check --ignore "F401" $(find . -not -path '*/venv/*' -name '__init__.py') || DID_FAIL=1

# Check all other files
ruff check --exclude=__init__.py . || DID_FAIL=1

# Check formatting
ruff format --diff || DID_FAIL=1

exit $DID_FAIL
