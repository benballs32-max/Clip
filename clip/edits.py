"""Editing: ordered segments + text overlays, and AI helpers for the three popular edit styles.

An edit decision list (EDL) is plain JSON:
  {"segments": [{"start": 355.9, "end": 384.9}, ...],   # source seconds, in playback order
   "overlays": [{"text": "...", "start": 0, "dur": 3, "pos": "top"}],   # output seconds
   "captions": true, "tags": ["hook", "keyword", "loop"]}
Words are never altered; the AI only chooses where to cut and what order to play pieces in."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import assets, config, highlights, llm, render, store, transcribe, youtube

PAD = 20.0               # seconds of source kept either side of a clip for the editor to reach into
MAX_TOTAL = 180.0
TAGS = ("hook", "keyword", "loop")
TAG_LABEL = {"hook": "Punchline first", "keyword": "Keyword", "loop": "Loop"}


# ---------------------------------------------------------------- source, words, ranges

def source_path(cid: str, item: dict) -> Path:
    root = store.cdir(cid).resolve()
    p = (root / item["file"]).resolve()
    if root not in p.parents:
        raise ValueError("Bad source path")
    return p


def open_source(cid: str, item: dict):
    """(src, has_audio): a local Path, or a fresh streamed-YouTube spec."""
    if item["kind"] == "youtube":
        _, url = youtube.normalize(item["url"])
        return youtube.stream_spec(url), True
    p = source_path(cid, item)
    return p, assets.has_audio(p)


def load_words(cid: str, item: dict) -> list[dict]:
    """Every transcribed word of the source video, in source time ([] if it has no speech)."""
    if item["kind"] == "youtube":
        vid, url = youtube.normalize(item["url"])
        segs = youtube.transcript(url, vid, store.cdir(cid) / "_work")
    else:
        cache = source_path(cid, item).with_suffix(".transcript.json")
        segs = json.loads(cache.read_text()) if cache.exists() else []
    return [w for s in (segs or []) for w in s["words"]]


def clip_of(item: dict, file: str) -> dict:
    for c in item["clips"]:
        if c["file"] == file:
            return c
    raise KeyError(file)


def editor_range(item: dict, clip: dict) -> tuple[float, float]:
    segs = (clip.get("edit") or {}).get("segments") or [{"start": clip["start"], "end": clip["end"]}]
    a, b = min(s["start"] for s in segs), max(s["end"] for s in segs)
    dur = float(item.get("duration") or 0) or b + PAD
    return max(0.0, a - PAD), min(dur, b + PAD)


def proxy_dir(cid: str, clip: dict) -> Path:
    return store.cdir(cid) / "_work" / "edit" / re.sub(r"[^\w\-]", "_", Path(clip["file"]).stem)


def proxy_ready(cid: str, clip: dict, lo: float, hi: float) -> bool:
    d = proxy_dir(cid, clip)
    try:
        m = json.loads((d / "proxy.json").read_text())
    except (OSError, ValueError):
        return False
    return (d / "proxy.mp4").exists() and abs(m["lo"] - lo) < 0.01 and abs(m["hi"] - hi) < 0.01


def ensure_proxy(cid: str, item: dict, clip: dict, say=None, pct_cb=None) -> tuple[float, float]:
    lo, hi = editor_range(item, clip)
    if not proxy_ready(cid, clip, lo, hi):
        (say or (lambda *a: None))(f"Building a preview copy of {int(hi - lo)}s of the video…")
        src, _ = open_source(cid, item)
        d = proxy_dir(cid, clip)
        render.build_proxy(src, lo, hi, d / "proxy.mp4", pct_cb)
        (d / "proxy.json").write_text(json.dumps({"lo": lo, "hi": hi}))
    return lo, hi


def editor_info(cid: str, item: dict, clip: dict, campaign: dict) -> dict:
    lo, hi = editor_range(item, clip)
    words = [w for w in load_words(cid, item) if w["end"] >= lo and w["start"] <= hi]
    base = (clip.get("edit") or {"segments": [{"start": clip["start"], "end": clip["end"]}],
                                   "overlays": [], "captions": True, "tags": []})
    return {
        "lo": lo, "hi": hi, "ready": proxy_ready(cid, clip, lo, hi),
        "proxy": f"/editorfile/{cid}/{proxy_dir(cid, clip).name}/proxy.mp4",
        "words": [{"word": w["word"], "start": round(w["start"], 3), "end": round(w["end"], 3)} for w in words],
        "edl": clip.get("draft") or base, "original": {"segments": [{"start": clip["start"], "end": clip["end"]}],
                                                        "overlays": [], "captions": True, "tags": []},
        "clip": {"file": clip["file"], "title": clip["title"], "caption": clip.get("caption", ""),
                 "start": clip["start"], "end": clip["end"]},
        "settings": {k: campaign[k] for k in ("frame_mode", "logo_position", "logo_scale", "music_volume")},
    }


# ---------------------------------------------------------------- validation

def clean_edl(edl: dict, lo: float, hi: float) -> dict:
    segs = []
    for s in (edl.get("segments") or [])[:12]:
        a = min(max(float(s["start"]), lo), hi)
        b = min(max(float(s["end"]), lo), hi)
        if b - a >= 0.3:
            segs.append({"start": round(a, 2), "end": round(b, 2)})
    if not segs:
        raise ValueError("Nothing to render: every segment is empty or outside the available video.")
    total = sum(s["end"] - s["start"] for s in segs)
    if total > MAX_TOTAL:
        raise ValueError(f"That's {int(total)}s long; the limit is {int(MAX_TOTAL)}s.")
    overlays = []
    for o in (edl.get("overlays") or [])[:6]:
        text = re.sub(r"\s+", " ", str(o.get("text", ""))).strip()[:80]
        if text:
            overlays.append({"text": text, "start": round(max(0.0, float(o.get("start", 0))), 2),
                             "dur": round(min(max(float(o.get("dur", 3)), 0.3), 30.0), 2),
                             "pos": o.get("pos") if o.get("pos") in render.TEXT_POS else "top"})
    return {"segments": segs, "overlays": overlays, "captions": bool(edl.get("captions", True)),
            "tags": [t for t in (edl.get("tags") or []) if t in TAGS]}


def total_len(edl: dict) -> float:
    return sum(s["end"] - s["start"] for s in edl["segments"])


# ---------------------------------------------------------------- AI proposals

def _sentences(words: list[dict]) -> list[tuple[int, int]]:
    out, i0 = [], 0
    for i, w in enumerate(words):
        if highlights._ends(w):
            out.append((i0, i))
            i0 = i + 1
    if i0 < len(words):
        out.append((i0, len(words) - 1))
    return out


def _pad_span(words: list[dict], i0: int, i1: int) -> tuple[float, float]:
    """Time span of words i0..i1, padded only into silence (never into a neighbouring word)."""
    a, b = words[i0]["start"], words[i1]["end"]
    prev = words[i0 - 1]["end"] if i0 > 0 else 0.0
    nxt = words[i1 + 1]["start"] if i1 + 1 < len(words) else b + 1.0
    return max(0.0, a - min(0.1, max(0.0, a - prev - 0.03))), b + min(0.4, max(0.05, nxt - b - 0.06))


PROPOSE_TOOL = {
    "name": "propose_edit",
    "description": "Pick the lines and keyword for the requested edit styles.",
    "input_schema": {"type": "object", "properties": {
        "hook_sentence": {"type": "integer", "description": "Index of the sentence to play first (punchline first)"},
        "keyword": {"type": "string", "description": "On-screen keyword, at most 4 words"},
        "tail_start_word": {"type": "integer", "description": "Index of the first word of the loop tail"},
        "why": {"type": "string"}}, "required": ["why"]},
}


def _ask_ai(client, usage_note: list, campaign: dict, clip: dict, bw: list[dict], sents, modes: set) -> dict:
    lines = "\n".join(f"S{n}| [{bw[a]['start']:.1f}-{bw[b]['end']:.1f}] " + " ".join(w["word"] for w in bw[a:b + 1])
                      for n, (a, b) in enumerate(sents))
    asks = []
    if "hook" in modes:
        asks.append(
            "PUNCHLINE FIRST: choose hook_sentence, the index of ONE sentence (verbatim, unchanged) to play at the very "
            "start, then the clip plays from its beginning. It should state the main point or the most gripping line, "
            "stand alone, and be 2-7 seconds long. Prefer a later sentence over the first one.")
    if "loop" in modes:
        a, b = sents[-1]
        words = "\n".join(f"W{i}| {bw[i]['word']}" for i in range(a, b + 1))
        asks.append(
            "SEAMLESS LOOP: the clip's final sentence will be split. Choose tail_start_word, the index of the first "
            "word of a short tail (1.2-4 seconds) that finishes the thought. The tail is moved to the very start, "
            "the video plays through and ends on the lead-in, so when it replays the lead-in flows into the tail. Pick "
            "a split where the tail is a punchy standalone ending and the lead-in reads naturally into it. "
            f"Final sentence words:\n{words}")
    if "keyword" in modes:
        asks.append(
            "KEYWORD: choose keyword, a punchy on-screen title of at most 4 words that tells the viewer what the clip is "
            "about (a topic or a contrast, e.g. COURAGE > CONFIDENCE). It must not be misleading or clickbait. No hashtags or emojis.")
    prompt = (f"You are editing a short-form clip. Campaign: {campaign.get('name')}. Platform: {campaign.get('platform')}.\n"
              f"Rules/brief: {campaign.get('rules') or 'none'}\n\nClip transcript (seconds, sentence index first):\n{lines}\n\n"
              "Never change, add or remove words; you only choose where things go. Follow every content rule in the brief.\n\n"
              + "\n\n".join(asks))
    data, u = llm.call_tool(client, config.REFINE_MODEL if config.REFINE_MODEL.lower() not in ("", "none", "off")
                            else config.CLAUDE_MODEL, PROPOSE_TOOL, prompt, 1000)
    usage_note.append(f"{u.input_tokens / 1000:.1f}k in / {u.output_tokens / 1000:.1f}k out")
    return data


def propose(cid: str, item: dict, clip: dict, modes: list[str], campaign: dict, words=None) -> dict:
    """Return {"edl": ..., "notes": [...]} for a combination of hook | loop | keyword."""
    modes = {m for m in modes if m in TAGS}
    if {"hook", "loop"} <= modes:
        raise ValueError("Choose either Punchline first or Seamless loop, not both.")
    if not modes:
        raise ValueError("Pick at least one style.")
    if clip.get("parent"):  # styles always apply to the original moment, not an already-edited cut
        try:
            clip = clip_of(item, clip["parent"])
        except KeyError:
            pass
    lo, hi = editor_range(item, clip)
    words = words if words is not None else load_words(cid, item)
    cs, ce = clip["start"], clip["end"]
    notes: list[str] = []
    body = {"start": cs, "end": ce}
    edl = {"segments": [body], "overlays": [], "captions": True, "tags": []}
    # words inside the clip: judged by their midpoint, so the next sentence's first word (which can start
    # a few hundredths after the cut) is not mistaken for a tiny final sentence
    ids = [i for i, w in enumerate(words) if cs - 0.05 <= (w["start"] + w["end"]) / 2 <= ce]
    bw = [words[i] for i in ids]
    sents = _sentences(bw) if bw else []
    if not bw and modes & {"hook", "loop"}:
        modes -= {"hook", "loop"}
        notes.append("This clip has no speech, so Punchline first and Loop don't apply.")

    ai: dict = {}
    if config.ANTHROPIC_API_KEY and modes:
        try:
            import anthropic
            usage: list[str] = []
            ai = _ask_ai(anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY), usage, campaign, clip, bw, sents, modes)
            why = re.sub(r"\s+", " ", str(ai.get("why", ""))).strip()
            notes.append(f"Claude chose the lines ({usage[0]}). {why[:150]}{'…' if len(why) > 150 else ''}".strip())
        except Exception as e:
            notes.append(f"AI suggestion failed ({str(e)[:80]}); used a simple default.")
    elif modes:
        notes.append("No API key set, so a simple default was used.")

    if "hook" in modes and sents:
        n = ai.get("hook_sentence") if isinstance(ai.get("hook_sentence"), int) else None
        if n is None or not 0 <= n < len(sents):
            n = max(range(len(sents)), key=lambda k: -abs((bw[sents[k][1]]["end"] - bw[sents[k][0]]["start"]) - 4.0) + k * 0.01)
        a, b = sents[n]
        s, e = _pad_span(words, ids[a], ids[b])
        if e - s > 9.0:  # too long for a hook: keep its closing words
            keep = [i for i in range(a, b + 1) if bw[b]["end"] - bw[i]["start"] <= 6.5]
            if keep:
                s, e = _pad_span(words, ids[keep[0]], ids[b])
                notes.append("The chosen line was long, so only its ending is used as the hook.")
        edl["segments"] = [{"start": round(s, 2), "end": round(e, 2)}, body]
        edl["tags"].append("hook")
        notes.append(f"Hook: “{' '.join(w['word'] for w in bw[a:b + 1])[:90]}”")

    if "loop" in modes and sents:
        a, b = sents[-1]
        k = ai.get("tail_start_word") if isinstance(ai.get("tail_start_word"), int) else None
        ok = k is not None and a < k <= b and 1.0 <= bw[b]["end"] - bw[k]["start"] <= 5.0
        if not ok:  # default: a ~1.8s tail, leaving a lead-in of 3+ words, preferring a natural pause
            cands = [i for i in range(a + 1, b + 1) if 1.0 <= bw[b]["end"] - bw[i]["start"] <= 4.0]
            roomy = [i for i in cands if i - a >= 3] or cands
            k = min(roomy, key=lambda i: abs(bw[b]["end"] - bw[i]["start"] - 1.8)
                    - 2 * (bw[i]["start"] - bw[i - 1]["end"])) if roomy else None
        if k is None:
            raise ValueError("The ending of this clip is too short to loop. Try Punchline first instead.")
        tail_s, tail_e = _pad_span(words, ids[k], ids[b])
        lead_e = _pad_span(words, ids[a], ids[k - 1])[1]
        edl["segments"] = [{"start": round(tail_s, 2), "end": round(min(tail_e, hi), 2)},
                           {"start": round(cs, 2), "end": round(lead_e, 2)}]
        edl["tags"].append("loop")
        notes.append(f"Loop: starts on “{' '.join(w['word'] for w in bw[k:b + 1])}” and ends on “…{' '.join(w['word'] for w in bw[max(a, k - 3):k])}”.")

    if "keyword" in modes:
        kw = re.sub(r"\s+", " ", str(ai.get("keyword", ""))).strip().upper()[:60]
        if not kw:
            kw = re.sub(r"[^\w\s>&'-]", "", clip.get("title", "")).upper()[:40] if not config.ANTHROPIC_API_KEY or not ai else ""
        edl["overlays"].append({"text": kw, "start": 0.0, "dur": round(min(3.0, total_len(edl)), 2), "pos": "top"})
        edl["tags"].append("keyword")
        notes.append(f"Keyword: {kw or '(type your own)'}")
    return {"edl": clean_edl(edl, lo, hi), "notes": notes}


# ---------------------------------------------------------------- export

def _media(cid: str, campaign: dict):
    root = store.cdir(cid) / "assets"
    pick = lambda role, kind: next((root / a["path"] for a in campaign["assets"]
                                    if a["role"] == role and a["kind"] == kind), None)
    return pick("logo", "image"), pick("music", "audio")


def _free_name(cid: str, stem: str) -> str:
    n = 1
    while (store.cdir(cid) / "clips" / f"{stem}_e{n:02d}.mp4").exists():
        n += 1
    return f"{stem}_e{n:02d}"


def export_edl(cid: str, xid: str, file: str, edl: dict, say=None, pct_cb=None,
               src=None, words=None) -> dict:
    """Render an edit of one clip to a NEW clip next to the original, and return it."""
    say = say or (lambda *a: None)
    campaign, item = store.get(cid), store.get_content(cid, xid)
    clip = clip_of(item, file)
    lo, hi = editor_range(item, clip)
    edl = clean_edl(edl, lo, hi)
    if src is None:
        src = open_source(cid, item)
    src, has_audio = src
    if words is None:
        words = load_words(cid, item)
    logo, music = _media(cid, campaign)
    name = _free_name(cid, Path(clip.get("parent") or file).stem)  # number edits from the original clip
    out = store.cdir(cid) / "clips" / f"{name}.mp4"
    segs = [(s["start"], s["end"]) for s in edl["segments"]]
    say(f"Rendering {len(segs)} segment{'s' if len(segs) > 1 else ''}, {total_len(edl):.0f}s…")
    render.render_edit(src, out, segs, words, overlays=edl["overlays"], captions=edl["captions"],
                       mode=campaign["frame_mode"], logo=logo, logo_pos=campaign["logo_position"],
                       logo_scale=campaign["logo_scale"], music=music, music_volume=campaign["music_volume"],
                       src_has_audio=has_audio, pct_cb=pct_cb)
    labels = [TAG_LABEL[t] for t in edl["tags"]] or ["Edited"]
    new = {"file": out.name, "source": clip.get("source", ""), "title": clip["title"], "caption": clip.get("caption", ""),
           "start": min(s["start"] for s in edl["segments"]), "end": max(s["end"] for s in edl["segments"]),
           "duration": round(total_len(edl), 2), "reason": "edit", "score": clip.get("score"),
           "src_key": clip.get("src_key"), "content": xid, "parent": file, "edit": edl,
           "tags": edl["tags"] or ["edited"], "status": "new", "notes": "", "payout": 0}
    out.with_suffix(".txt").write_text(f"{new['title']} ({', '.join(labels)})\n\n{new['caption']}\n")
    store.add_clips(cid, xid, [new])
    say(f"Done: {out.name}")
    return new


def batch_edit(cid: str, xid: str, files: list[str], modes: list[str], say, pct_cb=None) -> list[dict]:
    """Apply the same style(s) to several clips; each becomes a new clip."""
    campaign, item = store.get(cid), store.get_content(cid, xid)
    words = load_words(cid, item)
    src, made = None, []
    for n, f in enumerate(files, 1):
        clip = clip_of(item, f)
        say(f"Clip {n}/{len(files)}: {clip['title']}", round((n - 1) / len(files) * 100))
        try:
            prop = propose(cid, item, clip, modes, campaign, words)
            for note in prop["notes"]:
                say("  " + note)
            if src is None or (isinstance(src[0], dict) and time.time() - src[0]["fetched"] > 3000):
                src = open_source(cid, item)
            made.append(export_edl(cid, xid, f, prop["edl"], say, None, src, words))
        except Exception as e:
            say(f"  Skipped: {str(e)[:160]}")
    return made
