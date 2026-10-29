"""Tiny JSON-file store: one folder per campaign under DATA_DIR/campaigns.

A campaign holds the brief, settings and shared assets (logo, music, brief files).
Each piece of content (one video or YouTube link) is its own item with its own clips."""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config

ROOT = config.DATA_DIR / "campaigns"
ROOT.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()
_ID = re.compile(r"[0-9a-f]{8}")

DEFAULTS = {
    "name": "", "platform": "TikTok", "rate_cpm": 1.0, "budget_note": "", "rules": "",
    "hashtags": [], "disclosure": "#ad", "min_seconds": 15, "max_seconds": 60,
    "clips_per_video": 8, "frame_mode": "blur",
    "logo_position": "tr", "logo_scale": 0.18, "music_volume": 0.12,
}
CLIP_FIELDS = ("status", "notes", "payout", "post_url")


def cdir(cid: str) -> Path:
    if not _ID.fullmatch(cid):
        raise KeyError(cid)
    return ROOT / cid


def _write(cid: str, data: dict) -> None:
    p = cdir(cid) / "campaign.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)  # atomic, so a reader never sees half a file


def _coerce(key: str, value):
    """Convert a setting to its proper type, or raise ValueError (blank and junk are rejected)."""
    want = type(DEFAULTS[key])
    if want is list:
        return value if isinstance(value, list) else str(value).split()
    if want in (int, float):
        if isinstance(value, bool) or str(value).strip() == "":
            raise ValueError(key)
        return int(float(value)) if want is int else float(value)
    return str(value)


def _norm(data: dict) -> dict:
    for k, v in DEFAULTS.items():  # settings added later, or saved blank / as text by older versions
        try:
            data[k] = _coerce(k, data[k]) if k in data else v
        except (ValueError, TypeError):
            data[k] = v
    data.setdefault("assets", [])
    data.setdefault("brief_text", "")
    data.setdefault("content", [])
    return data


def get(cid: str) -> dict:
    p = cdir(cid) / "campaign.json"
    if not p.exists():
        raise KeyError(cid)
    return _norm(json.loads(p.read_text()))


def list_all() -> list[dict]:
    out = [_norm(json.loads(p.read_text())) for p in ROOT.glob("*/campaign.json")]
    return sorted(out, key=lambda c: c["created"])


def create(fields: dict) -> dict:
    cid = uuid.uuid4().hex[:8]
    with _lock:
        (cdir(cid) / "assets").mkdir(parents=True)
        (cdir(cid) / "clips").mkdir()
        data = {**DEFAULTS, **{k: v for k, v in fields.items() if k in DEFAULTS},
                "id": cid, "created": len(list_all()) + 1, "assets": [], "brief_text": "", "content": []}
        _write(cid, data)
    return data


def update(cid: str, fields: dict) -> dict:
    with _lock:
        data = get(cid)
        for k, v in fields.items():
            if k in DEFAULTS:
                try:
                    data[k] = _coerce(k, v)
                except (ValueError, TypeError):
                    pass  # blank or invalid: keep the current value
        _write(cid, data)
    return data


def delete(cid: str) -> None:
    with _lock:
        shutil.rmtree(cdir(cid), ignore_errors=True)


# ---- shared assets (brief files, logo, music) ----

def upsert_asset(cid: str, asset: dict) -> None:
    with _lock:
        data = get(cid)
        data["assets"] = [a for a in data["assets"] if a["path"] != asset["path"]] + [asset]
        data["assets"].sort(key=lambda a: a["path"])
        _write(cid, data)


def set_asset_role(cid: str, path: str, role: str) -> None:
    with _lock:
        data = get(cid)
        for a in data["assets"]:
            if a["path"] == path:
                a["role"] = role
                _write(cid, data)
                return
    raise KeyError(path)


def remove_asset(cid: str, path: str) -> None:
    with _lock:
        data = get(cid)
        data["assets"] = [a for a in data["assets"] if a["path"] != path]
        _write(cid, data)


def set_brief_text(cid: str, text: str) -> None:
    with _lock:
        data = get(cid)
        data["brief_text"] = text
        _write(cid, data)


# ---- content items: one video / YouTube link each, with its own clips ----

def _item(data: dict, xid: str) -> dict:
    if not _ID.fullmatch(xid):
        raise KeyError(xid)
    for x in data["content"]:
        if x["id"] == xid:
            return x
    raise KeyError(xid)


def get_content(cid: str, xid: str) -> dict:
    return _item(get(cid), xid)


def create_content(cid: str, title: str, source: dict | None = None) -> dict:
    with _lock:
        data = get(cid)
        item = {"id": uuid.uuid4().hex[:8], "title": title.strip()[:120] or "Untitled content",
                "kind": None, "created": time.time(), "clips": []}
        item.update(source or {})
        data["content"].append(item)
        _write(cid, data)
    return item


def update_content(cid: str, xid: str, fields: dict) -> dict:
    """Rename, or set the source (kind/url/file/duration...)."""
    allowed = {"title", "kind", "url", "file", "yt_title", "channel", "license", "duration",
               "width", "height", "size"}
    with _lock:
        data = get(cid)
        item = _item(data, xid)
        item.update({k: v for k, v in fields.items() if k in allowed})
        if "title" in fields:
            item["title"] = str(fields["title"]).strip()[:120] or item["title"]
        _write(cid, data)
    return item


def delete_content(cid: str, xid: str) -> dict:
    """Remove the item and return it so the caller can delete its files."""
    with _lock:
        data = get(cid)
        item = _item(data, xid)
        data["content"] = [x for x in data["content"] if x["id"] != xid]
        _write(cid, data)
    return item


def add_clips(cid: str, xid: str, clips: list[dict]) -> None:
    with _lock:
        data = get(cid)
        _item(data, xid)["clips"].extend(clips)
        _write(cid, data)


def update_clip(cid: str, xid: str, filename: str, fields: dict) -> dict:
    with _lock:
        data = get(cid)
        for c in _item(data, xid)["clips"]:
            if c["file"] == filename:
                c.update({k: v for k, v in fields.items() if k in CLIP_FIELDS})
                _write(cid, data)
                return c
    raise KeyError(filename)


# ---- one-off migration from the old layout (flat clips + video assets) ----

def _migrate(data: dict) -> dict:
    clips = data.pop("clips", [])
    data.pop("sources_done", None)
    keep, content = [], []
    for a in data.get("assets", []):
        if a.get("kind") in ("video", "youtube") and a.get("role") == "source":
            vid = a["path"].split(":", 1)[1] if a["kind"] == "youtube" else None
            mine = [c for c in clips if c.get("src_key") == a["path"] or c.get("source") == a["path"]
                    or (vid and vid in c.get("source", ""))]
            clips = [c for c in clips if c not in mine]
            item = {"id": uuid.uuid4().hex[:8], "created": time.time() + len(content),
                    "title": a.get("title") or Path(a["path"]).stem, "clips": mine,
                    "duration": a.get("duration", 0)}
            if a["kind"] == "youtube":
                item.update(kind="youtube", url=a["url"], yt_title=a.get("title", ""),
                            channel=a.get("channel", ""), license=a.get("license", ""))
            else:
                item.update(kind="video", file=f"assets/{a['path']}", width=a.get("width", 0),
                            height=a.get("height", 0), size=a.get("size", 0))
            content.append(item)
        else:
            keep.append(a)
    if clips:  # clips whose source we can't identify
        content.append({"id": uuid.uuid4().hex[:8], "created": time.time() + len(content),
                        "title": "Imported clips", "kind": None, "clips": clips})
    data["assets"], data["content"] = keep, content
    return data


def migrate_all() -> int:
    """Convert any campaign still in the old layout. Keeps a backup next to it. Returns count."""
    n = 0
    with _lock:
        for p in ROOT.glob("*/campaign.json"):
            data = json.loads(p.read_text())
            if "content" in data:
                fixed = _norm(json.loads(p.read_text()))  # repair blank / text-typed settings in place
                if fixed != data:
                    _write(data["id"], fixed)
                continue
            shutil.copy2(p, p.with_name("campaign.json.bak-v1"))
            _write(data["id"], _migrate(data))
            n += 1
    return n
