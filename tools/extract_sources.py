#!/usr/bin/env python3
"""Recover the original TypeScript sources of the vendor SDK from its source maps.

`@worklouder/wl-device-kit` ships *unpacked and with source maps* inside the Input
desktop app, so `sourcesContent` in the map files contains the whole SDK verbatim.
That is how the vendor HID RPC framing in `docs/VENDOR-RPC.md` was recovered.

usage:  LM_INPUT_APP=/Applications/input.app python3 extract_sources.py

The extracted files are **vendor IP** — they land in `extracted-src/`, which is
git-ignored and must never be committed.
"""
import json
import os
import pathlib

APP = os.environ.get("LM_INPUT_APP", "/Applications/input.app")
if not os.path.isdir(APP):
    raise SystemExit(
        "Set LM_INPUT_APP to Work Louder's Input app bundle, e.g.\n"
        "  LM_INPUT_APP=/Applications/input.app python3 " + os.path.basename(__file__) + "\n"
        "This repo deliberately ships no vendor SDK — see tools/README.md.")

KIT = os.path.join(APP, "Contents/Resources/app.asar.unpacked"
                        "/node_modules/@worklouder/wl-device-kit/dist")
if not os.path.isdir(KIT):
    raise SystemExit(
        f"No wl-device-kit dist under {APP}.\n"
        "Expected Contents/Resources/app.asar.unpacked/node_modules/"
        "@worklouder/wl-device-kit/dist — the app version may have changed.")

# Default next to the repo root, matching .gitignore's `extracted-src/` entry.
OUT = pathlib.Path(os.environ.get(
    "LM_EXTRACT_OUT",
    pathlib.Path(__file__).resolve().parent.parent / "extracted-src"))

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
