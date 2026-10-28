"""python -m clip.cli video.mp4 --out clips/ [--rules "..."] [--tags "#a #b"]"""
import argparse
from pathlib import Path

from .pipeline import process_source


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--name", default="test")
    ap.add_argument("--rules", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--logo", type=Path)
    ap.add_argument("--music", type=Path)
    a = ap.parse_args()
    campaign = {"name": a.name, "rules": a.rules, "hashtags": a.tags.split(),
                "clips_per_video": a.count}
    for c in process_source(a.video, a.video.name, a.out, campaign, logo=a.logo, music=a.music):
        print(f"{c['file']}  {c['start']:.1f}-{c['end']:.1f}s  {c['title']}")


if __name__ == "__main__":
    main()
