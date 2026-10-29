"""Settings read from environment (.env is loaded if present)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

DATA_DIR = Path(os.environ.get("CLIP_DATA_DIR", ROOT / "data")).resolve()
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
CLAUDE_MODEL = os.environ.get("CLIP_MODEL", "claude-haiku-5-5")  # first-pass scan, brief reading
REFINE_MODEL = os.environ.get("CLIP_REFINE_MODEL", "claude-sonnet-5-5")  # final cuts; "none" disables
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DATA_DIR.mkdir(parents=True, exist_ok=True)
