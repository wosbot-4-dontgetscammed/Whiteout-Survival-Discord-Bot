#!/usr/bin/env python3
"""Read-only screen probe for building an in-game map/profile scanner.

Captures one frame from an Android emulator over adb and runs it through the
same on-device Apple Vision OCR helper the Discord bot uses for profile
screenshots (`bin/ocr_vision`). It prints every recognised text line with its
position, which is what you need to write reliable anchors for a screen.

This script never sends input to the device - it only reads. Driving the game
(taps, swipes, jumping to coordinates) is a separate step, deliberately not
done here.

Usage:
    python3 tools/map_probe.py                    # first device, print lines
    python3 tools/map_probe.py -s emulator-5556   # pick a device
    python3 tools/map_probe.py --json out.json    # also dump structured data
    python3 tools/map_probe.py --png frame.png    # keep the screenshot
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCR_HELPER = os.path.join(REPO, "bin", "ocr_vision")
ADB_CANDIDATES = [
    os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
    "/opt/homebrew/bin/adb",
    "adb",
]


def find_adb() -> str:
    for candidate in ADB_CANDIDATES:
        try:
            subprocess.run([candidate, "version"], capture_output=True, check=True)
            return candidate
        except (OSError, subprocess.CalledProcessError):
            continue
    sys.exit("adb not found - install platform-tools or add it to PATH")


def list_devices(adb: str) -> list[str]:
    out = subprocess.run([adb, "devices"], capture_output=True, text=True).stdout
    return [
        line.split()[0]
        for line in out.splitlines()[1:]
        if line.strip() and line.split()[-1] == "device"
    ]


def capture(adb: str, serial: str | None, path: str):
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += ["exec-out", "screencap", "-p"]
    with open(path, "wb") as fh:
        result = subprocess.run(cmd, stdout=fh)
    if result.returncode != 0 or os.path.getsize(path) == 0:
        sys.exit(f"screen capture failed for {serial or 'default device'}")


def ocr(path: str) -> list[dict]:
    if not (os.path.isfile(OCR_HELPER) and os.access(OCR_HELPER, os.X_OK)):
        sys.exit(
            f"OCR helper missing at {OCR_HELPER}\n"
            "build it with: swiftc -O tools/ocr_vision.swift -o bin/ocr_vision"
        )
    result = subprocess.run([OCR_HELPER, path], capture_output=True, text=True)
    lines = []
    for raw in result.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", "--serial", help="adb device serial (default: first device)")
    parser.add_argument("--json", help="write the recognised lines to this file")
    parser.add_argument("--png", help="keep the screenshot at this path")
    parser.add_argument("--min-confidence", type=float, default=0.3)
    args = parser.parse_args()

    adb = find_adb()
    devices = list_devices(adb)
    if not devices:
        sys.exit("no adb devices online")
    serial = args.serial or devices[0]
    if serial not in devices:
        sys.exit(f"device {serial} not online. available: {', '.join(devices)}")

    png = args.png or os.path.join(tempfile.gettempdir(), f"map_probe_{serial}.png")
    capture(adb, serial, png)
    lines = [l for l in ocr(png) if l.get("c", 1) >= args.min_confidence]

    # Vision reports normalised coordinates with the origin bottom-left; sort
    # top-to-bottom so the output reads like the screen.
    lines.sort(key=lambda l: -l.get("y", 0))

    print(f"device {serial} · {len(lines)} text lines · frame {png}\n")
    for line in lines:
        x = line.get("x", 0)
        y = 1 - line.get("y", 0)
        print(f"  y={y:0.3f} x={x:0.3f} conf={line.get('c', 0):0.2f}  {line.get('t', '')}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"device": serial, "frame": png, "lines": lines}, fh, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
