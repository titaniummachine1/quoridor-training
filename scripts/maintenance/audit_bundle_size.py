#!/usr/bin/env python3
"""Size-ranked inventory of an Oracle upload bundle (manifest-only, no repo walk)."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle_dir", type=Path)
    ap.add_argument("--min-kb", type=float, default=250)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    manifest = json.loads((args.bundle_dir / "transfer-manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files") or []
    total = sum(f["size_bytes"] for f in files)
    print(f"bundle={args.bundle_dir.name} files={len(files)} bytes={total} manifest={manifest.get('manifest_sha256','')}")
    print(f"\n=== top {args.top} ===")
    for e in sorted(files, key=lambda x: -x["size_bytes"])[: args.top]:
        pct = 100 * e["size_bytes"] / total if total else 0
        print(f"{e['size_bytes']:>10} {pct:5.1f}% {e['path']}")
    print(f"\n=== >={args.min_kb} KB ===")
    threshold = int(args.min_kb * 1024)
    for e in sorted(files, key=lambda x: -x["size_bytes"]):
        if e["size_bytes"] >= threshold:
            print(f"{e['size_bytes']:>10} {e['path']}")
    print("\n=== by top-level dir ===")
    by_dir: dict[str, int] = defaultdict(int)
    for e in files:
        p = e["path"].replace("\\", "/")
        top = p.split("/")[0] if "/" in p else p
        by_dir[top] += e["size_bytes"]
    for k, v in sorted(by_dir.items(), key=lambda x: -x[1]):
        print(f"{v:>10} {100*v/total:5.1f}% {k}")
    print("\n=== by extension ===")
    by_ext: dict[str, int] = defaultdict(int)
    for e in files:
        ext = Path(e["path"]).suffix.lower() or "(no ext)"
        by_ext[ext] += e["size_bytes"]
    for k, v in sorted(by_ext.items(), key=lambda x: -x[1]):
        print(f"{v:>10} {100*v/total:5.1f}% {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
