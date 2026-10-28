# Clip

Local web app for turning long videos into captioned vertical clips for pay-per-view clipping campaigns.

Add a campaign in the sidebar, drop in its brief and assets, and hit **Generate**.

**Brief:** drop a PDF / Word / text file, or paste a link-shared Google Docs URL. Claude reads it and pre-fills the rules, hashtags, disclosure tag, lengths and rate for you to check and save.

**Assets:** drop whole folders. Files are sorted by type and given a role you can change:
`source` (clipped), `logo` (watermark on every clip), `music` (mixed under the audio), `brief` (read for rules), `image` / `ignore` (unused).
Videos with speech go through transcript-based highlighting; videos with little speech (trailers, cinematics) are cut at scene changes instead.

Each source video is:

1. transcribed locally (faster-whisper, word timestamps)
2. scanned by Claude for the best 15-60s moments, using the campaign's rules
3. cut, reframed to 9:16 (blurred-background fit or center crop) and given word-highlighted captions (ffmpeg)
4. exported with a ready-to-paste caption (hashtags + disclosure tag)

You review, approve, post manually, and log payouts per clip. Nothing is auto-posted.

## Setup

```sh
brew install ffmpeg-full          # needs libass for captions (Linux: apt install ffmpeg)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env              # add ANTHROPIC_API_KEY
.venv/bin/uvicorn clip.server:app --port 8765
```

Open http://localhost:8765. Without an API key it falls back to evenly spaced windows.

CLI (no UI): `.venv/bin/python -m clip.cli video.mp4 --out out/ --tags "#gaming"`

## Notes

- Data lives in `CLIP_DATA_DIR` (default `./data`, git-ignored): footage, clips and `campaign.json` per campaign.
- There is no login. Run it on a trusted network only.
- Only clip footage a campaign authorises, and keep the paid-partnership disclosure on posts.
