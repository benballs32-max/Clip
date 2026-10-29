"""YouTube sources via yt-dlp: metadata, captions as transcript, section-only downloads."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.utils import download_range_func

HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
ID_RE = re.compile(r"^[\w-]{11}$")


def normalize(url: str) -> tuple[str, str]:
    """Return (video_id, canonical_url). Only plain YouTube video links are accepted."""
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or u.hostname not in HOSTS:
        raise ValueError("That doesn't look like a YouTube video link.")
    if u.hostname == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif u.path == "/watch":
        vid = (parse_qs(u.query).get("v") or [""])[0]
    else:  # /shorts/ID, /live/ID, /embed/ID
        parts = [p for p in u.path.split("/") if p]
        vid = parts[1] if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed") else ""
    if not ID_RE.match(vid):
        raise ValueError("Couldn't find a video ID in that link (playlists and channels aren't supported).")
    return vid, f"https://www.youtube.com/watch?v={vid}"


def _opts(**extra) -> dict:
    deno = Path(sys.executable).parent / "deno"
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "retries": 3,
            "js_runtimes": {"deno": {"path": str(deno)} if deno.exists() else {}}}
    opts.update(extra)
    return opts


FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]"


def stream_spec(url: str) -> dict:
    """Direct (temporary, signed) stream URLs so ffmpeg can read just the part it needs."""
    with yt_dlp.YoutubeDL(_opts(format=FORMAT)) as ydl:
        d = ydl.extract_info(url, download=False)
    fmts = d.get("requested_formats") or [d]
    video = next(f for f in fmts if f.get("vcodec") not in (None, "none"))
    audio = next((f for f in fmts if f is not video and f.get("acodec") not in (None, "none")), None)
    headers = {**d.get("http_headers", {}), **video.get("http_headers", {})}
    return {"video": video["url"], "audio": audio["url"] if audio else None,
            "headers": "".join(f"{k}: {v}\r\n" for k, v in headers.items()), "fetched": time.time()}


def info(url: str) -> dict:
    with yt_dlp.YoutubeDL(_opts()) as ydl:
        d = ydl.extract_info(url, download=False)
    return {"id": d["id"], "title": d.get("title", d["id"]), "duration": d.get("duration") or 0,
            "channel": d.get("channel") or d.get("uploader") or "", "license": d.get("license") or ""}


def _parse_json3(data: dict) -> list[dict]:
    """Caption events -> word list [{start,end,word}]. Auto captions carry per-word offsets."""
    words = []
    for ev in data.get("events", []):
        segs = [s for s in ev.get("segs", []) if s.get("utf8", "").strip()]
        if not segs:
            continue
        t0, dur = ev.get("tStartMs", 0), ev.get("dDurationMs", 0)
        toks = [(w, s.get("tOffsetMs")) for s in segs for w in s["utf8"].split()]
        timed = any(off for _, off in toks)
        for i, (w, off) in enumerate(toks):
            ms = t0 + (off or 0) if timed else t0 + dur * i // max(len(toks), 1)
            words.append({"start": ms / 1000, "end": ms / 1000, "word": w})
    out, last = [], -1.0
    for w in sorted(words, key=lambda w: w["start"]):
        if w["start"] >= last - 0.05:
            out.append(w)
            last = w["start"]
    for a, b in zip(out, out[1:]):
        a["end"] = max(min(b["start"], a["start"] + 1.0), a["start"] + 0.08)
    if out:
        out[-1]["end"] = out[-1]["start"] + 0.4
    return out


_JUNK = re.compile(r"^(>+|-+|\[[^\]]*\]|\([^)]*\)|♪+)$")  # ">>" speaker changes, [Music], (laughs)


def _clean(segs: list[dict]) -> list[dict]:
    out = []
    for sg in segs:
        ws = [w for w in sg["words"] if not _JUNK.match(w["word"])]
        if ws:
            out.append({"start": ws[0]["start"], "end": ws[-1]["end"],
                        "text": " ".join(w["word"] for w in ws), "words": ws})
    return out


def _segments(words: list[dict]) -> list[dict]:
    """Group words into phrase-sized segments with word timings (same shape as Whisper's)."""
    segs, cur = [], []
    for w in words:
        if cur and (len(cur) >= 14 or w["start"] - cur[-1]["end"] > 1.2
                    or (len(cur) >= 8 and cur[-1]["word"][-1] in ".?!")):
            segs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return [{"start": s[0]["start"], "end": s[-1]["end"], "text": " ".join(x["word"] for x in s),
             "words": s} for s in segs]


def _hook(progress, label):
    """yt-dlp progress hook that reports every 10%."""
    last = [-10]

    def hook(d):
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if d.get("status") != "downloading" or not total:
            return
        pct = int(d["downloaded_bytes"] * 100 / total)
        if pct >= last[0] + 10:
            last[0] = pct - pct % 10
            progress(f"{label} {pct}%", pct)
    return hook


def transcript(url: str, vid: str, work: Path, progress=None) -> list[dict] | None:
    """Transcript from YouTube's own captions, or None if there are none.

    One track per request: asking for two at once makes YouTube rate-limit (HTTP 429) the
    second, and a single error used to discard the track that had already arrived."""
    say = progress or (lambda *a: None)
    work.mkdir(parents=True, exist_ok=True)
    cache = work / f"{vid}.transcript.json"
    if cache.exists():
        say("Using cached transcript.")
        return _clean(json.loads(cache.read_text()))
    # (auto captions have per-word timing so come first; manual captions are the fallback)
    for label, auto, lang in (("auto captions", True, "en-orig"), ("auto captions", True, "en"),
                              ("manual captions", False, "en")):
        for attempt in range(2):
            for old in work.glob(f"{vid}.cap.*"):
                old.unlink()
            try:
                with yt_dlp.YoutubeDL(_opts(
                        skip_download=True, writeautomaticsub=auto, writesubtitles=not auto,
                        subtitleslangs=[lang], subtitlesformat="json3",
                        outtmpl=str(work / f"{vid}.cap.%(ext)s"))) as ydl:
                    ydl.download([url])
            except Exception as e:
                if "429" in str(e) and attempt == 0:
                    say("YouTube rate-limited the caption request, retrying…")
                    time.sleep(5)
                    continue
            break
        files = sorted(work.glob(f"{vid}.cap.*.json3"))  # a file may exist even if yt-dlp raised
        if files:
            segs = _clean(_segments(_parse_json3(json.loads(files[0].read_text()))))
            if segs:
                say(f"Got {label} ({lang}): {len(segs)} phrases, {int(segs[-1]['end'] // 60)} min of speech.")
                cache.write_text(json.dumps(segs))
                return segs
    return None


def download_audio(url: str, vid: str, work: Path, progress=None) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    hooks = [_hook(progress, "Downloading audio")] if progress else []
    with yt_dlp.YoutubeDL(_opts(format="bestaudio[ext=m4a]/bestaudio/best", progress_hooks=hooks,
                                outtmpl=str(work / f"{vid}.audio.%(ext)s"))) as ydl:
        ydl.download([url])
    return next(work.glob(f"{vid}.audio.*"))


def download_section(url: str, start: float, end: float, dest: Path) -> Path:
    """Download just [start, end] seconds, up to 1080p, as dest (.mp4)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    with yt_dlp.YoutubeDL(_opts(
            format=FORMAT, merge_output_format="mp4", download_ranges=download_range_func(None, [(start, end)]),
            force_keyframes_at_cuts=True, outtmpl=str(dest.with_suffix(".%(ext)s")))) as ydl:
        ydl.download([url])
    return dest
