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


def build_ass(words: list[dict], offset: float, path: Path, per_line: int = 3, overlays=()) -> None:
    """ASS file: caption chunks with the spoken word highlighted, plus optional text overlays.
    Word times are shifted by -offset; overlays are already in output time."""
    key_styles = "".join(
        f"Style: {n},Arial,104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,8,3,{al},70,70,{mv},1\n"
        for n, al, mv in TEXT_POS.values())
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Default,Arial,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1\n"
        + key_styles + "\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    lines = []
    for i in range(0, len(words), per_line):
        chunk = words[i:i + per_line]
        for j, w in enumerate(chunk):
            start = w["start"] - offset
            end = (chunk[j + 1]["start"] if j + 1 < len(chunk) else w["end"]) - offset
            if end <= start:
                continue
            text = " ".join(
                (f"{{\\c&H0000E5FF&}}{ass_text(x['word']).upper()}{{\\c&HFFFFFF&}}" if k == j else ass_text(x["word"]).upper())
                for k, x in enumerate(chunk)
            )
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}")
    for o in overlays:  # keyword / title text: fades in with a small pop, fades out
        style = TEXT_POS.get(o.get("pos"), TEXT_POS["top"])[0]
        s, e = float(o["start"]), float(o["start"]) + float(o["dur"])
        if e > s and str(o.get("text", "")).strip():
            lines.append(f"Dialogue: 1,{_ts(s)},{_ts(e)},{style},,0,0,0,,"
                         f"{{\\fad(150,200)\\fscx88\\fscy88\\t(0,180,\\fscx100\\fscy100)}}{ass_text(o['text']).upper()}")
    path.write_text(head + "\n".join(lines) + "\n")


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


def render_edit(src, out: Path, segments, words: list[dict], *, overlays=(), mode: str = "blur",
                captions: bool = True, logo: Path | None = None, logo_pos: str = "tr",
                logo_scale: float = 0.18, music: Path | None = None, music_volume: float = 0.12,
                src_has_audio: bool = True, pct_cb: Callable[[int], None] | None = None) -> None:
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
        build_ass(cap_words, 0.0, ass, overlays=overlays)

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

    if mode == "fill":
        chain.append(f"[vs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[v0]")
    else:
        chain.append(f"[vs]split[sa][sb];[sa]scale=270:480:force_original_aspect_ratio=increase,"
                     f"crop=270:480,boxblur=8:2,scale={W}:{H}[bg];[sb]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]")
    last = "v0"
    if logo_idx is not None:
        chain.append(f"[{logo_idx}:v]scale={int(W * logo_scale)}:-1[lg];[{last}][lg]overlay={LOGO_POS[logo_pos]}[v1]")
        last = "v1"
    chain.append(f"[{last}]ass={ass.name}[v]" if use_ass else f"[{last}]null[v]")

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
                src_has_audio: bool = True) -> None:
    """One contiguous cut, as made by the automatic pipeline."""
    render_edit(src, out, [(start, end)], words, mode=mode, captions=captions, logo=logo,
                logo_pos=logo_pos, logo_scale=logo_scale, music=music, music_volume=music_volume,
                src_has_audio=src_has_audio)


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
