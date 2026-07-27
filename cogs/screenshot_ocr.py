"""Offline profile-screenshot OCR using Apple Vision (macOS).

Since 2026-07 there is no API that returns a WOS player's nickname/furnace
from a FID (see project_api_change_2026_07). The only offline substitute is
reading them from a profile screenshot. This module shells out to the bundled
Swift helper ``bin/ocr_vision`` which runs ``VNRecognizeTextRequest`` on-device:
free, offline, no API key, and — unlike ddddocr — it reads unicode/emoji
nicknames correctly.

The helper prints one JSON object per recognised text line:
``{"t": text, "x":.., "y":.., "w":.., "h":.., "c": confidence}`` with
normalised coordinates (origin bottom-left, Vision convention).
"""

import asyncio
import json
import os
import re

from .log_config import get_logger

logger = get_logger("screenshot_ocr")

_HELPER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "ocr_vision")


def available() -> bool:
    """True if the compiled OCR helper is present and executable."""
    return os.path.isfile(_HELPER) and os.access(_HELPER, os.X_OK)


async def ocr_lines(image_path: str) -> list[dict]:
    """Run Vision OCR on an image; return recognised text lines (or [])."""
    if not available():
        logger.warning("OCR helper not found/executable at %s", _HELPER)
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            _HELPER, image_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception as e:
        logger.warning("OCR helper failed: %s", e)
        return []
    if proc.returncode != 0:
        logger.warning("OCR helper rc=%s err=%s", proc.returncode, (err or b"").decode()[:200])
        return []

    lines = []
    for ln in out.decode("utf-8", "ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            lines.append(json.loads(ln))
        except Exception:
            pass
    return lines


# Field patterns, calibrated against the real WOS "Profil des Gouverneurs" /
# governor profile card (labels seen in EN + DE).
_FID_LABEL_RE = re.compile(r'(?:ID|编号|フレンドID|Player\s*ID)\s*[:：]?\s*(\d{6,10})', re.I)
_FID_ANY_RE = re.compile(r'\b(\d{8,10})\b')
# The current kingdom is shown with a leading '#': "Region: #1587". The starting
# region ("Anfangsregion") is shown WITHOUT a '#', so the '#' cleanly picks the
# current kingdom. Fall back to a labelled Region/State (never Anfangsregion).
_KID_HASH_RE = re.compile(r'#\s*(\d{2,5})\b')
_KID_LABEL_RE = re.compile(r'(?<!Anfangs)(?<!anfangs)(?:Region|State|Kingdom|Staat|État)\s*[:#]?\s*#?\s*(\d{2,5})', re.I)
_FURNACE_FC_RE = re.compile(r'\bF\.?C\.?\s*(\d{1,2})\b', re.I)
_FURNACE_LV_RE = re.compile(r'(?:Furnace|Ofen)\D{0,4}(\d{1,2})|(?:Lv|Level)\.?\s*(\d{1,2})', re.I)
_NUMERIC_ONLY_RE = re.compile(r'^[\d\s#.,%:/]+$')

# UI chrome / label words that are never a nickname (EN + DE).
_CHROME_WORDS = (
    "profil", "gouverneur", "governor", "medaillon", "medal", "insel", "besuchen",
    "visit", "allianz", "alliance", "ansehen", "freunde", "friends", "privater",
    "chat", "kills", "kampfkraft", "power", "anfangsregion", "region", "state",
    "kingdom", "staat",
)


def _is_chrome_or_field(t: str) -> bool:
    """True if the line is a label/number/UI-chrome, not a nickname candidate."""
    tl = (t or "").strip().lower()
    if not tl or _NUMERIC_ONLY_RE.match(tl):
        return True
    if _FID_LABEL_RE.search(t) or _FURNACE_FC_RE.search(t):
        return True
    return any(w in tl for w in _CHROME_WORDS)


def parse_profile(lines: list[dict]) -> dict:
    """Best-effort extraction of {nickname, fid, kid, furnace} from OCR lines.

    fid/kid/furnace are read by pattern. nickname is the non-chrome text line
    directly above the ID line (the profile card lays the name right on top of
    the ID), falling back to the top-most non-chrome line. All values may be
    None; the caller confirms/edits before saving.
    """
    joined = " \n".join(l.get("t", "") for l in lines)

    fid = None
    m = _FID_LABEL_RE.search(joined) or _FID_ANY_RE.search(joined)
    if m:
        fid = m.group(1)

    kid = None
    m = _KID_HASH_RE.search(joined) or _KID_LABEL_RE.search(joined)
    if m:
        kid = m.group(1)

    furnace = None
    m = _FURNACE_FC_RE.search(joined)
    if m:
        furnace = f"FC{m.group(1)}"
    else:
        m = _FURNACE_LV_RE.search(joined)
        if m:
            furnace = m.group(1) or m.group(2)

    candidates = [
        l for l in lines
        if l.get("t", "").strip() and not _is_chrome_or_field(l["t"]) and l.get("c", 0) >= 0.3
    ]

    # Locate the ID line and take the nearest non-chrome line just above it.
    id_line = None
    for l in lines:
        t = l.get("t", "")
        if _FID_LABEL_RE.search(t) or (fid and fid in t):
            id_line = l
            break

    nickname = None
    if id_line is not None:
        id_y = id_line.get("y", 0)
        above = sorted((l for l in candidates if l.get("y", 0) > id_y), key=lambda l: l.get("y", 0))
        if above:
            nickname = above[0]["t"].strip()
    if not nickname and candidates:
        candidates.sort(key=lambda l: l.get("y", 0), reverse=True)
        nickname = candidates[0]["t"].strip()

    return {"nickname": nickname, "fid": fid, "kid": kid, "furnace": furnace}
