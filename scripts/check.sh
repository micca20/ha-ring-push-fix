#!/usr/bin/env bash
# Pre-release checks: syntax, JSON, manifest version has a CHANGELOG entry, ruff.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
C=custom_components/ring_push_fix
python3 -m py_compile $C/*.py
for f in $C/manifest.json hacs.json; do python3 -m json.tool "$f" >/dev/null; done
VER=$(python3 -c "import json;print(json.load(open('$C/manifest.json'))['version'])")
grep -q "^## \[$VER\]" CHANGELOG.md || { echo "CHANGELOG.md has no entry for $VER"; exit 1; }
if command -v ruff >/dev/null; then ruff check $C; elif command -v uvx >/dev/null; then uvx -q ruff check $C; fi
echo "checks OK (version $VER)"
