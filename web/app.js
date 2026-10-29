const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const api = async (url, opts = {}) => {
  const r = await fetch(url, opts.body && !(opts.body instanceof FormData)
    ? { ...opts, headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.body) } : opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
let current = null;

async function loadList() {
  const list = await api("/api/campaigns");
  $("#campaigns").innerHTML = list.map(c =>
    `<a data-id="${c.id}" class="${c.id === current ? "on" : ""}">${esc(c.name)}<small>${esc(c.platform)} · $${c.rate_cpm}/1k</small></a>`).join("");
  document.querySelectorAll("nav a").forEach(a => a.onclick = () => open(a.dataset.id));
  return list;
}

$("#new").onclick = async () => {
  const name = prompt("Campaign name?");
  if (!name) return;
  const c = await api("/api/campaigns", { method: "POST", body: { name } });
  await open(c.id);
};

async function open(id) {
  current = id;
  await loadList();
  const c = await api(`/api/campaigns/${id}`);
  const clips = c.clips;
  const briefs = c.assets.filter(a => a.role === "brief");
  const total = clips.reduce((a, x) => a + (+x.payout || 0), 0);
  const count = s => clips.filter(x => x.status === s).length;
  $("#main").innerHTML = `<div class="cols"><div class="left">
    <h2>${esc(c.name)}</h2>
    <div class="stats"><b>${clips.length}</b> clips · <b>${count("approved")}</b> approved · <b>${count("posted")}</b> posted · <b>$${total.toFixed(2)}</b> earned</div>
    <h3>Campaign brief</h3>
    <div class="drop" id="briefDrop">Drop the brief here (PDF, Word, text) or <u>click to choose</u><input id="briefFile" type="file" accept=".pdf,.docx,.txt,.md" hidden></div>
    <div class="row"><input id="gdoc" placeholder="…or paste a Google Docs link (shared with 'anyone with the link')" style="flex:1;max-width:560px"><button id="gdocBtn">Read link</button>${briefs.length ? '<button id="reread">Re-read brief</button>' : ""}</div>
    <div class="stats" id="briefMsg">${briefs.length ? "Brief files: " + briefs.map(a => esc(a.path)).join(", ") : ""}</div>
    <form id="f" class="grid">
      <label>Name<input name="name" value="${esc(c.name)}"></label>
      <label>Platform<select name="platform">${["TikTok","Instagram","YouTube Shorts","Any"].map(p => `<option ${p===c.platform?"selected":""}>${p}</option>`).join("")}</select></label>
      <label>Rate ($ per 1,000 views)<input name="rate_cpm" type="number" step="0.01" value="${c.rate_cpm}"></label>
      <label>Budget / payout notes<input name="budget_note" value="${esc(c.budget_note)}"></label>
      <label class="full">Rules / brief (Claude uses this to pick clips)<textarea name="rules" style="min-height:130px">${esc(c.rules)}</textarea></label>
      <label>Hashtags (space separated)<input name="hashtags" value="${esc(c.hashtags.join(" "))}"></label>
      <label>Disclosure tag<input name="disclosure" value="${esc(c.disclosure)}"></label>
      <label>Min seconds<input name="min_seconds" type="number" value="${c.min_seconds}"></label>
      <label>Max seconds<input name="max_seconds" type="number" value="${c.max_seconds}"></label>
      <label>Max clips per video<input name="clips_per_video" type="number" value="${c.clips_per_video}"></label>
      <label>Framing<select name="frame_mode"><option value="blur" ${c.frame_mode==="blur"?"selected":""}>Fit + blurred background</option><option value="fill" ${c.frame_mode==="fill"?"selected":""}>Fill (center crop)</option></select></label>
      <label>Logo position<select name="logo_position">${[["tr","Top right"],["tl","Top left"],["br","Bottom right"],["bl","Bottom left"],["off","Off"]].map(([v,t]) => `<option value="${v}" ${v===c.logo_position?"selected":""}>${t}</option>`).join("")}</select></label>
      <label>Logo size (fraction of width)<input name="logo_scale" type="number" step="0.01" min="0.05" max="0.6" value="${c.logo_scale}"></label>
      <label>Music volume (0 to 1)<input name="music_volume" type="number" step="0.01" min="0" max="1" value="${c.music_volume}"></label>
      <div class="full row"><button class="primary">Save</button><button type="button" id="del" class="danger">Delete campaign</button></div>
    </form>
    <h3>Assets</h3>
    <div class="drop" id="drop">Drop a <b>folder</b> or files here: footage, trailers, logos, music, briefs<div class="row" style="justify-content:center"><button type="button" id="pickDir">Choose folder</button><button type="button" id="pickFiles">Choose files</button></div><input id="dirIn" type="file" webkitdirectory multiple hidden><input id="fileIn" type="file" multiple hidden></div>
    <div class="row"><input id="yt" placeholder="…or paste a YouTube link (only videos the campaign lets you use)" style="flex:1;max-width:560px"><button id="ytBtn">Add video</button></div>
    <div class="stats" id="status"></div>
    ${assetTable(c)}
    <div class="row"><button class="primary" id="gen">Generate clips</button>${c.sources_done.length ? '<button id="more" title="Re-scan videos already processed and add new clips that don\'t overlap the ones you have">Find more clips</button>' : ""}</div>
    <h3>Clips</h3>
    <div class="clips">${clips.map(clipCard(id)).join("") || '<p class="empty">Nothing generated yet.</p>'}</div>
    </div>
    <aside class="activity"><h3 style="margin-top:0">Activity <span id="pill" class="pill"></span></h3>
      <div class="bar" id="barwrap" hidden><div id="bar"></div></div>
      <div class="stats" id="elapsed"></div>
      <pre id="log" class="log">Nothing running. Press Generate to start.</pre></aside></div>`;
  bind(c);
  attachJob(c.id, false);
}

const clipCard = id => x => `
  <div class="clip ${x.status}" data-file="${esc(x.file)}">
    <video src="/media/${id}/${encodeURIComponent(x.file)}?v=${Math.round(x.start * 100)}-${Math.round(x.end * 100)}" controls preload="metadata"></video>
    <b>${esc(x.title)}</b><p>${esc(x.caption)}</p>
    <div class="row">
      <select class="st">${["new","approved","rejected","posted"].map(s => `<option ${s===x.status?"selected":""}>${s}</option>`).join("")}</select>
      <input class="pay" type="number" step="0.01" placeholder="$ paid" value="${x.payout || ""}" style="width:80px">
    </div>
    <div class="row"><button class="cp">Copy caption</button><a href="/media/${id}/${encodeURIComponent(x.file)}" download><button>Download</button></a></div>
  </div>`;

function bind(c) {
  $("#f").onsubmit = async e => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    d.hashtags = d.hashtags.split(/\s+/).filter(Boolean);
    ["rate_cpm"].forEach(k => d[k] = parseFloat(d[k]) || 0);
    ["min_seconds","max_seconds","clips_per_video"].forEach(k => d[k] = parseInt(d[k]) || 0);
    await api(`/api/campaigns/${c.id}`, { method: "PATCH", body: d });
    await open(c.id);
  };
  $("#del").onclick = async () => {
    if (!confirm(`Delete "${c.name}" and all its footage and clips?`)) return;
    await api(`/api/campaigns/${c.id}`, { method: "DELETE" });
    current = null; await loadList(); $("#main").innerHTML = '<p class="empty">Campaign deleted.</p>';
  };
  const drop = $("#drop");
  $("#pickDir").onclick = () => $("#dirIn").click();
  $("#pickFiles").onclick = () => $("#fileIn").click();
  $("#dirIn").onchange = e => upload(c.id, [...e.target.files].map(f => ({ file: f, path: f.webkitRelativePath || f.name })));
  $("#fileIn").onchange = e => upload(c.id, [...e.target.files].map(f => ({ file: f, path: f.name })));
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = async e => { e.preventDefault(); drop.classList.remove("over"); upload(c.id, await collect(e.dataTransfer.items)); };
  const bd = $("#briefDrop"), bf = $("#briefFile");
  bd.onclick = () => bf.click();
  bd.ondragover = e => { e.preventDefault(); bd.classList.add("over"); };
  bd.ondragleave = () => bd.classList.remove("over");
  bd.ondrop = async e => { e.preventDefault(); bd.classList.remove("over"); upload(c.id, (await collect(e.dataTransfer.items)).slice(0, 1), true); };
  bf.onchange = () => upload(c.id, [...bf.files].map(f => ({ file: f, path: f.name })), true);
  $("#gdocBtn").onclick = async () => {
    const msg = $("#briefMsg");
    try {
      await api(`/api/campaigns/${c.id}/brief/gdoc`, { method: "POST", body: { url: $("#gdoc").value } });
      await open(c.id); await analyze(c.id);
    } catch (e) { msg.textContent = e.message; }
  };
  $("#ytBtn").onclick = async () => {
    const st = $("#status"), btn = $("#ytBtn");
    btn.disabled = true; st.textContent = "Looking up video…";
    try {
      await api(`/api/campaigns/${c.id}/youtube`, { method: "POST", body: { url: $("#yt").value } });
      await open(c.id);
    } catch (e) { st.textContent = e.message; btn.disabled = false; }
  };
  if ($("#reread")) $("#reread").onclick = () => analyze(c.id);
  document.querySelectorAll(".role").forEach(sel => sel.onchange = async () => {
    await api(`/api/campaigns/${c.id}/assets`, { method: "PATCH", body: { path: sel.dataset.path, role: sel.value } });
    open(c.id);
  });
  document.querySelectorAll(".rm").forEach(b => b.onclick = async () => {
    await api(`/api/campaigns/${c.id}/assets?path=${encodeURIComponent(b.dataset.path)}`, { method: "DELETE" });
    open(c.id);
  });
  $("#gen").onclick = () => generate(c.id);
  if ($("#more")) $("#more").onclick = () => generate(c.id, true);
  document.querySelectorAll(".clip").forEach(el => {
    const f = encodeURIComponent(el.dataset.file), patch = body =>
      api(`/api/campaigns/${c.id}/clips/${f}`, { method: "PATCH", body });
    $(".st", el).onchange = async e => { await patch({ status: e.target.value }); el.className = `clip ${e.target.value}`; };
    $(".pay", el).onchange = e => patch({ payout: parseFloat(e.target.value) || 0 });
    $(".cp", el).onclick = () => navigator.clipboard.writeText($("p", el).textContent);
  });
}

const ROLES = ["source", "logo", "music", "image", "brief", "ignore"];
const ICON = { youtube: "▶️", video: "🎞", image: "🖼", audio: "🎵", doc: "📄", other: "❔" };
const fmt = n => n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.ceil(n / 1e3) + " KB";
const dur = t => t >= 60 ? `${Math.floor(t / 60)}m${Math.round(t % 60)}s` : `${Math.round(t)}s`;

function assetTable(c) {
  if (!c.assets.length) return '<p class="empty">No assets yet. Drop a folder above.</p>';
  const rows = c.assets.map(a => {
    const detail = [a.duration ? dur(a.duration) : "", a.width ? `${a.width}×${a.height}` : "", a.alpha ? "transparent" : "", fmt(a.size)].filter(Boolean).join(" · ");
    const done = c.sources_done.includes(a.path) ? " ✓" : "";
    const label = a.kind === "youtube" ? `${esc(a.title)} <span class="stats">(${esc(a.channel)})</span>` : esc(a.path);
    return `<tr><td>${ICON[a.kind]} ${label}${done}</td><td class="stats">${detail}</td>
      <td><select class="role" data-path="${esc(a.path)}">${ROLES.map(r => `<option ${r===a.role?"selected":""}>${r}</option>`).join("")}</select></td>
      <td><button class="rm danger" data-path="${esc(a.path)}">✕</button></td></tr>`;
  }).join("");
  return `<table class="assets"><tbody>${rows}</tbody></table>
    <p class="stats">source = clipped (speech → highlights, little speech → scene cuts) · logo = watermark · music = background track · brief = read for rules · image/ignore = unused</p>`;
}

// Walk dropped folders recursively (works for files too).
async function collect(items) {
  const out = [];
  const walk = async (entry, prefix) => {
    if (entry.isFile) await new Promise(r => entry.file(f => { out.push({ file: f, path: prefix + f.name }); r(); }));
    else if (entry.isDirectory) {
      const rd = entry.createReader();
      for (;;) {
        const batch = await new Promise(r => rd.readEntries(r));
        if (!batch.length) break;
        for (const e of batch) await walk(e, prefix + entry.name + "/");
      }
    }
  };
  const entries = [...items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  for (const e of entries) await walk(e, "");
  return out;
}

async function upload(id, items, isBrief = false) {
  items = items.filter(i => !i.path.split("/").some(p => p.startsWith(".")));
  const st = $("#status"); let done = 0, sawBrief = false;
  for (const it of items) {
    await new Promise(res => {
      const fd = new FormData(); fd.append("file", it.file); fd.append("path", it.path);
      const x = new XMLHttpRequest();
      x.open("POST", `/api/campaigns/${id}/assets`);
      x.upload.onprogress = e => st.textContent = `Uploading ${done + 1}/${items.length}: ${it.path} ${Math.round(e.loaded / e.total * 100)}%`;
      x.onload = () => { try { sawBrief ||= JSON.parse(x.responseText).role === "brief"; } catch {} done++; res(); };
      x.onerror = () => { st.textContent = `Failed: ${it.path}`; res(); };
      x.send(fd);
    });
  }
  const hadRules = ($("[name=rules]") || {}).value;
  await open(id);
  if (isBrief || (sawBrief && !hadRules)) await analyze(id);
}

async function analyze(id) {
  const msg = $("#briefMsg");
  msg.textContent = "Reading brief…";
  try {
    const r = await api(`/api/campaigns/${id}/brief/analyze`, { method: "POST" });
    const set = (n, v) => { const el = $(`[name=${n}]`); if (el && v !== undefined && v !== null) el.value = v; };
    set("rules", r.rules); set("hashtags", (r.hashtags || []).join(" ")); set("disclosure", r.disclosure);
    set("platform", r.platform); set("rate_cpm", r.rate_cpm); set("min_seconds", r.min_seconds); set("max_seconds", r.max_seconds);
    msg.textContent = "Filled in from the brief" + (r.note ? ` (${r.note})` : "") + ". Check it, then Save.";
  } catch (e) { msg.textContent = e.message; }
}

let jobLoop = 0;
async function attachJob(id, refreshOnDone) {
  const loop = ++jobLoop;  // only the newest loop may update the page
  const btn = $("#gen");
  let was = false;
  for (;;) {
    let j;
    try { j = await api(`/api/campaigns/${id}/job`); } catch { return; }
    if (loop !== jobLoop || current !== id || !$("#log")) return;
    if (!j.id) return;
    const running = j.state === "running";
    const log = $("#log"), stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.textContent = j.log.join("\n") || "Starting…";
    if (stick) log.scrollTop = log.scrollHeight;
    const pill = $("#pill"); pill.textContent = j.state; pill.className = `pill ${j.state}`;
    const m = Math.floor(j.elapsed / 60), sec = Math.floor(j.elapsed % 60);
    $("#elapsed").textContent = `${running ? "Running for" : "Took"} ${m}m ${sec}s`;
    $("#barwrap").hidden = !running;
    $("#barwrap").classList.toggle("indet", j.percent == null);
    $("#bar").style.width = j.percent == null ? "35%" : `${j.percent}%`;
    if (btn) btn.disabled = running;
    if (!running) { if (refreshOnDone || was) await open(id); return; }
    was = true;
    await new Promise(r => setTimeout(r, 1500));
  }
}

async function generate(id, redo = false) {
  try {
    await api(`/api/campaigns/${id}/generate${redo ? "?redo=true" : ""}`, { method: "POST" });
    attachJob(id, true);
  } catch (e) { $("#log").textContent = e.message; }
}

loadList();
