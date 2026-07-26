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


# Field patterns (calibrated against the WOS profile card layout).
_FID_LABEL_RE = re.compile(r'(?:ID|编号|フレンドID|Player\s*ID)\D{0,3}(\d{6,10})', re.I)
_FID_ANY_RE = re.compile(r'\b(\d{8,10})\b')
_STATE_RE = re.compile(r'(?:State|Kingdom|Staat|État|#)\s*#?\s*(\d{2,5})', re.I)
_FURNACE_FC_RE = re.compile(r'\bF\.?C\.?\s*(\d{1,2})\b', re.I)
_FURNACE_LV_RE = re.compile(r'(?:Lv|Level|Furnace|Ofen)\.?\s*(\d{1,2})', re.I)
_NUMERIC_ONLY_RE = re.compile(r'^[\d\s#.,%:/]+$')


def _is_field_line(t: str) -> bool:
    """A recognised label/number line (not a nickname candidate)."""
    return bool(
        _FID_LABEL_RE.search(t) or _STATE_RE.search(t)
        or _FURNACE_FC_RE.search(t) or _FURNACE_LV_RE.search(t)
        or _NUMERIC_ONLY_RE.match(t or "")
    )


def parse_profile(lines: list[dict]) -> dict:
    """Best-effort extraction of {nickname, fid, kid, furnace} from OCR lines.

    fid/kid/furnace are read by pattern. nickname is taken as the top-most
    (largest normalised y) high-confidence text line that is not a recognised
    field. All values may be None; the caller confirms/edits before saving.
    """
    joined = " \n".join(l.get("t", "") for l in lines)

    fid = None
    m = _FID_LABEL_RE.search(joined) or _FID_ANY_RE.search(joined)
    if m:
        fid = m.group(1)

    kid = None
    m = _STATE_RE.search(joined)
    if m:
        kid = m.group(1)

    furnace = None
    m = _FURNACE_FC_RE.search(joined)
    if m:
        furnace = f"FC{m.group(1)}"
    else:
        m = _FURNACE_LV_RE.search(joined)
        if m:
            furnace = m.group(1)

    candidates = [
        l for l in lines
        if l.get("t", "").strip() and not _is_field_line(l["t"]) and l.get("c", 0) >= 0.3
    ]
    candidates.sort(key=lambda l: l.get("y", 0), reverse=True)  # top of image first
    nickname = candidates[0]["t"].strip() if candidates else None

    return {"nickname": nickname, "fid": fid, "kid": kid, "furnace": furnace}
