"""Cut, reframe to 9:16 and burn word-by-word captions with ffmpeg."""
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


def render_clip(src: Path, out: Path, start: float, end: float, words: list[dict],
                mode: str = "blur", captions: bool = True) -> None:
    """mode: 'fill' center-crops to 9:16; 'blur' fits the video over a blurred copy."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.with_suffix(".ass")
    clip_words = [w for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.3]
    if captions and clip_words:
        build_ass(clip_words, start, ass)
    sub = f",ass={ass.name}" if captions and clip_words else ""
    if mode == "fill":
        graph = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}{sub}[v]"
    else:
        graph = (
            f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=25:5[bg];[b]scale={W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2{sub}[v]"
        )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
        "-i", str(src.resolve()), "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out.name,
    ]
    subprocess.run(cmd, cwd=out.parent, check=True)
    ass.unlink(missing_ok=True)
