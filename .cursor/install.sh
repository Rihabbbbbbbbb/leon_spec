#!/usr/bin/env bash
# Idempotent bootstrap for the LEON Spec Validator Cloud Agent environment.
# Runs after the repository is checked out. Safe to run repeatedly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 1. Ensure the Python venv toolchain is present (Debian/Ubuntu splits it out).
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    PY_MINOR="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        sudo apt-get update -qq
        sudo apt-get install -y "python${PY_MINOR}-venv" python3-pip
    else
        echo "WARNING: python venv module missing and passwordless sudo unavailable." >&2
    fi
fi

# 2. Create the virtual environment once; reuse it on later runs.
if [ ! -x ".venv/bin/python" ]; then
    python3 -m venv .venv
fi

# 3. Install / refresh Python dependencies.
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# 4. Ensure the runtime upload directory exists (gitignored contents).
mkdir -p data/uploads

echo "LEON Spec Validator environment ready."
