"""Pick clip-worthy moments from a transcript using Claude (or a plain fallback)."""
import threading

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


class _Usage:
    """Token counts per model, for the activity log."""

    def __init__(self):
        self.t, self.lock = {}, threading.Lock()

    def add(self, model, u):
        with self.lock:
            d = self.t.setdefault(model, [0, 0])
            d[0] += u.input_tokens
            d[1] += u.output_tokens

    def summary(self) -> str:
        return "Tokens used: " + "; ".join(f"{m} {i / 1000:.1f}k in / {o / 1000:.1f}k out"
                                           for m, (i, o) in self.t.items())


def _trim(segments, start, end, min_s, max_s):
    """Over-long pick: end on the last phrase boundary that fits, never mid-phrase."""
    ends = [s["end"] for s in segments if start + min_s <= s["end"] <= start + max_s]
    return (start, max(ends)) if ends else None


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


def _ask(client, usage, window: list[dict], campaign: dict, per: int, min_s: int, max_s: int, total: float) -> list[dict]:
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
    usage.add(config.CLAUDE_MODEL, resp.usage)
    return next((b.input.get("clips", []) for b in resp.content if b.type == "tool_use"), [])


REFINE_TOOL = {
    "name": "submit_final",
    "description": "Submit the final clip cuts.",
    "input_schema": {"type": "object", "properties": {"clips": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "candidate": {"type": "integer"},
            "start_line": {"type": "integer", "description": "Index of the first transcript line of the clip"},
            "end_line": {"type": "integer", "description": "Index of the last transcript line (inclusive)"},
            "title": {"type": "string"},
            "caption": {"type": "string", "description": "Post caption, no hashtags"},
            "score": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["candidate", "start_line", "end_line", "title", "caption", "score"],
    }}}, "required": ["clips"]},
}


def _refine(client, usage, segments, cands, campaign, count, min_s, max_s):
    """Second pass (stronger model): choose exact start/end lines so each clip opens on its hook and
    ends only once the point has landed, on a complete sentence."""
    blocks = []
    for n, c in enumerate(cands):
        lo, hi = c["start"] - 25, c["end"] + 40
        lines = "\n".join(f"{i}| [{s['start']:.1f}-{s['end']:.1f}] {s['text']}"
                          for i, s in enumerate(segments) if s["end"] > lo and s["start"] < hi)
        blocks.append(f"## Candidate {n}: \"{c['title']}\" (rough {c['start']:.0f}-{c['end']:.0f}s)\n{lines}")
    prompt = (
        f"You are the final editor cutting short-form clips from a long video.\n"
        f"Campaign: {campaign.get('name')}\nRules/brief: {campaign.get('rules') or 'none'}\n\n"
        f"Below are candidate moments, each with surrounding transcript lines (index| [start-end] text). "
        f"For each good one choose start_line and end_line so that the clip:\n"
        f"- OPENS on the hook: the first line must stand alone, never a fragment, never a reply to something "
        f"unseen ('and so', 'that's why', 'he said').\n"
        f"- ENDS only after the point has landed: the payoff, punchline, conclusion or lesson is fully "
        f"delivered and the last line completes its sentence. Never stop before the payoff or leave the "
        f"next idea half-started. If the payoff comes after the rough end, extend; if it comes earlier, end there.\n"
        f"- Lasts {min_s}-{max_s} seconds (end of end_line minus start of start_line). If the idea can't "
        f"land inside {max_s}s, skip it.\n"
        f"Drop weak or rule-breaking candidates and avoid near-duplicates. Return at most {count} clips, "
        f"best first. Write the title and a one-line caption (no hashtags) that follow the brief's caption "
        f"rules, including required @mentions, with no clickbait. Score 1-10.\n\n" + "\n\n".join(blocks))
    resp = client.messages.create(
        model=config.REFINE_MODEL, max_tokens=4000, tools=[REFINE_TOOL],
        tool_choice={"type": "tool", "name": "submit_final"},
        messages=[{"role": "user", "content": prompt}])
    usage.add(config.REFINE_MODEL, resp.usage)
    raw = next((b.input.get("clips", []) for b in resp.content if b.type == "tool_use"), [])
    out = []
    for r in raw:
        a, b = r.get("start_line"), r.get("end_line")
        if not (isinstance(a, int) and isinstance(b, int) and 0 <= a <= b < len(segments)):
            continue
        while (segments[b]["text"].rstrip()[-1:] not in '.?!…"\'”)' and b + 1 < len(segments)
               and b - a < 40 and segments[b + 1]["end"] - segments[a]["start"] <= max_s):
            b += 1  # safety net: don't stop mid-sentence if there's room to finish it
        if not segments[a]["words"] or not segments[b]["words"]:
            continue
        s, e = max(0.0, segments[a]["words"][0]["start"] - 0.1), segments[b]["words"][-1]["end"] + 0.4
        if not (min_s * 0.8 <= e - s <= max_s + 1):
            continue
        out.append({**r, "start": round(s, 2), "end": round(e, 2)})
    return out


def pick(segments: list[dict], campaign: dict, count: int = 8, progress=None) -> list[dict]:
    say = progress or (lambda *a: None)
    min_s, max_s = campaign.get("min_seconds", 15), campaign.get("max_seconds", 60)
    if not config.ANTHROPIC_API_KEY:
        picks = _fallback(segments, count, min_s, max_s)
        refine = False
    else:
        from concurrent.futures import ThreadPoolExecutor

        import anthropic
        client, usage = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY), _Usage()
        refine = config.REFINE_MODEL.lower() not in ("", "none", "off")
        wins = _windows(segments)
        per = PER_WINDOW if len(wins) > 1 else count
        total = segments[-1]["end"]
        say(f"{config.CLAUDE_MODEL} is scanning {len(wins)} section(s) of the transcript…")
        with ThreadPoolExecutor(3) as ex:
            results = list(ex.map(lambda w: _ask(client, usage, w, campaign, per, min_s, max_s, total), wins))
        picks = [p for r in results for p in r if p.get("score", 10) >= MIN_SCORE]
        say(f"{len(picks)} candidate moments found.")

    # first pass: snap to word boundaries; over-long picks end on a phrase boundary, never mid-phrase
    cands = []
    for p in picks:
        s_, e_ = _snap(segments, p["start"], p["end"])
        if e_ - s_ > max_s:
            t = _trim(segments, s_, e_, min_s, max_s)
            if not t:
                continue
            s_, e_ = t
        if e_ - s_ >= min_s * 0.7:
            cands.append({**p, "start": round(s_, 2), "end": round(e_, 2)})

    def best(items, n):
        keep = []
        for c in sorted(items, key=lambda c: -c.get("score", 5)):
            if len(keep) >= n:
                break
            if all(c["end"] <= k["start"] or c["start"] >= k["end"] for k in keep):
                keep.append(c)
        return sorted(keep, key=lambda c: c["start"])

    if refine and cands:
        pool = best(cands, count * 2)
        say(f"{config.REFINE_MODEL} is choosing exact start/end points for {len(pool)} candidates…")
        try:
            final = _refine(client, usage, segments, pool, campaign, count, min_s, max_s)
            say(usage.summary())
            if final:
                return best(final, count)
            say("Refinement returned nothing usable; using first-pass picks.")
        except Exception as e:
            say(f"Refinement failed ({str(e)[:90]}); using first-pass picks.")
    if config.ANTHROPIC_API_KEY:
        say(usage.summary())
    return best(cands, count)
