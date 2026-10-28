"""Local speech-to-text with word timestamps (faster-whisper)."""
import json
from pathlib import Path

from . import config

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.WHISPER_MODEL, device="auto", compute_type="int8")
    return _model


def transcribe(video: Path) -> list[dict]:
    """Return [{start, end, text, words:[{start,end,word}]}]. Cached beside the video."""
    cache = video.with_suffix(".transcript.json")
    if cache.exists():
        return json.loads(cache.read_text())
    segments, _ = _get_model().transcribe(str(video), word_timestamps=True, vad_filter=True)
    out = []
    for s in segments:
        out.append({
            "start": s.start, "end": s.end, "text": s.text.strip(),
            "words": [{"start": w.start, "end": w.end, "word": w.word.strip()} for w in (s.words or [])],
        })
    cache.write_text(json.dumps(out))
    return out
