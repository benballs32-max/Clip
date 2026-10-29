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
                "score": {"type": "integer", "description": "1-10 fit and performance"},
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


WINDOW = 480   # seconds of transcript per Claude request
OVERLAP = 45   # so a moment straddling two sections isn't lost
PER_WINDOW = 3
MIN_SCORE = 5


def _windows(segments: list[dict]) -> list[list[dict]]:
    total, out, t = segments[-1]["end"], [], 0.0
    while t < total:
        out.append([s for s in segments if t <= s["start"] < t + WINDOW + OVERLAP])
        t += WINDOW
    return [w for w in out if w]


def _ask(client, window: list[dict], campaign: dict, per: int, min_s: int, max_s: int, total: float) -> list[dict]:
    a, b = window[0]["start"], window[-1]["end"]
    where = f"This is minutes {a / 60:.0f}-{b / 60:.0f} of a {total / 60:.0f}-minute video. " if total > WINDOW * 1.5 else ""
    prompt = (
        f"You find the best short-form clips (TikTok/Reels/Shorts) in a transcript.\n"
        f"Campaign: {campaign.get('name')}\nRules/brief: {campaign.get('rules') or 'none'}\n\n"
        f"{where}Pick up to {per} non-overlapping moments from this section, each {min_s}-{max_s} "
        f"seconds long. Each must start on a strong hook, be a complete thought that makes sense "
        f"standalone, and not end mid-sentence. Prefer surprising, funny, emotional or high-value "
        f"moments. Never invent quotes. Score each 1-10 for how well it fits the brief and how well "
        f"it would perform; skip anything below 5. Write a short punchy title and a one-line caption "
        f"(no hashtags). The caption must follow any caption rules in the brief, including required "
        f"@mentions, and must not be clickbait or misleading. Respect every content rule in the brief "
        f"(topics, banned segments such as ad reads or sponsors).\n\n"
        f"Transcript (seconds):\n{_fmt(window)}"
    )
    resp = client.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=3000, tools=[TOOL],
        tool_choice={"type": "tool", "name": "submit_clips"},
        messages=[{"role": "user", "content": prompt}])
    return next((b.input.get("clips", []) for b in resp.content if b.type == "tool_use"), [])


def pick(segments: list[dict], campaign: dict, count: int = 8, progress=None) -> list[dict]:
    say = progress or (lambda *a: None)
    min_s, max_s = campaign.get("min_seconds", 15), campaign.get("max_seconds", 60)
    if not config.ANTHROPIC_API_KEY:
        picks = _fallback(segments, count, min_s, max_s)
    else:
        from concurrent.futures import ThreadPoolExecutor

        import anthropic
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        wins = _windows(segments)
        per = PER_WINDOW if len(wins) > 1 else count
        total = segments[-1]["end"]
        say(f"Claude is reading {len(wins)} section(s) of the transcript…")
        with ThreadPoolExecutor(3) as ex:
            results = list(ex.map(lambda w: _ask(client, w, campaign, per, min_s, max_s, total), wins))
        picks = [p for r in results for p in r if p.get("score", 10) >= MIN_SCORE]
        say(f"{len(picks)} candidate moments scored; keeping the best {count}.")

    # snap to word boundaries, then greedily keep the best-scoring non-overlapping ones
    cands = []
    for p in picks:
        s, e = _snap(segments, p["start"], p["end"])
        if e - s > max_s:
            e = s + max_s
        if e - s >= min_s * 0.7:
            cands.append({**p, "start": round(s, 2), "end": round(e, 2)})
    clips = []
    for c in sorted(cands, key=lambda c: -c.get("score", 5)):
        if len(clips) >= count:
            break
        if all(c["end"] <= k["start"] or c["start"] >= k["end"] for k in clips):
            clips.append(c)
    return sorted(clips, key=lambda c: c["start"])
