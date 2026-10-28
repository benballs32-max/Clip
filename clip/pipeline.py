"""Source video -> finished vertical clips plus a caption file for each."""
from pathlib import Path
from typing import Callable

from . import highlights, render, transcribe

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def caption_text(clip: dict, campaign: dict) -> str:
    tags = " ".join(campaign.get("hashtags", []))
    disclosure = campaign.get("disclosure", "#ad")
    return "\n".join(x for x in [clip.get("caption", ""), f"{tags} {disclosure}".strip()] if x)


def process_source(src: Path, out_dir: Path, campaign: dict,
                   progress: Callable[[str], None] = print) -> list[dict]:
    progress(f"Transcribing {src.name}…")
    segments = transcribe.transcribe(src)
    progress("Choosing highlights…")
    picks = highlights.pick(segments, campaign, campaign.get("clips_per_video", 8))
    words = [w for s in segments for w in s["words"]]
    results = []
    for i, p in enumerate(picks, 1):
        name = f"{src.stem}_{i:02d}"
        progress(f"Rendering clip {i}/{len(picks)}…")
        out = out_dir / f"{name}.mp4"
        render.render_clip(src, out, p["start"], p["end"], words,
                           mode=campaign.get("frame_mode", "blur"))
        text = caption_text(p, campaign)
        out.with_suffix(".txt").write_text(f"{p['title']}\n\n{text}\n")
        results.append({"file": out.name, "source": src.name, "title": p["title"],
                        "caption": text, "start": p["start"], "end": p["end"],
                        "reason": p.get("reason", ""), "status": "new", "notes": "", "payout": 0})
    return results
