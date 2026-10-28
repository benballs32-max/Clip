"""Scene-based clip windows for footage with little or no speech (trailers, cinematics)."""
import re
import subprocess
from pathlib import Path


def detect_cuts(src: Path, threshold: float = 0.35) -> list[float]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-vf",
                        f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def windows(duration: float, cuts: list[float], min_s: float, max_s: float, count: int) -> list[tuple[float, float]]:
    """Cut-aligned windows of min_s..max_s, spread through the video."""
    if duration <= max_s:
        return [(0.0, duration)] if duration >= 3 else []
    points = [0.0] + cuts + [duration]
    out, t = [], 0.0
    while t < duration - min_s and len(out) < count * 3:
        ends = [p for p in points if t + min_s <= p <= t + max_s]
        end = max(ends) if ends else min(t + max_s, duration)
        out.append((round(t, 2), round(end, 2)))
        t = end
    if len(out) > count:  # keep an even spread
        step = len(out) / count
        out = [out[int(i * step)] for i in range(count)]
    return out
