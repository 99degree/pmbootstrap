#!/bin/sh -e
# Description: create documentation with sphinx
# Artifacts: public/
# https://postmarketos.org/pmb-ci


# Install sphinx + extensions when running in CI
if [ "$(id -u)" = 0 ]; then
	set -x
	apk -q add \
		py3-myst-parser \
		py3-sphinx_rtd_theme \
		py3-sphinxcontrib-autoprogram
	exec su "${TESTUSER:-build}" -c "sh -e $0"
fi

# Require sphinx to be installed on the host system
if [ -z "$(command -v sphinx-build)" ]; then
	echo "ERROR: sphinx-build command not found, make sure it is in your PATH."
	exit 1
fi

# Sanity check docs that all modules are documented.
# Ignore all packages and files named test*
fail=0
modules="$(find pmb/ -name "*.py" | grep -v '/__init__.py' | grep -v '/test' | grep -v '/conftest.py' | sort | sed 's|\.py$||' | sed 's|/|.|g')"
for module in $modules; do
    if ! grep -q "automodule:: $module" docs/api/*.rst; then
        echo "Undocumented module: $module"
        fail=1
    fi
done
if [ "$fail" -eq 1 ]; then
    echo "ERROR: Found undocumented modules!"
    echo "ERROR: Please add this module to the correct .rst file in docs/api/"
    exit 1
fi

sphinx-build \
	docs \
	public \

#	-E -a -v -T
