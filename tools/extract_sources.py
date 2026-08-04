#!/usr/bin/env python3
"""Recover original TypeScript sources from wl-device-kit's source map."""
import json
import os
import pathlib

KIT = ("/Applications/input.app/Contents/Resources/app.asar.unpacked"
       "/node_modules/@worklouder/wl-device-kit/dist")
OUT = pathlib.Path("/Users/billpapas/AI/worklouder/wl-device-kit-src")

for mapname in ("index.js.map", "browser_safe.js.map"):
    p = os.path.join(KIT, mapname)
    if not os.path.exists(p):
        continue
    m = json.load(open(p))
    sources = m.get("sources", [])
    contents = m.get("sourcesContent") or []
    print(f"=== {mapname}: {len(sources)} sources, {len(contents)} with content")

    for i, src in enumerate(sources):
        body = contents[i] if i < len(contents) else None
        if not body:
            continue
        rel = src.lstrip("./").replace("../", "")
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body)

written = sorted(OUT.rglob("*"))
files = [f for f in written if f.is_file()]
print(f"\nwrote {len(files)} files to {OUT}\n")
for f in files:
    print(f"  {f.relative_to(OUT)}  ({f.stat().st_size}B)")
