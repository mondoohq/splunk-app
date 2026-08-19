#!/usr/bin/env bash
# setup_venv.sh – Create a Python 3.9 virtual environment for TA testing.
# Mirrors the Python version Splunk ships with (3.9.x).
#
# Usage:
#   cd tests
#   ./setup_venv.sh
#   source .venv/bin/activate
#   python test_mondoo_input.py        # unit + integration tests
#   python test_mondoo_input.py live   # quick live smoke-test

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

# ---------------------------------------------------------------------------
# Locate Python 3.9
# ---------------------------------------------------------------------------
PY39=""
for candidate in python3.9 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" --version 2>&1 | awk '{print $2}')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" = "3" ] && [ "$minor" = "9" ]; then
            PY39="$candidate"
            break
        fi
    fi
done

if [ -z "$PY39" ]; then
    echo "ERROR: Python 3.9 not found."
    echo "Install it with:"
    echo "  brew install python@3.9          # macOS"
    echo "  sudo apt-get install python3.9   # Debian/Ubuntu"
    echo "  pyenv install 3.9.x              # via pyenv"
    exit 1
fi

echo "Using Python: $($PY39 --version)"

# ---------------------------------------------------------------------------
# Create or refresh the venv
# ---------------------------------------------------------------------------
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment already exists at $VENV_DIR"
    echo "Delete it and re-run to recreate: rm -rf $VENV_DIR"
else
    echo "Creating virtual environment at $VENV_DIR …"
    "$PY39" -m venv "$VENV_DIR"
fi

# ---------------------------------------------------------------------------
# Install dependencies
# ---------------------------------------------------------------------------
echo "Installing test dependencies …"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "${SCRIPT_DIR}/requirements.txt"

echo ""
echo "Setup complete. Activate with:"
echo "  source ${VENV_DIR}/bin/activate"
echo ""
echo "Then run tests:"
echo "  python test_mondoo_input.py        # all tests"
echo "  python test_mondoo_input.py live   # quick smoke-test"
