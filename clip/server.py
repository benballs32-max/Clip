"""FastAPI app: campaigns, uploads, background generation, clip review."""
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import assets, briefs, config, store, youtube
from .pipeline import process_source, process_youtube

app = FastAPI(title="Clip")
jobs: dict[str, dict] = {}


def _campaign(cid: str) -> dict:
    try:
        return store.get(cid)
    except KeyError:
        raise HTTPException(404, "Campaign not found")


@app.get("/api/campaigns")
def list_campaigns():
    return store.list_all()


@app.post("/api/campaigns")
def create_campaign(body: dict = Body(...)):
    if not str(body.get("name", "")).strip():
        raise HTTPException(400, "Name required")
    return store.create(body)


@app.get("/api/campaigns/{cid}")
def get_campaign(cid: str):
    return _campaign(cid)


@app.patch("/api/campaigns/{cid}")
def patch_campaign(cid: str, body: dict = Body(...)):
    _campaign(cid)
    return store.update(cid, body)


@app.delete("/api/campaigns/{cid}")
def delete_campaign(cid: str):
    _campaign(cid)
    store.delete(cid)
    return {"ok": True}


def _safe_rel(raw: str) -> str:
    """Relative upload path: no traversal, no hidden files, plain characters only."""
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        raise HTTPException(400, "Bad path")
    return "/".join(re.sub(r"[^\w .\-()&,'+@]", "_", p)[:120] for p in parts)


def _asset_file(cid: str, rel: str) -> Path:
    root = (store.cdir(cid) / "assets").resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise HTTPException(400, "Bad path")
    return path


@app.post("/api/campaigns/{cid}/assets")
async def upload_asset(cid: str, file: UploadFile, path: str = Form("")):
    _campaign(cid)
    rel = _safe_rel(path or file.filename or "file")
    dest = _asset_file(cid, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    info = assets.classify(dest, rel)
    store.upsert_asset(cid, info)
    return info


@app.patch("/api/campaigns/{cid}/assets")
def patch_asset(cid: str, body: dict = Body(...)):
    if body.get("role") not in assets.ROLES:
        raise HTTPException(400, "Bad role")
    try:
        store.set_asset_role(cid, body.get("path", ""), body["role"])
    except KeyError:
        raise HTTPException(404)
    return {"ok": True}


@app.delete("/api/campaigns/{cid}/assets")
def delete_asset(cid: str, path: str):
    _campaign(cid)
    _asset_file(cid, path).unlink(missing_ok=True)
    store.remove_asset(cid, path)
    return {"ok": True}


@app.post("/api/campaigns/{cid}/brief/gdoc")
def brief_from_gdoc(cid: str, body: dict = Body(...)):
    _campaign(cid)
    try:
        text = briefs.fetch_gdoc(body.get("url", ""))
    except Exception as e:
        raise HTTPException(400, str(e))
    rel = "Google Doc brief.txt"
    dest = _asset_file(cid, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    store.upsert_asset(cid, assets.classify(dest, rel))
    return {"ok": True}


@app.post("/api/campaigns/{cid}/brief/analyze")
def analyze_brief(cid: str):
    c = _campaign(cid)
    texts = []
    for a in c["assets"]:
        if a["role"] == "brief" and a["kind"] == "doc":
            try:
                texts.append(f"## {a['path']}\n" + briefs.extract_text(_asset_file(cid, a["path"])))
            except Exception as e:
                texts.append(f"## {a['path']}\n(could not read: {e})")
    if not texts:
        raise HTTPException(400, "No brief documents uploaded")
    text = "\n\n".join(texts)[:briefs.MAX_CHARS]
    store.set_brief_text(cid, text)
    try:
        return briefs.analyze(text)
    except Exception as e:
        raise HTTPException(502, f"Claude couldn't read the brief: {e}")


@app.post("/api/campaigns/{cid}/youtube")
def add_youtube(cid: str, body: dict = Body(...)):
    _campaign(cid)
    try:
        vid, url = youtube.normalize(body.get("url", ""))
        meta = youtube.info(url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"YouTube lookup failed: {str(e)[:200]}")
    asset = {"path": f"youtube:{vid}", "kind": "youtube", "role": "source", "url": url,
             "title": meta["title"], "channel": meta["channel"], "license": meta["license"],
             "duration": float(meta["duration"]), "width": 0, "height": 0, "alpha": False, "size": 0}
    store.upsert_asset(cid, asset)
    return asset


def _run_job(job_id: str, cid: str) -> None:
    job = jobs[job_id]
    try:
        campaign = store.get(cid)
        pick = lambda role: next((_asset_file(cid, a["path"]) for a in campaign["assets"] if a["role"] == role
                                  and a["kind"] == ("audio" if role == "music" else "image")), None)
        logo, music = pick("logo"), pick("music")
        todo = [a for a in campaign["assets"] if a["kind"] in ("video", "youtube") and a["role"] == "source"
                and a["path"] not in campaign["sources_done"]]
        if not todo:
            job["log"].append("No new source videos to process.")
        log = lambda m: job["log"].append(m)
        for a in todo:
            if a["kind"] == "youtube":
                clips = process_youtube(a["url"], a["path"].split(":", 1)[1], a["title"],
                                        store.cdir(cid) / "_work", store.cdir(cid) / "clips",
                                        campaign, progress=log, logo=logo, music=music)
                store.add_clips(cid, a["path"], clips)
                continue
            clips = process_source(_asset_file(cid, a["path"]), a["path"], store.cdir(cid) / "clips",
                                   campaign, progress=lambda m: job["log"].append(m),
                                   logo=logo, music=music)
            store.add_clips(cid, a["path"], clips)
        job["state"] = "done"
    except Exception as e:  # surface any failure in the UI
        job["state"], job["error"] = "error", str(e)
        job["log"].append(f"Error: {e}")


@app.post("/api/campaigns/{cid}/generate")
def generate(cid: str):
    _campaign(cid)
    if any(j["cid"] == cid and j["state"] == "running" for j in jobs.values()):
        raise HTTPException(409, "Already running")
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {"cid": cid, "state": "running", "log": [], "started": time.time()}
    threading.Thread(target=_run_job, args=(job_id, cid), daemon=True).start()
    return {"job": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    return jobs[job_id]


@app.patch("/api/campaigns/{cid}/clips/{filename}")
def patch_clip(cid: str, filename: str, body: dict = Body(...)):
    try:
        return store.update_clip(cid, filename, body)
    except KeyError:
        raise HTTPException(404)


@app.get("/media/{cid}/{filename}")
def media(cid: str, filename: str):
    try:
        path = store.cdir(cid) / "clips" / Path(filename).name
    except KeyError:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


app.mount("/", StaticFiles(directory=config.ROOT / "web", html=True), name="web")
