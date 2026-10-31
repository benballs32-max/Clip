"""YouTube thumbnails: sample frames, rank them (face, sharpness), let Claude pick, draw the text."""
from __future__ import annotations

import glob
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, llm, render, track

W, H = 1280, 720
SAMPLES = 16      # frames pulled from the video
SHORTLIST = 8     # the best of those are shown to Claude
MIN_GAP = 20.0    # seconds between shortlisted frames, so they are not near-duplicates
ACCENT = (255, 229, 0)

FONTS = ["/usr/share/fonts/opentype/bebas-neue/BebasNeue-Regular.otf",
         "/usr/share/fonts/opentype/league-spartan/LeagueSpartan-Bold.otf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/System/Library/Fonts/Supplemental/Impact.ttf", "/System/Library/Fonts/Helvetica.ttc"]

TOOL = {
    "name": "choose_thumbnails",
    "description": "Choose the best thumbnail frames and the text for each.",
    "input_schema": {"type": "object", "properties": {"picks": {"type": "array", "items": {
        "type": "object", "properties": {
            "frame": {"type": "integer", "description": "Index of the chosen frame"},
            "text": {"type": "string", "description": "Thumbnail text, 1-4 words, taken from or based on the options"},
            "why": {"type": "string", "description": "One short sentence on why this frame works"}},
        "required": ["frame", "text", "why"]}}}, "required": ["picks"]},
}


def available() -> bool:
    try:
        import PIL  # noqa: F401
        return track.available()
    except Exception:
        return False


def _grab(src, t: float, out: Path) -> bool:
    """One frame at t seconds, cropped to 16:9 and scaled to 1280x720."""
    inputs, _, vi, _ = render._seg_inputs(src, [(t, t + 1.5)])
    cmd = ["ffmpeg", "-loglevel", "error", "-y", *inputs, "-map", f"{vi[0]}:v", "-frames:v", "1",
           "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", "-q:v", "2", str(out)]
    try:
        subprocess.run(cmd, check=True, timeout=90, stdin=subprocess.DEVNULL)
    except Exception:
        return False
    return out.exists() and out.stat().st_size > 0


def sample_times(duration: float, focus: list[float], n: int = SAMPLES) -> list[float]:
    """Moments worth looking at: the middles of the chosen clips plus an even spread over the video."""
    lo, hi = duration * 0.04, duration * 0.96
    times = [t for t in focus if lo <= t <= hi]
    spread = max(n - len(times), 0)
    times += [lo + (hi - lo) * (i + 0.5) / max(spread, 1) for i in range(spread)]
    return sorted(set(round(t, 1) for t in times))[: n + len(focus)]


def _score(frame, det) -> tuple[float, float | None]:
    """(score, face centre x as 0..1 or None). Prefers one big, sharp, well-lit face."""
    import cv2
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (640, 360))
    det.setInputSize((640, 360))
    _, res = det.detect(small)
    faces = [] if res is None else [r for r in res if r[14] >= track.MIN_SCORE]
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
    light = float(gray.mean())
    s = 2.0 * sharp / (sharp + 120) - (1.5 if light < 40 or light > 225 else 0)
    if not faces:
        return s + 0.3, None
    big = max(faces, key=lambda r: r[2] * r[3])
    size = float(big[2]) / 640
    cx = float(big[0] + big[2] / 2) / 640
    s += (3.0 if len(faces) == 1 else 1.8) + min(size / 0.22, 1.0) * 2.0 - (1.0 if size > 0.5 else 0)
    return s, cx


def _jpeg(frame, width: int = 640) -> bytes:
    import cv2
    h, w = frame.shape[:2]
    ok, buf = cv2.imencode(".jpg", cv2.resize(frame, (width, int(h * width / w))), [cv2.IMWRITE_JPEG_QUALITY, 72])
    return buf.tobytes()


def shortlist(src, duration: float, focus: list[float], work: Path, progress) -> list[dict]:
    """Grab frames, score them, return the best SHORTLIST as [{t, frame, cx, score}]."""
    import cv2
    work.mkdir(parents=True, exist_ok=True)
    times = sample_times(duration, focus)
    progress(f"Looking at {len(times)} moments of the video for thumbnail frames…")
    with ThreadPoolExecutor(6) as ex:
        ok = list(ex.map(lambda i: _grab(src, times[i], work / f"f{i:02d}.jpg"), range(len(times))))
    det = cv2.FaceDetectorYN.create(str(track.MODEL), "", (640, 360), track.MIN_SCORE, 0.3, 5000)
    scored = []
    for i, good in enumerate(ok):
        if not good:
            continue
        frame = cv2.imread(str(work / f"f{i:02d}.jpg"))
        if frame is None:
            continue
        s, cx = _score(frame, det)
        scored.append({"t": times[i], "frame": frame, "cx": cx, "score": s})
    keep = []
    for c in sorted(scored, key=lambda c: -c["score"]):
        if all(abs(c["t"] - k["t"]) >= MIN_GAP for k in keep):
            keep.append(c)
        if len(keep) >= SHORTLIST:
            break
    return sorted(keep, key=lambda c: c["t"])


def choose(cands: list[dict], title: str, texts: list[str], campaign: dict, count: int = 3) -> list[dict]:
    """Claude looks at the shortlisted frames and picks the best, each with its text."""
    fallback = [{"frame": i, "text": texts[i % len(texts)] if texts else title, "why": "Sharp frame with a clear face"}
                for i in range(min(count, len(cands)))]
    if not config.ANTHROPIC_API_KEY or len(cands) <= 1:
        return fallback
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    images = [(f"Frame {i} (at {int(c['t'] // 60)}:{int(c['t'] % 60):02d})", _jpeg(c["frame"])) for i, c in enumerate(cands)]
    prompt = (
        f"You are choosing YouTube thumbnails for a video titled \"{title}\".\n"
        f"Channel: {campaign.get('name')}. Audience: {campaign.get('audience') or 'general'}.\n"
        f"Candidate text: {'; '.join(texts) or 'none given'}\n\n"
        f"Pick the {count} best frames above, all different. Prefer a clear, expressive face looking engaged, "
        "eyes open, not mid-blink or mid-word with an awkward mouth, no motion blur, good lighting, uncluttered "
        "background, and room on one side for text. Avoid slides, black frames, and logos filling the frame. "
        "For each, give 1 to 4 words of thumbnail text that complements the title instead of repeating it and "
        "does not promise anything the video does not deliver. Use one of the candidate texts or a short variation.")
    try:
        data, _ = llm.call_tool(client, config.REFINE_MODEL if config.REFINE_MODEL.lower() not in ("", "none", "off")
                                else config.CLAUDE_MODEL, TOOL, prompt, 1500, images=images)
    except Exception:
        return fallback
    seen, picks = set(), []
    for p in data.get("picks", []):
        i = p.get("frame")
        if isinstance(i, int) and 0 <= i < len(cands) and i not in seen and str(p.get("text", "")).strip():
            seen.add(i)
            picks.append({"frame": i, "text": " ".join(str(p["text"]).split()[:5]), "why": str(p.get("why", ""))[:200]})
    return picks[:count] or fallback


def _font(size: int):
    from PIL import ImageFont
    for f in [*glob.glob("/usr/share/fonts/**/BebasNeue-Regular.otf", recursive=True), *FONTS]:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, words: list[str], font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if cur and draw.textlength(test, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = test
    return lines + [cur] if cur else lines


def draw(frame, text: str, face_cx: float | None, out: Path) -> None:
    """Punch the colours up, shade the text side, and set the text big with an outline."""
    import cv2
    from PIL import Image, ImageDraw, ImageEnhance
    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.22)).enhance(1.08)
    right = face_cx is not None and face_cx < 0.5  # text goes on the side away from the face
    shade = Image.new("L", (W, 1))
    for x in range(W):
        edge = (W - 1 - x) / W if right else x / W
        shade.putpixel((x, 0), int(max(0.0, 1 - edge / 0.62) * 175))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade.resize((W, H)))
    d = ImageDraw.Draw(img)
    words = text.upper().split() or [" "]
    margin = 48
    box_w, box_h = int(W * 0.44) - margin, int(H * 0.8)  # stays clear of the middle, where the face is
    for size in range(240, 60, -6):
        font = _font(size)
        pad = max(4, size // 12) * 2  # the outline widens every line
        lines = _wrap(d, words, font, box_w - pad)
        line_h = size * 1.0
        if len(lines) <= 3 and len(lines) * line_h <= box_h and max(d.textlength(l, font=font) for l in lines) + pad <= box_w:
            break
    x0 = W - margin - box_w + 8 if right else margin
    y = (H - len(lines) * line_h) / 2
    for i, line in enumerate(lines):
        accent = i == len(lines) - 1 and (len(lines) > 1 or len(words) == 1)  # last line pops in yellow
        color = ACCENT if accent else (255, 255, 255)
        d.text((x0, y), line, font=font, fill=color, stroke_width=max(4, size // 12), stroke_fill=(0, 0, 0))
        y += line_h
    img.save(out, quality=92)


def make(src, duration: float, focus: list[float], title: str, texts: list[str], campaign: dict,
         work: Path, out_dir: Path, prefix: str, progress=print) -> list[dict]:
    """Return [{file, text, why, t}] and write the JPEGs into out_dir."""
    cands = shortlist(src, duration, focus, work, progress)
    if not cands:
        raise RuntimeError("Couldn't read any frames from the video.")
    progress(f"{config.REFINE_MODEL} is choosing the best of {len(cands)} frames…")
    picks = choose(cands, title, texts, campaign)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for n, p in enumerate(picks, 1):
        c = cands[p["frame"]]
        name = f"{prefix}_thumb_{n:02d}.jpg"
        draw(c["frame"], p["text"], c["cx"], out_dir / name)
        result.append({"file": name, "text": p["text"], "why": p["why"], "t": c["t"]})
    for f in work.glob("f*.jpg"):
        f.unlink(missing_ok=True)
    progress(f"Made {len(result)} thumbnails.")
    return result
