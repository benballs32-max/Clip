"""Repurpose: one long video -> a YouTube package plus per-platform copy for each short clip."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from . import config, llm

PLATFORMS = ("tiktok", "instagram", "shorts")
LABEL = {"tiktok": "TikTok", "instagram": "Instagram Reels", "shorts": "YouTube Shorts"}
CHUNK = 60000  # transcript characters sent to the model for the long-form package

YT_TOOL = {
    "name": "submit_package",
    "description": "Submit the YouTube publishing package for the full video.",
    "input_schema": {"type": "object", "properties": {
        "titles": {"type": "array", "items": {"type": "string"}, "description": "5 title options, max 70 characters each"},
        "description": {"type": "string", "description": "Description: hook line, summary, then a call to action. No hashtags."},
        "chapters": {"type": "array", "items": {"type": "object", "properties": {
            "start": {"type": "number", "description": "Start in seconds, taken from the transcript"},
            "title": {"type": "string"}}, "required": ["start", "title"]}},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "10-15 search tags, no #"},
        "thumbnail_texts": {"type": "array", "items": {"type": "string"}, "description": "4 options, max 4 words each"},
    }, "required": ["titles", "description", "chapters", "tags", "thumbnail_texts"]},
}

COPY_TOOL = {
    "name": "submit_copy",
    "description": "Submit platform-specific copy for each clip.",
    "input_schema": {"type": "object", "properties": {"clips": {"type": "array", "items": {
        "type": "object", "properties": {
            "index": {"type": "integer"},
            "tiktok": {"type": "object", "properties": {"caption": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}}, "required": ["caption", "hashtags"]},
            "instagram": {"type": "object", "properties": {"caption": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}}, "required": ["caption", "hashtags"]},
            "shorts": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}}, "required": ["title", "description"]},
        }, "required": ["index", "tiktok", "instagram", "shorts"]}}}, "required": ["clips"]},
}


def _transcript(segments: list[dict]) -> str:
    text = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in segments)
    if len(text) <= CHUNK:
        return text
    head = text[: CHUNK // 2]  # very long videos: keep the start and end, thin the middle
    lines = text[CHUNK // 2:].splitlines()
    step = max(1, len(lines) // 400)
    return head + "\n…\n" + "\n".join(lines[::step])[: CHUNK // 2]


def _context(campaign: dict) -> str:
    bits = [f"Channel/project: {campaign.get('name')}"]
    if campaign.get("audience"):
        bits.append(f"Audience: {campaign['audience']}")
    if campaign.get("tone"):
        bits.append(f"Tone of voice: {campaign['tone']}")
    if campaign.get("rules"):
        bits.append(f"Extra instructions: {campaign['rules']}")
    return "\n".join(bits)


def youtube_package(segments: list[dict], title: str, duration: float, campaign: dict, progress=print) -> dict:
    """Titles, description, chapters, tags and thumbnail text for the full video."""
    if not config.ANTHROPIC_API_KEY or not segments:
        return {}
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    progress("Writing the YouTube titles, description and chapters…")
    prompt = (
        f"You are a YouTube strategist packaging a long-form video for publishing.\n{_context(campaign)}\n"
        f"Working title: {title}\nLength: {duration / 60:.0f} minutes.\n\n"
        "Write titles that are specific, honest and curiosity-driven (no clickbait the video does not deliver on). "
        "Chapters must start at times that appear in the transcript, begin at 0, be at least 10 seconds apart, "
        "and have short descriptive names; use 4-12 chapters depending on length. "
        "Never invent claims, guests or numbers that are not in the transcript.\n\n"
        f"Transcript ([seconds] text):\n{_transcript(segments)}")
    data, _ = llm.call_tool(client, config.REFINE_MODEL if config.REFINE_MODEL.lower() not in ("", "none", "off")
                            else config.CLAUDE_MODEL, YT_TOOL, prompt, 4000)
    chapters = sorted((c for c in data.get("chapters", []) if isinstance(c.get("start"), (int, float))),
                      key=lambda c: c["start"])
    if chapters and chapters[0]["start"] > 0:  # YouTube needs the first chapter at 0:00
        chapters[0]["start"] = 0
    data["chapters"] = chapters
    return data


def platform_copy(clips: list[dict], campaign: dict, progress=print) -> dict:
    """{clip file: {tiktok:{caption,hashtags}, instagram:{...}, shorts:{title,description}}}."""
    if not config.ANTHROPIC_API_KEY or not clips:
        return {}
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    progress(f"Writing TikTok, Reels and Shorts copy for {len(clips)} clips…")
    listing = "\n".join(f"{i}. \"{c['title']}\" - {c.get('caption', '')} (why: {c.get('reason', '')})"
                        for i, c in enumerate(clips))
    prompt = (
        f"You write social copy for short clips cut from a longer video.\n{_context(campaign)}\n\n"
        "For each clip write copy for three platforms, each with its own native voice (not the same text three times):\n"
        "- TikTok: casual, a hook in the first line, under 150 characters, 3-5 hashtags mixing broad and niche.\n"
        "- Instagram Reels: a little more polished, can run 2 short lines, 5-8 hashtags.\n"
        "- YouTube Shorts: a searchable title under 70 characters and a one or two line description with 2-3 hashtags inside it.\n"
        "Hashtags are given without the # sign. Do not claim anything the clip does not show.\n\n"
        f"Clips:\n{listing}")
    data, _ = llm.call_tool(client, config.CLAUDE_MODEL, COPY_TOOL, prompt, 6000)
    out = {}
    for row in data.get("clips", []):
        i = row.get("index")
        if isinstance(i, int) and 0 <= i < len(clips):
            copy = {k: row[k] for k in PLATFORMS if k in row}
            out[clips[i]["file"]] = {**copy, "text": {p: clip_text(p, copy) for p in copy}}
    return out


def _hashtags(tags: list[str]) -> str:
    return " ".join("#" + re.sub(r"\W+", "", t.lstrip("#")) for t in tags if t.strip())


def ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def chapters_text(chapters: list[dict]) -> str:
    return "\n".join(f"{ts(c['start'])} {c['title']}" for c in chapters)


def youtube_description(pkg: dict) -> str:
    """Description with the chapter list pasted in, ready for the YouTube box."""
    parts = [pkg.get("description", "").strip(), chapters_text(pkg.get("chapters", []))]
    return "\n\n".join(p for p in parts if p)


def clip_text(platform: str, copy: dict) -> str:
    """The paste-ready text for one platform."""
    c = copy.get(platform) or {}
    if platform == "shorts":
        return "\n\n".join(x for x in (c.get("title", ""), c.get("description", "")) if x)
    return "\n\n".join(x for x in (c.get("caption", ""), _hashtags(c.get("hashtags", []))) if x)


def build_zip(item: dict, clips_dir: Path, out) -> None:
    """Everything for one video: youtube/ text, clips/ videos, and a folder of copy per platform."""
    pkg, copy = item.get("package") or {}, (item.get("package") or {}).get("clips", {})
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        if pkg.get("titles"):
            z.writestr("youtube/titles.txt", "\n".join(pkg["titles"]) + "\n\nThumbnail text options:\n"
                       + "\n".join(pkg.get("thumbnail_texts", [])) + "\n")
            z.writestr("youtube/description.txt", youtube_description(pkg) + "\n")
            z.writestr("youtube/tags.txt", ", ".join(pkg.get("tags", [])) + "\n")
        for t in item.get("thumbnails", []):
            f = clips_dir / t["file"]
            if f.exists():
                z.write(f, f"youtube/thumbnails/{t['file']}")
        for k in item.get("clips", []):
            f = clips_dir / k["file"]
            if not f.exists() or k.get("status") == "rejected":
                continue
            stem = Path(k["file"]).stem
            z.write(f, f"clips/{stem}.mp4")  # one copy of the video; each platform folder has its own text
            for p in PLATFORMS:
                text = clip_text(p, copy.get(k["file"], {}))
                if text:
                    z.writestr(f"{p}/{stem}.txt", text + "\n")
