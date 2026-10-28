"""Tiny JSON-file store: one folder per campaign under DATA_DIR/campaigns."""
import json
import re
import shutil
import threading
import uuid
from pathlib import Path

from . import config

ROOT = config.DATA_DIR / "campaigns"
ROOT.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()

DEFAULTS = {
    "name": "", "platform": "TikTok", "rate_cpm": 1.0, "budget_note": "", "rules": "",
    "hashtags": [], "disclosure": "#ad", "min_seconds": 15, "max_seconds": 60,
    "clips_per_video": 8, "frame_mode": "blur",
}


def cdir(cid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{8}", cid):
        raise KeyError(cid)
    return ROOT / cid


def _write(cid: str, data: dict) -> None:
    (cdir(cid) / "campaign.json").write_text(json.dumps(data, indent=2))


def get(cid: str) -> dict:
    p = cdir(cid) / "campaign.json"
    if not p.exists():
        raise KeyError(cid)
    return json.loads(p.read_text())


def list_all() -> list[dict]:
    out = [json.loads(p.read_text()) for p in ROOT.glob("*/campaign.json")]
    return sorted(out, key=lambda c: c["created"])


def create(fields: dict) -> dict:
    cid = uuid.uuid4().hex[:8]
    with _lock:
        (cdir(cid) / "sources").mkdir(parents=True)
        (cdir(cid) / "clips").mkdir()
        data = {**DEFAULTS, **{k: v for k, v in fields.items() if k in DEFAULTS},
                "id": cid, "created": len(list_all()) + 1, "sources_done": [], "clips": []}
        _write(cid, data)
    return data


def update(cid: str, fields: dict) -> dict:
    with _lock:
        data = get(cid)
        data.update({k: v for k, v in fields.items() if k in DEFAULTS})
        _write(cid, data)
    return data


def add_clips(cid: str, source: str, clips: list[dict]) -> None:
    with _lock:
        data = get(cid)
        data["clips"].extend(clips)
        if source not in data["sources_done"]:
            data["sources_done"].append(source)
        _write(cid, data)


def update_clip(cid: str, filename: str, fields: dict) -> dict:
    with _lock:
        data = get(cid)
        for c in data["clips"]:
            if c["file"] == filename:
                c.update({k: v for k, v in fields.items() if k in ("status", "notes", "payout", "post_url")})
                _write(cid, data)
                return c
    raise KeyError(filename)


def delete(cid: str) -> None:
    with _lock:
        shutil.rmtree(cdir(cid), ignore_errors=True)
