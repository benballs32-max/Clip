"""Pre-submission checks: turn a brief into a checklist, then judge each clip against it.

A clip is judged on its caption, its transcript and a handful of frames from the rendered video,
so rules like "the speaker's face is on screen in the first 5 seconds" can be checked too."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from . import config, edits, llm, render, store

KINDS = ("caption", "visual", "speech", "length", "manual")
STD = [  # always judged, whatever the brief says
    ("std_start", "speech", "The clip opens on a clean start: a complete sentence or a strong hook, not mid-thought."),
    ("std_end", "speech", "The clip ends after the point has landed, on a finished sentence."),
    ("std_caption", "caption", "The caption reads naturally, matches what is said, and is not misleading or clickbait."),
    ("std_subs", "visual", "The burned-in subtitles are readable and not covered by anything important."),
    ("std_brands", "visual", "No other brands, logos, sponsor graphics or watermarks from other creators are visible."),
]

CHECKS_TOOL = {
    "name": "set_checks",
    "description": "The checklist for this campaign.",
    "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {
            "id": {"type": "string", "description": "short snake_case id"},
            "text": {"type": "string", "description": "one concrete, checkable requirement"},
            "kind": {"type": "string", "enum": list(KINDS)}},
        "required": ["id", "text", "kind"]}}}, "required": ["items"]},
}

QA_TOOL = {
    "name": "report_qa",
    "description": "The result for every check.",
    "input_schema": {"type": "object", "properties": {
        "results": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"},
            "status": {"type": "string", "enum": ["pass", "warn", "fail", "unsure"]},
            "detail": {"type": "string", "description": "at most 20 words: what you saw or read"}},
            "required": ["id", "status", "detail"]}},
        "summary": {"type": "string"}}, "required": ["results"]},
}


def _model() -> str:
    return config.REFINE_MODEL if config.REFINE_MODEL.lower() not in ("", "none", "off") else config.CLAUDE_MODEL


def _client():
    if not config.ANTHROPIC_API_KEY:
        raise ValueError("Add your Anthropic API key to .env to use the AI checks.")
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def generate_checks(campaign: dict) -> dict:
    """Read the campaign rules and return {"items": [...], "hash": ...}."""
    rules = (campaign.get("rules") or "").strip()
    if not rules:
        raise ValueError("Add the brief or rules first; the checklist is built from them.")
    prompt = (
        "Turn this clipping campaign's rules into a checklist a reviewer can tick off for each finished clip.\n"
        "Each item is ONE concrete requirement written as a statement that is true when the clip complies "
        "(e.g. 'The caption tags @a and @b'). Use these kinds:\n"
        "- caption: checkable from the caption text (required tags, mentions, tone, no clickbait)\n"
        "- visual: checkable from video frames (the speaker's face in the first 5 seconds, no other brands in frame)\n"
        "- speech: checkable from the transcript (no profanity, no sponsor or ad reads, complete thought, quotes not altered)\n"
        "- length: clip duration limits\n"
        "- manual: about posting or the account, which cannot be seen in the clip (likes visible, how long to keep the post up, "
        "which platforms, engagement targets, no paid promotion)\n"
        "Skip payout, budget and contact details. 6 to 14 items, no duplicates.\n\n"
        f"Platform: {campaign.get('platform')}\n<rules>\n{rules}\n</rules>")
    data, _ = llm.call_tool(_client(), _model(), CHECKS_TOOL, prompt, 2000)
    items, seen = [], set()
    for it in data.get("items", [])[:20]:
        i = re.sub(r"[^a-z0-9_]", "", str(it.get("id", "")).lower().replace(" ", "_"))[:32] or f"check_{len(items) + 1}"
        while i in seen:
            i += "_"
        seen.add(i)
        kind = it.get("kind") if it.get("kind") in KINDS else "speech"
        text = re.sub(r"\s+", " ", str(it.get("text", ""))).strip()[:220]
        if text:
            items.append({"id": i, "text": text, "kind": kind, "enabled": True})
    if not items:
        raise ValueError("Couldn't build a checklist from those rules.")
    return {"items": items, "hash": store.rules_hash(rules)}


def clean_checks(raw: dict, campaign: dict) -> dict:
    items, seen = [], set()
    for it in (raw.get("items") or [])[:30]:
        text = re.sub(r"\s+", " ", str(it.get("text", ""))).strip()[:220]
        if not text:
            continue
        i = re.sub(r"[^a-z0-9_]", "", str(it.get("id", "")).lower())[:32] or f"custom_{len(items) + 1}"
        while i in seen:
            i += "_"
        seen.add(i)
        items.append({"id": i, "text": text, "kind": it.get("kind") if it.get("kind") in KINDS else "speech",
                      "enabled": bool(it.get("enabled", True))})
    return {"items": items, "hash": raw.get("hash") or campaign["checks"].get("hash", "")}


# ---------------------------------------------------------------- per clip

def _probe_duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def frames(path: Path, duration: float) -> list[tuple[str, bytes]]:
    times = sorted({round(t, 1) for t in (0.4, 1.5, 3.0, 4.6, duration / 2, duration - 0.6) if 0 <= t < duration - 0.05})
    out = []
    for t in times:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=270:-2", "-q:v", "6", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                           capture_output=True)
        if r.stdout:
            out.append((f"Frame at {t:.1f}s of {duration:.0f}s:", r.stdout))
    return out


def _transcript(cid: str, item: dict, clip: dict) -> str:
    """What is said, in output time, as '[0-5s] ...' lines."""
    try:
        words = edits.load_words(cid, item)
    except Exception:
        return "(no transcript available)"
    segs = [(s["start"], s["end"]) for s in (clip.get("edit") or {}).get("segments", [])] or [(clip["start"], clip["end"])]
    mapped = render.map_words(words, segs)
    if not mapped:
        return "(no speech detected)"
    lines, bucket, cur = [], 0, []
    for w in mapped:
        b = int(w["start"] // 5)
        if b != bucket and cur:
            lines.append(f"[{bucket * 5}-{bucket * 5 + 5}s] " + " ".join(cur))
            cur = []
        bucket = b
        cur.append(w["word"])
    if cur:
        lines.append(f"[{bucket * 5}-{bucket * 5 + 5}s] " + " ".join(cur))
    return "\n".join(lines)


def run_qa(cid: str, xid: str, file: str) -> dict:
    campaign, item = store.get(cid), store.get_content(cid, xid)
    clip = edits.clip_of(item, file)
    path = store.cdir(cid) / "clips" / Path(file).name
    if not path.exists():
        raise ValueError("The clip file is missing.")
    dur = _probe_duration(path)
    caption = clip.get("caption", "")
    results: list[dict] = []

    # --- exact checks that need no AI
    mn, mx = campaign["min_seconds"], campaign["max_seconds"]
    results.append({"id": "length", "label": f"Length is {mn}-{mx} seconds", "kind": "length",
                    "status": "pass" if mn - 0.5 <= dur <= mx + 0.5 else "fail", "detail": f"{dur:.0f} seconds"})
    low = caption.lower()
    need = [t for t in [campaign["disclosure"], *campaign["hashtags"]] if t]
    missing = [t for t in need if t.lower() not in low]
    results.append({"id": "tags", "label": "Caption has the required hashtags and disclosure", "kind": "caption",
                    "status": "fail" if missing else "pass", "detail": ("Missing " + ", ".join(missing)) if missing else "All present"})

    # --- AI judgement of the brief's checks plus the standard ones
    brief_items = [c for c in campaign["checks"]["items"] if c["enabled"] and c["kind"] not in ("manual", "length")]
    asks = [(i, k, t) for i, k, t in STD] + [(c["id"], c["kind"], c["text"]) for c in brief_items]
    label = {i: t for i, _, t in asks}
    kind = {i: k for i, k, _ in asks}
    tags = (clip.get("edit") or {}).get("tags") or clip.get("tags") or []
    style = ""
    if "loop" in tags:
        style = ("EDIT STYLE: seamless loop. By design the clip opens with the final words of its last sentence and ends on "
                 "the lead-in, so a replay flows back into the opening. Do not fail the start or end checks for that; "
                 "judge instead whether it plays smoothly and the words are unaltered.\n\n")
    elif "hook" in tags:
        style = ("EDIT STYLE: punchline first. By design the clip opens with a key line that is repeated later in the clip. "
                 "Do not penalise that repetition; judge whether the opening line is a strong, complete, verbatim sentence.\n\n")
    prompt = (
        "You are a strict but fair reviewer checking a finished short-form clip before it is submitted to a paid "
        f"clipping campaign. Platform: {campaign.get('platform')}.\n\n" + style +
        f"Caption (as it will be posted):\n{caption or '(empty)'}\n\n"
        f"What is said (output time):\n{_transcript(cid, item, clip)}\n\n"
        "The frames below come from the finished video, burned-in subtitles included. Judge EVERY check by its id:\n"
        + "\n".join(f"- {i} [{k}]: {t}" for i, k, t in asks) +
        "\n\nUse pass when clearly satisfied, fail when clearly violated, warn when borderline or likely to be "
        "questioned, unsure when the evidence is not visible or audible in what you were given. Never guess: if a "
        "face is not clearly visible in the frames, say so. Keep each detail to at most 20 words.")
    data, usage = llm.call_tool(_client(), _model(), QA_TOOL, prompt, 2500, images=frames(path, dur))
    got = {r.get("id"): r for r in data.get("results", [])}
    for i, _, _ in asks:
        r = got.get(i) or {}
        status = r.get("status") if r.get("status") in ("pass", "warn", "fail", "unsure") else "unsure"
        results.append({"id": i, "label": label[i], "kind": kind[i], "status": status,
                        "detail": re.sub(r"\s+", " ", str(r.get("detail") or "Not assessed"))[:160]})
    states = {r["status"] for r in results}
    verdict = "fail" if "fail" in states else "warn" if states & {"warn", "unsure"} else "pass"
    qa = {"at": time.time(), "verdict": verdict, "items": results, "summary": str(data.get("summary", ""))[:240],
          "tokens": f"{usage.input_tokens / 1000:.1f}k in / {usage.output_tokens / 1000:.1f}k out"}
    store.set_clip_field(cid, xid, file, "qa", qa)
    return qa
