"""Cut, reframe to 9:16, burn captions and text with ffmpeg.

render_edit handles an ordered list of segments (cut, reorder, hook-first, loops) plus text overlays;
render_clip is the single-segment case used by the automatic pipeline."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

W, H = 1080, 1920


def _ts(t: float) -> str:
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_text(text: str) -> str:
    """Make arbitrary user text safe to put inside an ASS dialogue line."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(text))
    text = text.replace("\\", " ").replace("{", "(").replace("}", ")")
    return text.replace("\r", "").replace("\n", "\\N").strip()


# overlay position -> (style name, alignment, vertical margin) on a 1080x1920 frame
TEXT_POS = {"top": ("KeyTop", 8, 300), "center": ("KeyMid", 5, 0), "low": ("KeyLow", 2, 800)}

# subtitle style: every key is a campaign setting
CAP_DEFAULTS = {"cap_font": "Inter", "cap_size": 84, "cap_color": "#FFFFFF", "cap_highlight": "#FFE500",
                "cap_style": "outline", "cap_pos": "low", "cap_words": 3, "cap_upper": True, "cap_pop": False}
CAP_POS = {"low": (2, 520), "mid": (5, 0), "high": (8, 380)}   # alignment, vertical margin
CAP_STYLES = ("outline", "shadow", "box")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def cap_params(style: dict | None) -> dict:
    """Merge a campaign (or any dict) over the defaults and make every value safe to use."""
    st = {k: (style or {}).get(k, v) for k, v in CAP_DEFAULTS.items()}
    st["cap_font"] = re.sub(r"[^\w .\-]", "", str(st["cap_font"]))[:48] or CAP_DEFAULTS["cap_font"]
    try:
        st["cap_size"] = min(max(int(float(st["cap_size"])), 40), 160)
        st["cap_words"] = min(max(int(float(st["cap_words"])), 1), 5)
    except (TypeError, ValueError):
        st["cap_size"], st["cap_words"] = CAP_DEFAULTS["cap_size"], CAP_DEFAULTS["cap_words"]
    for k in ("cap_color", "cap_highlight"):
        st[k] = str(st[k]) if _HEX.match(str(st[k])) else CAP_DEFAULTS[k]
    st["cap_style"] = st["cap_style"] if st["cap_style"] in CAP_STYLES else "outline"
    st["cap_pos"] = st["cap_pos"] if st["cap_pos"] in CAP_POS else "low"
    st["cap_upper"], st["cap_pop"] = bool(st["cap_upper"]), bool(st["cap_pop"])
    return st


def _bgr(hexcolor: str, alpha: int = 0) -> str:
    """#RRGGBB -> ASS &HAABBGGRR (alpha 0 = opaque)."""
    r, g, b = hexcolor[1:3], hexcolor[3:5], hexcolor[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _style_line(cap: dict) -> str:
    size, mode = cap["cap_size"], cap["cap_style"]
    align, mv = CAP_POS[cap["cap_pos"]]
    prim = _bgr(cap["cap_color"])
    if mode == "box":      # solid dark box behind the line
        border, outline, shadow, ocol, back = 3, max(12, int(size * 0.17)), 0, "&H00181818", "&H00181818"
    elif mode == "shadow":  # no outline, soft drop shadow
        border, outline, shadow, ocol, back = 1, 0, max(3, int(size * 0.05)), "&H00000000", "&H96000000"
    else:                  # classic outline
        border, outline, shadow, ocol, back = 1, max(3, int(size * 0.075)), max(1, int(size * 0.025)), "&H00000000", "&H80000000"
    return (f"Style: Default,{cap['cap_font']},{size},{prim},{prim},{ocol},{back},-1,0,0,0,100,100,0,0,"
            f"{border},{outline},{shadow},{align},60,60,{mv},1")


def build_ass(words: list[dict], offset: float, path: Path, per_line: int | None = None, overlays=(),
              style: dict | None = None) -> None:
    """ASS file: caption chunks with the spoken word highlighted, plus optional text overlays.
    Word times are shifted by -offset; overlays are already in output time."""
    cap = cap_params(style)
    per_line = per_line or cap["cap_words"]
    prim, hl = _bgr(cap["cap_color"]), _bgr(cap["cap_highlight"])
    key_styles = "".join(
        f"Style: {n},{cap['cap_font']},104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,8,3,{al},70,70,{mv},1\n"
        for n, al, mv in TEXT_POS.values())
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        + _style_line(cap) + "\n" + key_styles + "\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    fix = (lambda t: t.upper()) if cap["cap_upper"] else (lambda t: t)
    pop = "\\fscx86\\fscy86\\t(0,110,\\fscx106\\fscy106)" if cap["cap_pop"] else ""
    lines = []
    for i in range(0, len(words), per_line):
        chunk = words[i:i + per_line]
        for j, w in enumerate(chunk):
            start = w["start"] - offset
            end = (chunk[j + 1]["start"] if j + 1 < len(chunk) else w["end"]) - offset
            if end <= start:
                continue
            text = " ".join(
                (f"{{\\c{hl}&{pop}}}{fix(ass_text(x['word']))}{{\\r}}" if k == j else fix(ass_text(x["word"])))
                for k, x in enumerate(chunk)
            )
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}")
    for o in overlays:  # keyword / title text: fades in with a small pop, fades out
        style_name = TEXT_POS.get(o.get("pos"), TEXT_POS["top"])[0]
        s, e = float(o["start"]), float(o["start"]) + float(o["dur"])
        if e > s and str(o.get("text", "")).strip():
            lines.append(f"Dialogue: 1,{_ts(s)},{_ts(e)},{style_name},,0,0,0,,"
                         f"{{\\fad(150,200)\\fscx88\\fscy88\\t(0,180,\\fscx100\\fscy100)}}{ass_text(o['text']).upper()}")
    path.write_text(head + "\n".join(lines) + "\n")


def _filter_path(p: str) -> str:
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def caption_sample(out_png: Path, style: dict | None, fontsdir: str | None = None) -> None:
    """A 360x640 still showing the subtitle style, rendered by the real renderer."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    ass = out_png.with_suffix(".ass")
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.36} for i, w in enumerate("This is how your captions will look".split())]
    build_ass(words, 0.0, ass, style=style)
    fd = f":fontsdir={_filter_path(fontsdir)}" if fontsdir else ""
    vf = f"ass={ass.name}{fd},scale=360:640"
    base = ["ffmpeg", "-y", "-loglevel", "error"]
    try:
        for src in ("gradients=s=1080x1920:c0=0x20242e:c1=0x4a5268:x0=0:y0=0:x1=1080:y1=1920:duration=1:rate=10",
                    "color=c=0x2a2f3b:s=1080x1920:d=1:r=10"):
            r = subprocess.run([*base, "-f", "lavfi", "-i", src, "-vf", vf, "-ss", "0.5", "-frames:v", "1", out_png.name],
                               cwd=out_png.parent, capture_output=True)
            if r.returncode == 0:
                return
        raise RuntimeError("Couldn't render the preview: " + r.stderr.decode()[-200:])
    finally:
        ass.unlink(missing_ok=True)


LOGO_POS = {  # overlay x:y for a 1080x1920 frame, clear of the caption zone
    "tr": "W-w-40:130", "tl": "40:130", "br": "W-w-40:H-h-640", "bl": "40:H-h-640",
}


def _seg_inputs(src, segments):
    """ffmpeg input args: one seek-trimmed input per segment, from a local file or a streamed YouTube
    spec. Returns (args, input_count, video_input_index_per_segment, audio_input_index_per_segment)."""
    args, vi, ai, n = [], [], [], 0
    for s, e in segments:
        seek = ["-ss", f"{s:.2f}", "-to", f"{e:.2f}"]
        if isinstance(src, dict):  # {"video": url, "audio": url|None, "headers": str}
            hdr = ["-headers", src["headers"]] if src.get("headers") else []
            args += [*seek, *hdr, "-i", src["video"]]
            vi.append(n); n += 1
            if src.get("audio"):
                args += [*seek, *hdr, "-i", src["audio"]]
                ai.append(n); n += 1
            else:
                ai.append(vi[-1])
        else:
            args += [*seek, "-i", str(src.resolve())]
            vi.append(n); ai.append(n); n += 1
    return args, n, vi, ai


def map_words(words: list[dict], segments) -> list[dict]:
    """Source-time words -> output-time words for an ordered list of segments (a word used by two
    segments appears twice, which is what a punchline-first edit needs)."""
    out, off = [], 0.0
    for a, b in segments:
        for w in words:
            if a - 0.05 <= (w["start"] + w["end"]) / 2 <= b:  # by midpoint: the next sentence's first word never leaks in
                s = max(w["start"] - a, 0.0) + off
                out.append({"word": w["word"], "start": s, "end": min(w["end"] - a + off, off + (b - a))})
        off += b - a
    return out


def _run(cmd: list[str], cwd: Path, total: float, pct_cb: Callable[[int], None] | None) -> None:
    """Run ffmpeg; if pct_cb is given, report progress (0-99) from ffmpeg's -progress stream."""
    if pct_cb is None:
        subprocess.run(cmd, cwd=cwd, check=True)
        return
    p = subprocess.Popen([cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]], cwd=cwd,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail, last = [], -1
    for line in p.stdout:
        if line.startswith("out_time_ms="):
            try:
                pct = min(99, int(int(line.split("=")[1]) / 1e6 / max(total, 0.1) * 100))
            except ValueError:
                continue
            if pct >= last + 3:
                last = pct
                pct_cb(pct)
        elif "=" not in line.split(" ")[0]:  # not a key=value progress line: keep for the error message
            tail = (tail + [line.strip()])[-6:]
    if p.wait():
        raise RuntimeError("ffmpeg failed: " + " | ".join(t for t in tail if t)[:400])


def follow_geometry(mode: str, dims: tuple[int, int]) -> tuple[int, float]:
    """Crop window width in source pixels for a follow mode, and as a fraction of the frame width."""
    ratio = 9 / 16 if mode == "follow" else 4 / 5
    cw = min(int(round(dims[1] * ratio / 2)) * 2, dims[0])
    return cw, cw / dims[0]


def _x_expr(keys: list[list[float]], segments, W0: int, cw: int) -> str:
    """ffmpeg expression for the crop's left edge over OUTPUT time, from source-time camera keyframes."""
    from .track import cx_at
    pts, off = [], 0.0
    for a, b in segments:
        pts.append((off, cx_at(keys, a)))
        pts += [(off + t - a, c) for t, c in keys if a < t < b]
        pts.append((off + b - a, cx_at(keys, b)))
        off += b - a
    if len(pts) > 160:  # keep the expression a sane size
        step = len(pts) / 160
        pts = [pts[int(i * step)] for i in range(160)] + [pts[-1]]
    xs = [(t, min(max(c * W0 - cw / 2, 0.0), W0 - cw)) for t, c in pts]
    expr = f"{xs[-1][1]:.1f}"
    for i in range(len(xs) - 2, -1, -1):
        (t0, x0), (t1, x1) = xs[i], xs[i + 1]
        if t1 - t0 < 1e-3:  # a step (camera cut or a segment join): the later piece takes over
            continue
        expr = f"if(lt(t,{t1:.3f}),{x0:.1f}+({x1 - x0:.1f})*(t-{t0:.3f})/{t1 - t0:.3f},{expr})"
    return expr


def render_edit(src, out: Path, segments, words: list[dict], *, overlays=(), mode: str = "blur",
                captions: bool = True, logo: Path | None = None, logo_pos: str = "tr",
                logo_scale: float = 0.18, music: Path | None = None, music_volume: float = 0.12,
                src_has_audio: bool = True, pct_cb: Callable[[int], None] | None = None,
                style: dict | None = None, track: dict | None = None, src_dims: tuple[int, int] | None = None) -> None:
    """Render ordered source segments [(start, end), ...] to a 1080x1920 clip.
    mode: 'fill' center-crops to 9:16; 'blur' fits the video over a blurred copy."""
    out.parent.mkdir(parents=True, exist_ok=True)
    segments = [(float(a), float(b)) for a, b in segments]
    total = sum(b - a for a, b in segments)
    ass = out.with_suffix(".ass")
    cap_words = map_words(words, segments) if captions else []
    overlays = [o for o in overlays if str(o.get("text", "")).strip()]
    use_ass = bool(cap_words or overlays)
    if use_ass:
        build_ass(cap_words, 0.0, ass, overlays=overlays, style=style)

    inputs, n, vi, ai = _seg_inputs(src, segments)
    logo_idx = music_idx = None
    if logo and logo_pos in LOGO_POS:
        inputs += ["-i", str(logo.resolve())]
        logo_idx, n = n, n + 1
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music.resolve())]
        music_idx, n = n, n + 1

    chain = []
    if len(segments) == 1:
        chain.append(f"[{vi[0]}:v]null[vs]")
        if src_has_audio:
            chain.append(f"[{ai[0]}:a]anull[as]")
    else:  # trim each piece to zero, then join them end to end (tiny audio fades hide the joins)
        parts = ""
        for k, (a, b) in enumerate(segments):
            chain.append(f"[{vi[k]}:v]setpts=PTS-STARTPTS[v{k}]")
            if src_has_audio:
                d = b - a
                chain.append(f"[{ai[k]}:a]asetpts=PTS-STARTPTS,afade=t=in:d=0.03,"
                             f"afade=t=out:st={max(d - 0.03, 0):.2f}:d=0.03[a{k}]")
                parts += f"[v{k}][a{k}]"
            else:
                parts += f"[v{k}]"
        chain.append(f"{parts}concat=n={len(segments)}:v=1:a={1 if src_has_audio else 0}[vs]" + ("[as]" if src_has_audio else ""))

    follow = mode in ("follow", "follow45") and track and track.get("keys") and src_dims
    if follow:  # crop a vertical window that follows the speaker
        W0, H0 = src_dims
        cw, _ = follow_geometry(mode, src_dims)
        xe = _x_expr(track["keys"], segments, W0, cw)
        if mode == "follow":
            chain.append(f"[vs]crop=w={cw}:h={H0}:x='{xe}':y=0,scale={W}:{H}[v0]")
        else:  # 4:5 window over a blurred copy: sharper than a full 9:16 crop
            fg_h = int(round(W * H0 / cw / 2)) * 2
            chain.append(f"[vs]split[sa][sb];[sa]scale=270:480:force_original_aspect_ratio=increase,"
                         f"crop=270:480,boxblur=8:2,scale={W}:{H}[bg];[sb]crop=w={cw}:h={H0}:x='{xe}':y=0,"
                         f"scale={W}:{fg_h}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]")
    elif mode == "fill":
        chain.append(f"[vs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[v0]")
    else:
        chain.append(f"[vs]split[sa][sb];[sa]scale=270:480:force_original_aspect_ratio=increase,"
                     f"crop=270:480,boxblur=8:2,scale={W}:{H}[bg];[sb]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]")
    last = "v0"
    if logo_idx is not None:
        chain.append(f"[{logo_idx}:v]scale={int(W * logo_scale)}:-1[lg];[{last}][lg]overlay={LOGO_POS[logo_pos]}[v1]")
        last = "v1"
    fd = f":fontsdir={_filter_path(str((style or {}).get('fontsdir')))}" if (style or {}).get("fontsdir") else ""
    chain.append(f"[{last}]ass={ass.name}{fd}[v]" if use_ass else f"[{last}]null[v]")

    fade = f"afade=t=out:st={max(total - 0.8, 0):.2f}:d=0.8"
    amap: list[str] = []
    if music_idx is not None and src_has_audio:
        chain.append(f"[as]volume=1[oa];[{music_idx}:a]volume={music_volume}[ma];"
                     f"[oa][ma]amix=inputs=2:duration=first:normalize=0,{fade}[a]")
        amap = ["-map", "[a]"]
    elif music_idx is not None:
        chain.append(f"[{music_idx}:a]volume={max(music_volume, 0.6)},{fade}[a]")
        amap = ["-map", "[a]"]
    elif src_has_audio:
        amap = ["-map", "[as]"]

    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chain),
           "-map", "[v]", *amap, "-t", f"{total:.2f}", "-c:v", "libx264", "-crf", "20",
           "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", out.name]
    try:
        _run(cmd, out.parent, total, pct_cb)
    finally:
        ass.unlink(missing_ok=True)


def render_clip(src, out: Path, start: float, end: float, words: list[dict], mode: str = "blur",
                captions: bool = True, logo: Path | None = None, logo_pos: str = "tr",
                logo_scale: float = 0.18, music: Path | None = None, music_volume: float = 0.12,
                src_has_audio: bool = True, style: dict | None = None, track: dict | None = None,
                src_dims: tuple[int, int] | None = None) -> None:
    """One contiguous cut, as made by the automatic pipeline."""
    render_edit(src, out, [(start, end)], words, mode=mode, captions=captions, logo=logo,
                logo_pos=logo_pos, logo_scale=logo_scale, music=music, music_volume=music_volume,
                src_has_audio=src_has_audio, style=style, track=track, src_dims=src_dims)


def build_proxy(src, lo: float, hi: float, out: Path, pct_cb: Callable[[int], None] | None = None) -> None:
    """Small, seek-friendly 540p copy of [lo, hi] for instant in-browser preview (a keyframe every second)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    inputs, _, vi, ai = _seg_inputs(src, [(lo, hi)])
    tmp = out.with_name("proxy.part.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-map", f"{vi[0]}:v", "-map", f"{ai[0]}:a?",
           "-vf", "scale=-2:540", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-g", "30",
           "-keyint_min", "30", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
           "-movflags", "+faststart", tmp.name]
    _run(cmd, out.parent, hi - lo, pct_cb)
    tmp.replace(out)
