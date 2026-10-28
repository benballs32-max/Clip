"""Cut, reframe to 9:16 and burn word-by-word captions with ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path

W, H = 1080, 1920


def _ts(t: float) -> str:
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(words: list[dict], offset: float, path: Path, per_line: int = 3) -> None:
    """ASS file: groups of words, with the spoken word highlighted."""
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Default,Arial,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1\n\n"
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
                (f"{{\\c&H0000E5FF&}}{x['word'].upper()}{{\\c&HFFFFFF&}}" if k == j else x["word"].upper())
                for k, x in enumerate(chunk)
            )
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}")
    path.write_text(head + "\n".join(lines) + "\n")


LOGO_POS = {  # overlay x:y for a 1080x1920 frame, clear of the caption zone
    "tr": "W-w-40:130", "tl": "40:130", "br": "W-w-40:H-h-640", "bl": "40:H-h-640",
}


def render_clip(src: Path, out: Path, start: float, end: float, words: list[dict],
                mode: str = "blur", captions: bool = True, logo: Path | None = None,
                logo_pos: str = "tr", logo_scale: float = 0.18, music: Path | None = None,
                music_volume: float = 0.12, src_has_audio: bool = True) -> None:
    """mode: 'fill' center-crops to 9:16; 'blur' fits the video over a blurred copy."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.with_suffix(".ass")
    clip_words = [w for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.3]
    use_caps = captions and bool(clip_words)
    if use_caps:
        build_ass(clip_words, start, ass)

    inputs = ["-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(src.resolve())]
    n = 1
    logo_idx = music_idx = None
    if logo and logo_pos in LOGO_POS:
        inputs += ["-i", str(logo.resolve())]
        logo_idx, n = n, n + 1
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music.resolve())]
        music_idx, n = n, n + 1

    if mode == "fill":
        chain = [f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[v0]"]
    else:
        chain = [f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,"
                 f"crop={W}:{H},boxblur=25:5[bg];[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]"]
    last = "v0"
    if logo_idx is not None:
        chain.append(f"[{logo_idx}:v]scale={int(W * logo_scale)}:-1[lg];[{last}][lg]overlay={LOGO_POS[logo_pos]}[v1]")
        last = "v1"
    chain.append(f"[{last}]ass={ass.name}[v]" if use_caps else f"[{last}]null[v]")

    dur = end - start
    fade = f"afade=t=out:st={max(dur - 0.8, 0):.2f}:d=0.8"
    if music_idx is not None and src_has_audio:
        chain.append(f"[0:a]volume=1[oa];[{music_idx}:a]volume={music_volume}[ma];"
                     f"[oa][ma]amix=inputs=2:duration=first:normalize=0,{fade}[a]")
        amap = ["-map", "[a]"]
    elif music_idx is not None:
        chain.append(f"[{music_idx}:a]volume={max(music_volume, 0.6)},{fade}[a]")
        amap = ["-map", "[a]"]
    else:
        amap = ["-map", "0:a?"]

    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chain),
           "-map", "[v]", *amap, "-t", f"{dur:.2f}", "-c:v", "libx264", "-crf", "20",
           "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", out.name]
    subprocess.run(cmd, cwd=out.parent, check=True)
    ass.unlink(missing_ok=True)
