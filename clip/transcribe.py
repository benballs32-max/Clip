"""Local speech-to-text with word timestamps (faster-whisper)."""
import json
import time
from pathlib import Path

from . import config

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.WHISPER_MODEL, device="auto", compute_type="int8")
    return _model


def transcribe(video: Path, progress=None) -> list[dict]:
    """Return [{start, end, text, words:[{start,end,word}]}]. Cached beside the video.

    progress(msg, pct) is called roughly every 5% with an estimate of the time left."""
    say = progress or (lambda *a: None)
    cache = video.with_suffix(".transcript.json")
    if cache.exists():
        say("Using cached transcript.")
        return json.loads(cache.read_text())
    say(f"Loading speech model ({config.WHISPER_MODEL})…")
    model = _get_model()
    segments, info = model.transcribe(str(video), word_timestamps=True, vad_filter=True)
    total, began, last, out = info.duration or 0, time.time(), -5, []
    say(f"Transcribing {int(total // 60) if total >= 120 else int(total)} {'min' if total >= 120 else 's'} of audio…", 0)
    for s in segments:
        out.append({
            "start": s.start, "end": s.end, "text": s.text.strip(),
            "words": [{"start": w.start, "end": w.end, "word": w.word.strip()} for w in (s.words or [])],
        })
        if total and s.end > 0:
            pct = int(min(s.end / total, 1) * 100)
            if pct >= last + 5:
                last = pct - pct % 5
                spent = time.time() - began
                left = spent / (s.end / total) - spent
                say(f"Transcribing… {pct}% (about {max(left / 60, 0):.0f} min left)", pct)
    cache.write_text(json.dumps(out))
    return out
