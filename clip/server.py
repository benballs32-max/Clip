"""FastAPI app: campaigns, uploads, background generation, clip review."""
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, store
from .pipeline import VIDEO_EXT, process_source

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
    c = _campaign(cid)
    c["sources"] = sorted(p.name for p in (store.cdir(cid) / "sources").iterdir()
                          if p.suffix.lower() in VIDEO_EXT)
    return c


@app.patch("/api/campaigns/{cid}")
def patch_campaign(cid: str, body: dict = Body(...)):
    _campaign(cid)
    return store.update(cid, body)


@app.delete("/api/campaigns/{cid}")
def delete_campaign(cid: str):
    _campaign(cid)
    store.delete(cid)
    return {"ok": True}


@app.post("/api/campaigns/{cid}/sources")
async def upload_source(cid: str, file: UploadFile):
    _campaign(cid)
    name = re.sub(r"[^\w.\- ]", "_", Path(file.filename or "video.mp4").name)
    if Path(name).suffix.lower() not in VIDEO_EXT:
        raise HTTPException(400, "Unsupported file type")
    with open(store.cdir(cid) / "sources" / name, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"name": name}


def _run_job(job_id: str, cid: str) -> None:
    job = jobs[job_id]
    try:
        campaign = store.get(cid)
        todo = [p for p in sorted((store.cdir(cid) / "sources").iterdir())
                if p.suffix.lower() in VIDEO_EXT and p.name not in campaign["sources_done"]]
        if not todo:
            job["log"].append("No new source videos to process.")
        for src in todo:
            clips = process_source(src, store.cdir(cid) / "clips", campaign,
                                   progress=lambda m: job["log"].append(m))
            store.add_clips(cid, src.name, clips)
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
