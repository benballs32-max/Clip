"""Speaker-follow framing: find the main face a few times per second, then build a calm camera path.

The path is a short list of keyframes [t, cx] (cx = horizontal centre of the subject as a fraction of
the frame width). It holds still until the speaker actually moves, glides when they do, and snaps on
camera cuts, so the crop doesn't jitter the way a naive face-follow does."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import render

MODEL = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
SAMPLE_FPS = 4
DET_W = 480            # frames are decoded at this width for detection
MIN_SCORE = 0.7
DEAD_ZONE = 0.035      # the camera ignores subject movement smaller than this (fraction of frame width)
MAX_SPEED = 0.30       # fastest glide, in frame widths per second
CUT_JUMP = 0.22        # a subject jump bigger than this between samples is a camera cut, not movement


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        return MODEL.exists() and hasattr(cv2, "FaceDetectorYN")
    except Exception:
        return False


def _frames(src, a: float, b: float, dims: tuple[int, int]):
    """Yield (t, bgr frame) at SAMPLE_FPS for source seconds [a, b], decoded small."""
    import numpy as np
    w = DET_W
    h = max(2, int(round(dims[1] * w / dims[0] / 2)) * 2)
    inputs, _, vi, _ = render._seg_inputs(src, [(a, b)])
    cmd = ["ffmpeg", "-loglevel", "error", *inputs, "-map", f"{vi[0]}:v", "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size, n = w * h * 3, 0
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield a + n / SAMPLE_FPS, np.frombuffer(buf, np.uint8).reshape(h, w, 3), (w, h)
            n += 1
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def _pick(faces: list[tuple[float, float, float]], prev: float | None, crop_w: float):
    """Choose where the camera should look: (centre_x, face_width) as fractions of the frame, or None."""
    if not faces:
        return None
    if len(faces) >= 2:  # a wide shot where everyone fits in the crop window: frame them together
        lo, hi = min(f[0] - f[2] / 2 for f in faces), max(f[0] + f[2] / 2 for f in faces)
        if hi - lo <= crop_w * 0.9:
            return (lo + hi) / 2, max(f[2] for f in faces)
    if prev is None:
        f = max(faces, key=lambda f: f[2])
        return f[0], f[2]
    # otherwise stay on the person we were following, unless someone is much bigger (a cut to a close-up)
    near = min(faces, key=lambda f: abs(f[0] - prev) - f[2])
    big = max(faces, key=lambda f: f[2])
    f = big if big[2] > near[2] * 1.8 else near
    return f[0], f[2]


def _rdp(pts: list[list[float]], eps: float) -> list[list[float]]:
    """Ramer-Douglas-Peucker: keep only the keyframes that matter."""
    if len(pts) < 3:
        return pts
    (t0, c0), (t1, c1) = pts[0], pts[-1]
    best, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        t, c = pts[i]
        line = c0 + (c1 - c0) * (t - t0) / (t1 - t0) if t1 > t0 else c0
        if abs(c - line) > best:
            best, idx = abs(c - line), i
    if best <= eps:
        return [pts[0], pts[-1]]
    return _rdp(pts[: idx + 1], eps)[:-1] + _rdp(pts[idx:], eps)


def build_path(samples: list[tuple[float, float | None, float]]) -> list[list[float]]:
    """samples = [(t, subject_cx or None, face_width)] -> keyframes [[t, cx], ...] of a virtual camera."""
    known = [s for s in samples if s[1] is not None]
    if not known:
        return []
    dt = 1.0 / SAMPLE_FPS
    cam, prev_subject, prev_size = known[0][1], known[0][1], known[0][2]
    out, cuts, first = [], set(), True
    held_c, held_size = known[0][1], known[0][2]
    for t, c, size in samples:
        subject, sz = (held_c, held_size) if c is None else (c, size)  # lost the face: hold where we were looking
        held_c, held_size = subject, sz
        shot_change = abs(subject - prev_subject) > CUT_JUMP or (
            abs(subject - prev_subject) > 0.08 and not (0.5 < sz / max(prev_size, 1e-6) < 2.0))  # face suddenly much bigger/smaller
        if first:
            cam, first = subject, False
        elif shot_change:  # camera cut: hold the old framing until this instant, then jump
            out.append([round(t - 0.001, 3), cam])
            cuts.add(round(t, 3))
            cam = subject
        elif abs(subject - cam) > DEAD_ZONE:
            cam += max(-MAX_SPEED * dt, min(MAX_SPEED * dt, (subject - cam) * 0.35))
        prev_subject, prev_size = subject, sz
        out.append([round(t, 3), round(cam, 4)])
    # simplify each continuous shot separately so a cut stays a hard step
    keys: list[list[float]] = []
    start = 0
    bounds = [i for i, (t, _) in enumerate(out) if t in cuts] + [len(out)]
    for end in bounds:
        if end > start:
            keys += _rdp(out[start:end], 0.010)
        start = end
    return keys


def compute(src, a: float, b: float, dims: tuple[int, int], crop_w: float = 0.32, pct_cb=None) -> dict:
    """Camera keyframes for source seconds [a, b]. Returns {"keys": [...], "seen": fraction of frames with a face}."""
    if not available():
        raise RuntimeError("Face detection isn't installed (needs opencv-python-headless).")
    import cv2
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (DET_W, 270), MIN_SCORE, 0.3, 5000)
    samples, seen, total, prev = [], 0, 0, None
    for t, frame, (w, h) in _frames(src, a, b, dims):
        det.setInputSize((w, h))
        _, res = det.detect(frame)
        faces = [] if res is None else [(float((r[0] + r[2] / 2) / w), float((r[1] + r[3] / 2) / h), float(r[2] / w)) for r in res if r[14] >= MIN_SCORE]
        pick = _pick(faces, prev, crop_w)
        if pick is not None:
            prev, seen = pick[0], seen + 1
        samples.append((t, pick[0] if pick else None, pick[1] if pick else 0.0))
        total += 1
        if pct_cb and total % 20 == 0:
            pct_cb(min(99, int((t - a) / max(b - a, 0.1) * 100)))
    return {"keys": [[float(t), float(c)] for t, c in build_path(samples)], "seen": round(seen / max(total, 1), 2), "samples": total}


def cx_at(keys: list[list[float]], t: float) -> float:
    """Camera centre at source time t (linear between keyframes, held outside them)."""
    if not keys:
        return 0.5
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, c0), (t1, c1) in zip(keys, keys[1:]):
        if t < t1:
            return c0 if t1 - t0 < 1e-6 else c0 + (c1 - c0) * (t - t0) / (t1 - t0)
    return keys[-1][1]
