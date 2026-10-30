"""FastAPI app: campaigns (brief + settings), content items (one video each), generation, clip review."""
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import assets, briefs, config, edits, qa, store, youtube
from .pipeline import process_source, process_youtube

app = FastAPI(title="Clip")
store.migrate_all()  # old flat layout -> content items (backs up campaign.json first)


@app.middleware("http")
async def no_stale_cache(request, call_next):
    """Always revalidate pages, scripts and clips: replaced clips reuse their file names."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


jobs: dict[str, dict] = {}


def _campaign(cid: str) -> dict:
    try:
        return store.get(cid)
    except KeyError:
        raise HTTPException(404, "Campaign not found")


def _content(cid: str, xid: str) -> dict:
    try:
        return store.get_content(cid, xid)
    except KeyError:
        raise HTTPException(404, "Content not found")


# ---------------- campaigns ----------------

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
    if any(j["cid"] == cid and j["state"] == "running" for j in jobs.values()):
        raise HTTPException(409, "A job is running for this campaign")
    store.delete(cid)
    return {"ok": True}


# ---------------- paths ----------------

def _safe_rel(raw: str) -> str:
    """Relative upload path: no traversal, no hidden files, plain characters only."""
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        raise HTTPException(400, "Bad path")
    return "/".join(re.sub(r"[^\w .\-()&,'+@]", "_", p)[:120] for p in parts)


def _inside(cid: str, sub: str, rel: str) -> Path:
    root = (store.cdir(cid) / sub).resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise HTTPException(400, "Bad path")
    return path


def _asset_file(cid: str, rel: str) -> Path:
    return _inside(cid, "assets", rel)


def _content_file(cid: str, rel: str) -> Path:
    """A content item's video: 'content/<id>/name.mp4' (new) or 'assets/name.mp4' (migrated)."""
    top, _, rest = rel.partition("/")
    if top not in ("content", "assets") or not rest:
        raise HTTPException(400, "Bad path")
    return _inside(cid, top, rest)


# ---------------- shared assets: brief files, logo, music ----------------

@app.post("/api/campaigns/{cid}/assets")
async def upload_asset(cid: str, file: UploadFile, path: str = Form("")):
    """Brief documents, logos and music. A video dropped here becomes its own content item."""
    _campaign(cid)
    rel = _safe_rel(path or file.filename or "file")
    dest = _asset_file(cid, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    info = assets.classify(dest, rel)
    if info["kind"] == "video":
        item = store.create_content(cid, Path(rel).stem.replace("_", " "), {
            "kind": "video", "file": f"assets/{rel}", "duration": info["duration"],
            "width": info["width"], "height": info["height"], "size": info["size"]})
        return {**info, "content": item}
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


# ---------------- content items ----------------

@app.post("/api/campaigns/{cid}/content")
def create_content(cid: str, body: dict = Body(...)):
    _campaign(cid)
    return store.create_content(cid, str(body.get("title", "")))


@app.patch("/api/campaigns/{cid}/content/{xid}")
def rename_content(cid: str, xid: str, body: dict = Body(...)):
    _content(cid, xid)
    if not str(body.get("title", "")).strip():
        raise HTTPException(400, "Title required")
    return store.update_content(cid, xid, {"title": body["title"]})


@app.delete("/api/campaigns/{cid}/content/{xid}")
def delete_content(cid: str, xid: str):
    item = _content(cid, xid)
    if any(j["cid"] == cid and j.get("xid") == xid and j["state"] == "running" for j in jobs.values()):
        raise HTTPException(409, "Generation is running for this content")
    for c in item["clips"]:  # the clips and their caption files
        for ext in (".mp4", ".txt"):
            (store.cdir(cid) / "clips" / Path(c["file"]).with_suffix(ext).name).unlink(missing_ok=True)
    if item.get("file"):
        try:
            _content_file(cid, item["file"]).unlink(missing_ok=True)
        except HTTPException:
            pass
    shutil.rmtree(store.cdir(cid) / "content" / xid, ignore_errors=True)
    store.delete_content(cid, xid)
    return {"ok": True}


def _default_title(item: dict) -> bool:
    return item["title"] == "Untitled content"


@app.post("/api/campaigns/{cid}/content/{xid}/youtube")
def content_youtube(cid: str, xid: str, body: dict = Body(...)):
    item = _content(cid, xid)
    if item.get("kind"):
        raise HTTPException(409, "This content already has a source. Add a new content item instead.")
    try:
        vid, url = youtube.normalize(body.get("url", ""))
        meta = youtube.info(url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"YouTube lookup failed: {str(e)[:200]}")
    fields = {"kind": "youtube", "url": url, "yt_title": meta["title"], "channel": meta["channel"],
              "license": meta["license"], "duration": float(meta["duration"])}
    if _default_title(item):
        fields["title"] = meta["title"]
    return store.update_content(cid, xid, fields)


@app.post("/api/campaigns/{cid}/content/{xid}/file")
async def content_file(cid: str, xid: str, file: UploadFile):
    item = _content(cid, xid)
    if item.get("kind"):
        raise HTTPException(409, "This content already has a source. Add a new content item instead.")
    name = _safe_rel(file.filename or "video.mp4").split("/")[-1]
    if assets.kind_of(Path(name)) != "video":
        raise HTTPException(400, "That isn't a video file (mp4, mov, mkv, webm, m4v, avi).")
    rel = f"content/{xid}/{name}"
    dest = _content_file(cid, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    info = assets.classify(dest, name)
    fields = {"kind": "video", "file": rel, "duration": info["duration"], "width": info["width"],
              "height": info["height"], "size": info["size"]}
    if _default_title(item):
        fields["title"] = Path(name).stem.replace("_", " ")
    return store.update_content(cid, xid, fields)


# ---------------- jobs (one at a time: the server has two cores) ----------------

def _start_job(cid: str, xid: str, title: str, kind: str, fn) -> dict:
    busy = next((j for j in jobs.values() if j["state"] == "running"), None)
    if busy:
        raise HTTPException(409, f"Another job is running ({busy.get('title', 'content')}). Wait for it to finish.")
    job_id = uuid.uuid4().hex[:8]
    job = jobs[job_id] = {"id": job_id, "cid": cid, "xid": xid, "title": title, "kind": kind, "state": "running",
                          "log": [], "percent": None, "started": time.time(), "finished": None}

    def log(msg: str, pct=None) -> None:
        t = int(time.time() - job["started"])
        job["log"].append(f"[{t // 60:02d}:{t % 60:02d}] {msg}")
        job["percent"] = pct

    def run() -> None:
        try:
            fn(log, lambda p: job.__setitem__("percent", p))
            job["state"] = "done"
        except Exception as e:  # surface any failure in the UI
            job["state"], job["error"] = "error", str(e)
            log(f"ERROR: {e}")
        job["finished"] = time.time()

    threading.Thread(target=run, daemon=True).start()
    return {"job": job_id}


def _generate_fn(cid: str, xid: str):
    def run(log, pct):
        campaign, item = store.get(cid), store.get_content(cid, xid)
        pick = lambda role: next((_asset_file(cid, a["path"]) for a in campaign["assets"] if a["role"] == role
                                  and a["kind"] == ("audio" if role == "music" else "image")), None)
        logo, music = pick("logo"), pick("music")
        out_dir, existing = store.cdir(cid) / "clips", item["clips"]
        log(f"Content: {item['title']}" + (f" ({len(existing)} clips already made)" if existing else ""))
        if item["kind"] == "youtube":
            vid, url = youtube.normalize(item["url"])
            clips = process_youtube(url, vid, item.get("yt_title") or item["title"], store.cdir(cid) / "_work",
                                    out_dir, campaign, progress=log, logo=logo, music=music, existing=existing)
        elif item["kind"] == "video":
            src = _content_file(cid, item["file"])
            clips = process_source(src, f"{xid}_{src.stem}", out_dir, campaign, progress=log,
                                   logo=logo, music=music, existing=existing)
        else:
            raise RuntimeError("Add a video or YouTube link to this content first.")
        for c in clips:
            c["content"] = xid
        store.add_clips(cid, xid, clips)
        log(f"Finished: {len(clips)} new clips.", 100)
    return run


@app.post("/api/campaigns/{cid}/content/{xid}/generate")
def generate(cid: str, xid: str):
    item = _content(cid, xid)
    if not item.get("kind"):
        raise HTTPException(400, "Add a video or YouTube link first.")
    return _start_job(cid, xid, item["title"], "generate", _generate_fn(cid, xid))


def _view(job: dict) -> dict:
    return {**job, "elapsed": (job["finished"] or time.time()) - job["started"]}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    return _view(jobs[job_id])


@app.get("/api/campaigns/{cid}/content/{xid}/job")
def latest_job(cid: str, xid: str):
    mine = [j for j in jobs.values() if j["cid"] == cid and j.get("xid") == xid]
    return _view(max(mine, key=lambda j: j["started"])) if mine else {}


@app.get("/api/running")
def running():
    """Which content (if any) is being processed, so the sidebar can show it."""
    j = next((j for j in jobs.values() if j["state"] == "running"), None)
    return {"cid": j["cid"], "xid": j["xid"]} if j else {}


# ---------------- editor: preview copy, AI suggestions, export, batch ----------------

def _clip(cid: str, xid: str, file: str) -> tuple[dict, dict]:
    item = _content(cid, xid)
    try:
        return item, edits.clip_of(item, Path(file).name)
    except KeyError:
        raise HTTPException(404, "Clip not found")


@app.get("/api/campaigns/{cid}/content/{xid}/clips/{file}/editor")
def editor_get(cid: str, xid: str, file: str):
    item, clip = _clip(cid, xid, file)
    if not item.get("kind"):
        raise HTTPException(400, "This content has no source video to edit.")
    try:
        return edits.editor_info(cid, item, clip, _campaign(cid))
    except Exception as e:
        raise HTTPException(502, f"Couldn't load the editor data: {str(e)[:200]}")


@app.post("/api/campaigns/{cid}/content/{xid}/clips/{file}/editor/prepare")
def editor_prepare(cid: str, xid: str, file: str):
    item, clip = _clip(cid, xid, file)
    lo, hi = edits.editor_range(item, clip)
    if edits.proxy_ready(cid, clip, lo, hi):
        return {"ready": True}
    return _start_job(cid, xid, f"Preview of {clip['title']}", "proxy",
                      lambda log, pct: (edits.ensure_proxy(cid, item, clip, log, pct), log("Preview ready.", 100)))


@app.post("/api/campaigns/{cid}/content/{xid}/clips/{file}/editor/suggest")
def editor_suggest(cid: str, xid: str, file: str, body: dict = Body(...)):
    item, clip = _clip(cid, xid, file)
    try:
        return edits.propose(cid, item, clip, list(body.get("modes", [])), _campaign(cid))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Suggestion failed: {str(e)[:200]}")


@app.post("/api/campaigns/{cid}/content/{xid}/clips/{file}/editor/export")
def editor_export(cid: str, xid: str, file: str, body: dict = Body(...)):
    item, clip = _clip(cid, xid, file)
    lo, hi = edits.editor_range(item, clip)
    try:
        edl = edits.clean_edl(body.get("edl") or {}, lo, hi)  # fail fast, before queuing a render
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e))
    return _start_job(cid, xid, f"Export of {clip['title']}", "export",
                      lambda log, pct: edits.export_edl(cid, xid, clip["file"], edl, log, pct))


@app.post("/api/campaigns/{cid}/content/{xid}/batch-edit")
def batch_edit(cid: str, xid: str, body: dict = Body(...)):
    item = _content(cid, xid)
    files = [Path(f).name for f in body.get("files", [])][:30]
    modes = [m for m in body.get("modes", []) if m in edits.TAGS]
    have = {c["file"] for c in item["clips"]}
    if not files or any(f not in have for f in files):
        raise HTTPException(400, "Pick one or more clips.")
    if not modes or {"hook", "loop"} <= set(modes):
        raise HTTPException(400, "Choose a style (Punchline first or Seamless loop, plus an optional keyword).")
    return _start_job(cid, xid, f"Edit {len(files)} clips", "batch",
                      lambda log, pct: (edits.batch_edit(cid, xid, files, modes, log, pct), log("All done.", 100)))


@app.get("/editorfile/{cid}/{name}/proxy.mp4")
def editor_proxy(cid: str, name: str):
    try:
        path = store.cdir(cid) / "_work" / "edit" / re.sub(r"[^\w\-]", "_", name) / "proxy.mp4"
    except KeyError:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


# ---------------- pre-submission checks ----------------

@app.post("/api/campaigns/{cid}/checks/generate")
def checks_generate(cid: str):
    c = _campaign(cid)
    try:
        checks = qa.generate_checks(c)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Couldn't build the checklist: {str(e)[:200]}")
    store.set_checks(cid, checks)
    return store.get(cid)["checks"]


@app.put("/api/campaigns/{cid}/checks")
def checks_save(cid: str, body: dict = Body(...)):
    c = _campaign(cid)
    store.set_checks(cid, qa.clean_checks(body, c))
    return store.get(cid)["checks"]


@app.post("/api/campaigns/{cid}/content/{xid}/clips/{file}/qa")
def clip_qa(cid: str, xid: str, file: str):
    _, clip = _clip(cid, xid, file)
    try:
        return qa.run_qa(cid, xid, clip["file"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"The check failed: {str(e)[:200]}")


@app.post("/api/campaigns/{cid}/content/{xid}/qa-all")
def qa_all(cid: str, xid: str, body: dict = Body(default={})):
    item = _content(cid, xid)
    have = {c["file"] for c in item["clips"]}
    files = [Path(f).name for f in body.get("files", [])] or [c["file"] for c in item["clips"]]
    files = [f for f in files if f in have]
    if not files:
        raise HTTPException(400, "No clips to check.")

    def run(log, pct):
        for n, f in enumerate(files, 1):
            log(f"Checking {n}/{len(files)}: {f}", round((n - 1) / len(files) * 100))
            try:
                r = qa.run_qa(cid, xid, f)
                bad = [i["label"] for i in r["items"] if i["status"] in ("fail", "warn")]
                log(f"  {r['verdict'].upper()}" + (f": {'; '.join(bad)[:140]}" if bad else ""))
            except Exception as e:
                log(f"  Skipped: {str(e)[:140]}")
        log("All done.", 100)
    return _start_job(cid, xid, f"Check {len(files)} clips", "qa", run)


# ---------------- clips ----------------

@app.patch("/api/campaigns/{cid}/content/{xid}/clips/{filename}")
def patch_clip(cid: str, xid: str, filename: str, body: dict = Body(...)):
    if "draft" in body and len(str(body["draft"])) > 20000:
        raise HTTPException(413, "Draft too large")
    try:
        return store.update_clip(cid, xid, filename, body)
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
