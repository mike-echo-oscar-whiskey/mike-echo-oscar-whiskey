#!/usr/bin/env bash
# Purpose: render the README as GitHub stacks it and screenshot one moment of the animation, so the
#          page can be looked at before it is pushed. The slices are inlined because Chromium's
#          virtual clock does not advance an animation inside an <img>.
# Usage:   scripts/preview.sh <seconds-of-animation-to-advance> <output.png>
set -euo pipefail
cd "$(dirname "$0")/.."
command -v chromium >/dev/null || { echo "needs chromium"; exit 1; }
[ $# -eq 2 ] || { echo "usage: scripts/preview.sh <seconds> <output.png>"; exit 1; }
mkdir -p preview
python3 -I - > preview/index.html <<'PY'
import re
from pathlib import Path
readme = Path("README.md").read_text()
print('<!doctype html><meta charset="utf-8"><body style="margin:0;background:#fff;width:880px"><style>svg{display:block}</style>')
for n, path in enumerate(re.findall(r'src="([^"]+)"', readme)):
    svg = Path(path).read_text()
    for ident in ("scan", "vig", "bg", "glow"):
        svg = svg.replace(f'id="{ident}"', f'id="{ident}{n}"').replace(f"url(#{ident})", f"url(#{ident}{n})")
    print(svg)
print("</body>")
PY
height=$(grep -o 'height="[0-9]*" rx="22"' "$(grep -o 'assets/[^"]*01.svg' README.md)" | grep -o '[0-9]*' | head -1)
chromium --headless=new --no-sandbox --hide-scrollbars --window-size=880,$((height + 40)) \
  --run-all-compositor-stages-before-draw --disable-threaded-animation \
  --virtual-time-budget=$(( $1 * 1000 )) --screenshot="$PWD/$2" "file://$PWD/preview/index.html" 2>/dev/null
echo "=== $2: the page $1 s into its animation ==="
