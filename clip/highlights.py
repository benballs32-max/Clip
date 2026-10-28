"""Pick clip-worthy moments from a transcript using Claude (or a plain fallback)."""
from . import config

TOOL = {
    "name": "submit_clips",
    "description": "Submit the chosen clips.",
    "input_schema": {
        "type": "object",
        "properties": {"clips": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "start": {"type": "number", "description": "Start time in seconds"},
                "end": {"type": "number", "description": "End time in seconds"},
                "title": {"type": "string"},
                "caption": {"type": "string", "description": "Post caption, no hashtags"},
                "reason": {"type": "string"},
            },
            "required": ["start", "end", "title", "caption"],
        }}},
        "required": ["clips"],
    },
}


def _fmt(segments: list[dict]) -> str:
    return "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in segments)


def _all_words(segments):
    return [w for s in segments for w in s["words"]]


def _snap(segments, start, end):
    """Snap to the nearest word boundaries so cuts never land mid-word."""
    words = _all_words(segments)
    if not words:
        return start, end
    s = min(words, key=lambda w: abs(w["start"] - start))["start"]
    e = min(words, key=lambda w: abs(w["end"] - end))["end"]
    return max(0.0, s - 0.1), e + 0.25


def _fallback(segments, count, min_s, max_s):
    """No API key: take evenly spaced windows. Crude, but keeps the pipeline usable."""
    if not segments:
        return []
    total = segments[-1]["end"]
    length = min((min_s + max_s) / 2, total)
    step = max(length, total / max(count, 1))
    out, t = [], 0.0
    while t + length <= total and len(out) < count:
        out.append({"start": t, "end": t + length, "title": f"Clip {len(out) + 1}",
                    "caption": "", "reason": "fallback window"})
        t += step
    return out


def pick(segments: list[dict], campaign: dict, count: int = 8) -> list[dict]:
    min_s, max_s = campaign.get("min_seconds", 15), campaign.get("max_seconds", 60)
    if not config.ANTHROPIC_API_KEY:
        picks = _fallback(segments, count, min_s, max_s)
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        prompt = (
            f"You find the best short-form clips (TikTok/Reels) in a transcript.\n"
            f"Campaign: {campaign.get('name')}\nRules/brief: {campaign.get('rules') or 'none'}\n\n"
            f"Pick up to {count} non-overlapping moments, each {min_s}-{max_s} seconds long. "
            f"Each must start on a strong hook and make sense standalone. Prefer surprising, funny, "
            f"emotional or high-value moments. Never invent quotes. Write a short punchy title and "
            f"a one-line caption (no hashtags).\n\nTranscript (seconds):\n{_fmt(segments)}"
        )
        resp = client.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=4000, tools=[TOOL],
            tool_choice={"type": "tool", "name": "submit_clips"},
            messages=[{"role": "user", "content": prompt}],
        )
        picks = next((b.input.get("clips", []) for b in resp.content if b.type == "tool_use"), [])

    clips, last_end = [], -1.0
    for p in sorted(picks, key=lambda c: c["start"]):
        s, e = _snap(segments, p["start"], p["end"])
        if e - s > max_s:
            e = s + max_s
        if e - s < min_s * 0.7 or s < last_end:
            continue
        clips.append({**p, "start": round(s, 2), "end": round(e, 2)})
        last_end = e
    return clips
