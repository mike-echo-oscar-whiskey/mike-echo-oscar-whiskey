#!/usr/bin/env bash
# Purpose: refresh the profile README: fetch today's weather and the current pull requests, rebuild
#          the CRT slices, run the tests, and say which patches show a raw title instead of curated text.
# Usage:   scripts/update-profile.sh
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in gh python3 git; do command -v "$tool" >/dev/null || { echo "needs $tool"; exit 1; }; done

echo "=== Step 1: fetch ==="
python3 -I scripts/fetch.py
echo "=== Step 2: build ==="
python3 -I scripts/build.py
echo "=== Step 3: tests ==="
python3 -I -m unittest discover -s tests
echo "=== Step 4: what changed ==="
git status --short
echo "=== Step 5: pull requests newly shown since the last commit (curate their wording in profile.toml) ==="
git diff --unified=0 HEAD -- data/patches.json | grep '^+ *"url"' || echo "(none)"
