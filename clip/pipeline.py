"""Source video -> finished vertical clips plus a caption file for each."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from . import assets, highlights, render, scenes, transcribe, youtube

SPEECH_WPM = 25  # below this many words/minute a video is treated as visual footage


def caption_text(clip: dict, campaign: dict) -> str:
    disclosure = campaign.get("disclosure", "#ad")
    tags = [t for t in campaign.get("hashtags", []) if t.lower() != disclosure.lower()]
    return "\n".join(x for x in [clip.get("caption", ""), " ".join(tags + [disclosure]).strip()] if x)


def _pretty(rel: str) -> str:
    return re.sub(r"[_\-]+", " ", Path(rel).stem).strip().title()


def process_source(src: Path, rel: str, out_dir: Path, campaign: dict,
                   progress: Callable[[str], None] = print,
                   logo: Path | None = None, music: Path | None = None) -> list[dict]:
    meta = assets.probe(src)
    duration = float(meta.get("format", {}).get("duration") or 0)
    src_audio = any(s.get("codec_type") == "audio" for s in meta.get("streams", []))
    min_s, max_s = campaign.get("min_seconds", 15), campaign.get("max_seconds", 60)

    segments = []
    if src_audio:
        progress(f"Transcribing {rel}…")
        segments = transcribe.transcribe(src)
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
        progress("Choosing highlights…")
        picks = highlights.pick(segments, campaign, campaign.get("clips_per_video", 8))

    base = re.sub(r"[^\w]+", "_", str(Path(rel).with_suffix(""))).strip("_")
    results = []
    for i, p in enumerate(picks, 1):
        name = f"{base}_{i:02d}"
        progress(f"Rendering clip {i}/{len(picks)}…")
        out = out_dir / f"{name}.mp4"
        render.render_clip(
            src, out, p["start"], p["end"], words, mode=campaign.get("frame_mode", "blur"),
            logo=logo, logo_pos=campaign.get("logo_position", "tr"),
            logo_scale=campaign.get("logo_scale", 0.18), music=music,
            music_volume=campaign.get("music_volume", 0.12) if not visual else max(campaign.get("music_volume", 0.12), 0.6),
            src_has_audio=src_audio)
        text = caption_text(p, campaign)
        out.with_suffix(".txt").write_text(f"{p['title']}\n\n{text}\n")
        results.append({"file": out.name, "source": rel, "title": p["title"], "caption": text,
                        "start": p["start"], "end": p["end"], "reason": p.get("reason", ""),
                        "status": "new", "notes": "", "payout": 0})
    return results


def process_youtube(url: str, vid: str, title: str, work: Path, out_dir: Path, campaign: dict,
                    progress: Callable[[str], None] = print,
                    logo: Path | None = None, music: Path | None = None) -> list[dict]:
    """Transcript first (captions, else audio+Whisper), then download only the chosen moments."""
    progress(f"{title}: fetching captions…")
    segments = youtube.transcript(url, vid, work)
    if not segments:
        progress("No captions. Downloading audio and transcribing (slow on long videos)…")
        audio = youtube.download_audio(url, vid, work)
        segments = transcribe.transcribe(audio)
    if not segments:
        raise RuntimeError(f"No speech found in {title}.")
    progress("Choosing highlights…")
    picks = highlights.pick(segments, campaign, campaign.get("clips_per_video", 8))
    words = [w for s in segments for w in s["words"]]
    results = []
    for i, p in enumerate(picks, 1):
        name = f"yt_{vid}_{i:02d}"
        sec_start = max(0.0, p["start"] - 1.0)
        progress(f"Downloading moment {i}/{len(picks)} ({int(p['start'])}s–{int(p['end'])}s)…")
        section = youtube.download_section(url, sec_start, p["end"] + 1.0, work / f"{name}.src.mp4")
        shifted = [{**w, "start": w["start"] - sec_start, "end": w["end"] - sec_start} for w in words
                   if sec_start - 0.1 <= w["start"] <= p["end"] + 1.0]
        progress(f"Rendering clip {i}/{len(picks)}…")
        out = out_dir / f"{name}.mp4"
        render.render_clip(section, out, p["start"] - sec_start, p["end"] - sec_start, shifted,
                           mode=campaign.get("frame_mode", "blur"), logo=logo,
                           logo_pos=campaign.get("logo_position", "tr"),
                           logo_scale=campaign.get("logo_scale", 0.18), music=music,
                           music_volume=campaign.get("music_volume", 0.12))
        section.unlink(missing_ok=True)
        text = caption_text(p, campaign)
        out.with_suffix(".txt").write_text(f"{p['title']}\n\n{text}\n")
        results.append({"file": out.name, "source": f"{title} ({url})", "title": p["title"],
                        "caption": text, "start": p["start"], "end": p["end"],
                        "reason": p.get("reason", ""), "status": "new", "notes": "", "payout": 0})
    return results
