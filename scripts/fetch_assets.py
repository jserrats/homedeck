#!/usr/bin/env python3
"""Download the fonts and icon metadata HomeDeck renders with.

These files are large and license-bound, so they are not committed to the repo.
Run once after install:

    python scripts/fetch_assets.py

Sources:
  - Material Design Icons webfont + metadata (Pictogrammers, Apache-2.0 / SIL OFL)
  - DejaVu Sans (used for key labels; Bitstream Vera / public-domain derived license)
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

# Pin font and metadata to the same MDI release so codepoints line up.
MDI_VERSION = "7.4.47"

ASSETS = Path(__file__).resolve().parent.parent / "homedeck" / "assets"

DOWNLOADS = {
    "materialdesignicons-webfont.ttf": (
        f"https://cdn.jsdelivr.net/npm/@mdi/font@{MDI_VERSION}/fonts/materialdesignicons-webfont.ttf"
    ),
    "mdi-meta.json": f"https://cdn.jsdelivr.net/npm/@mdi/svg@{MDI_VERSION}/meta.json",
    "DejaVuSans.ttf": "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf": "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf",
}


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for filename, url in DOWNLOADS.items():
        dest = ASSETS / filename
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  exists  {filename}")
            continue
        print(f"  fetch   {filename}  <-  {url}")
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
            dest.write_bytes(data)
            print(f"  ok      {filename} ({len(data)} bytes)")
        except Exception as exc:  # noqa: BLE001 - report and fail clearly
            print(f"  FAILED  {filename}: {exc}", file=sys.stderr)
            return 1
    print(f"\nAssets ready in {ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
