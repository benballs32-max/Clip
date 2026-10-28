"""Classify uploaded files (video / image / audio / document) and guess their role."""
import json
import subprocess
from pathlib import Path

VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".aiff", ".ogg"}
DOC = {".pdf", ".docx", ".txt", ".md"}
ROLES = ["source", "logo", "music", "image", "brief", "ignore"]
ALPHA_FORMATS = {"rgba", "bgra", "argb", "abgr", "ya8", "gbrap", "pal8"}
LOGO_HINTS = ("logo", "watermark", "brand", "wordmark")


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    for name, exts in (("video", VIDEO), ("image", IMAGE), ("audio", AUDIO), ("doc", DOC)):
        if ext in exts:
            return name
    return "other"


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                        "-show_format", str(path)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {}


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


def classify(path: Path, rel: str) -> dict:
    """Return {path, kind, role, duration, width, height, alpha, size}."""
    kind = kind_of(path)
    info = {"path": rel, "kind": kind, "role": "ignore", "duration": 0.0,
            "width": 0, "height": 0, "alpha": False, "size": path.stat().st_size}
    if kind == "doc":
        info["role"] = "brief"
        return info
    if kind == "other":
        return info
    meta = probe(path)
    vs = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), {})
    info["duration"] = float(meta.get("format", {}).get("duration") or 0)
    info["width"], info["height"] = vs.get("width", 0), vs.get("height", 0)
    info["alpha"] = vs.get("pix_fmt", "") in ALPHA_FORMATS or vs.get("pix_fmt", "").startswith("yuva")
    if kind == "video":
        info["role"] = "source"
    elif kind == "audio":
        info["role"] = "music"
    elif kind == "image":
        hinted = any(h in path.stem.lower() for h in LOGO_HINTS)
        small = max(info["width"], info["height"]) <= 1500
        info["role"] = "logo" if hinted or (info["alpha"] and small) else "image"
    return info
