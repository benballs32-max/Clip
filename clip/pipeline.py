"""Source video -> finished vertical clips plus a caption file for each."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from . import assets, highlights, render, scenes, transcribe, youtube

def _mins(sec: float) -> str:
    return f"{int(sec // 60)} min" if sec >= 120 else f"{int(sec)} s"


def _print(msg, pct=None):
    print(msg)


SPEECH_WPM = 25  # below this many words/minute a video is treated as visual footage


def _overlaps(p: dict, existing: list[dict]) -> bool:
    """True if a pick covers (mostly) the same moment as a clip we already made."""
    for c in existing:
        inter = min(p["end"], c["end"]) - max(p["start"], c["start"])
        if inter > 0.3 * min(p["end"] - p["start"], c["end"] - c["start"]):
            return True
    return False


def _free_name(out_dir: Path, base: str, start: int) -> tuple[str, int]:
    i = start
    while (out_dir / f"{base}_{i:02d}.mp4").exists():
        i += 1
    return f"{base}_{i:02d}", i + 1


def caption_text(clip: dict, campaign: dict) -> str:
    disclosure = campaign.get("disclosure", "#ad")
    tags = [t for t in campaign.get("hashtags", []) if t.lower() != disclosure.lower()]
    return "\n".join(x for x in [clip.get("caption", ""), " ".join(tags + [disclosure]).strip()] if x)


def _pretty(rel: str) -> str:
    return re.sub(r"[_\-]+", " ", Path(rel).stem).strip().title()


def process_source(src: Path, rel: str, out_dir: Path, campaign: dict,
                   progress: Callable = _print,
                   logo: Path | None = None, music: Path | None = None,
                   existing: list[dict] = ()) -> list[dict]:
    meta = assets.probe(src)
    duration = float(meta.get("format", {}).get("duration") or 0)
    src_audio = any(s.get("codec_type") == "audio" for s in meta.get("streams", []))
    min_s, max_s = campaign.get("min_seconds", 15), campaign.get("max_seconds", 60)

    segments = []
    if src_audio:
        progress(f"{rel}: {_mins(duration)} video, transcribing speech…")
        segments = transcribe.transcribe(src, progress)
    words = [w for s in segments for w in s["words"]]
    wpm = len(words) / (duration / 60) if duration else 0
    visual = wpm < SPEECH_WPM

    if visual:
        progress(f"{rel}: little speech, cutting by scene…")
        cuts = scenes.detect_cuts(src)
        picks = [{"start": a, "end": b, "title": _pretty(rel), "caption": "", "reason": "scene cut"}
                 for a, b in scenes.windows(duration, cuts, min_s, max_s, campaign.get("clips_per_video", 8))]
        words = []  # nothing to caption
    else:
        picks = highlights.pick(segments, campaign, campaign.get("clips_per_video", 8), progress)

    picks = [p for p in picks if not _overlaps(p, existing)]
    progress(f"{len(picks)} new moments to render.")
    base = re.sub(r"[^\w]+", "_", str(Path(rel).with_suffix(""))).strip("_")
    results, nxt = [], 1
    for i, p in enumerate(picks, 1):
        name, nxt = _free_name(out_dir, base, nxt)
        progress(f"Rendering clip {i}/{len(picks)}…", round((i - 1) / len(picks) * 100))
        t0 = time.time()
        out = out_dir / f"{name}.mp4"
        render.render_clip(
            src, out, p["start"], p["end"], words, mode=campaign.get("frame_mode", "blur"),
            logo=logo, logo_pos=campaign.get("logo_position", "tr"),
            logo_scale=campaign.get("logo_scale", 0.18), music=music,
            music_volume=campaign.get("music_volume", 0.12) if not visual else max(campaign.get("music_volume", 0.12), 0.6),
            src_has_audio=src_audio)
        progress(f"Clip {i} done in {time.time() - t0:.0f}s: {p['title']}")
        text = caption_text(p, campaign)
        out.with_suffix(".txt").write_text(f"{p['title']}\n\n{text}\n")
        results.append({"file": out.name, "source": rel, "title": p["title"], "caption": text,
                        "start": p["start"], "end": p["end"], "reason": p.get("reason", ""),
                        "score": p.get("score"), "src_key": rel,
                        "status": "new", "notes": "", "payout": 0})
    return results


def process_youtube(url: str, vid: str, title: str, work: Path, out_dir: Path, campaign: dict,
                    progress: Callable = _print,
                    logo: Path | None = None, music: Path | None = None,
                    existing: list[dict] = ()) -> list[dict]:
    """Transcript first (captions, else audio+Whisper), then download only the chosen moments."""
    progress(f"{title}: fetching YouTube captions…")
    segments = youtube.transcript(url, vid, work, progress)
    if not segments:
        progress("No captions found. Downloading the audio to transcribe it (slow on long videos)…")
        audio = youtube.download_audio(url, vid, work, progress)
        segments = transcribe.transcribe(audio, progress)
    if not segments:
        raise RuntimeError(f"No speech found in {title}.")
    picks = highlights.pick(segments, campaign, campaign.get("clips_per_video", 8), progress)
    picks = [p for p in picks if not _overlaps(p, existing)]
    progress(f"{len(picks)} new moments to render.")
    words = [w for s in segments for w in s["words"]]
    results, spec, nxt = [], None, 1
    for i, p in enumerate(picks, 1):
        name, nxt = _free_name(out_dir, f"yt_{vid}", nxt)
        out = out_dir / f"{name}.mp4"
        t0 = time.time()
        kw = dict(mode=campaign.get("frame_mode", "blur"), logo=logo,
                  logo_pos=campaign.get("logo_position", "tr"),
                  logo_scale=campaign.get("logo_scale", 0.18), music=music,
                  music_volume=campaign.get("music_volume", 0.12))
        progress(f"Clip {i}/{len(picks)} ({int(p['start'])}s–{int(p['end'])}s): streaming and rendering…",
                 round((i - 1) / len(picks) * 100))
        try:  # fast path: ffmpeg reads only this range straight from YouTube, one encode
            if spec is None or time.time() - spec["fetched"] > 3000:
                spec = youtube.stream_spec(url)
            render.render_clip(spec, out, p["start"], p["end"], words, **kw)
        except Exception as e:
            progress(f"Streaming failed ({str(e)[:80]}). Downloading the section instead…")
            spec = None
            sec_start = max(0.0, p["start"] - 1.0)
            section = youtube.download_section(url, sec_start, p["end"] + 1.0, work / f"{name}.src.mp4")
            shifted = [{**w, "start": w["start"] - sec_start, "end": w["end"] - sec_start} for w in words
                       if sec_start - 0.1 <= w["start"] <= p["end"] + 1.0]
            render.render_clip(section, out, p["start"] - sec_start, p["end"] - sec_start, shifted, **kw)
            section.unlink(missing_ok=True)
        progress(f"Clip {i} done in {time.time() - t0:.0f}s: {p['title']}")
        text = caption_text(p, campaign)
        out.with_suffix(".txt").write_text(f"{p['title']}\n\n{text}\n")
        results.append({"file": out.name, "source": f"{title} ({url})", "title": p["title"],
                        "caption": text, "start": p["start"], "end": p["end"],
                        "reason": p.get("reason", ""), "status": "new", "notes": "", "payout": 0})
    return results
