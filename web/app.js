const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const api = async (url, opts = {}) => {
  const r = await fetch(url, opts.body && !(opts.body instanceof FormData)
    ? { ...opts, headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.body) } : opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const tc = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const fmt = n => n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.ceil(n / 1e3) + " KB";
const dur = t => t >= 3600 ? `${Math.floor(t / 3600)}h ${Math.round(t % 3600 / 60)}m` : t >= 60 ? `${Math.floor(t / 60)}m ${Math.round(t % 60)}s` : `${Math.round(t)}s`;
const ROLES = ["logo", "music", "image", "brief", "ignore"];
const ICON = { youtube: "▶️", video: "🎞", image: "🖼", audio: "🎵", doc: "📄", other: "❔" };

let data = [];        // campaigns, each with its content items and their clips
let route = {};       // { cid, xid }
let busy = {};        // which content is generating right now
let jobLoop = 0;
const expanded = new Set(JSON.parse(localStorage.getItem("expanded") || "[]"));
const saveExpanded = () => localStorage.setItem("expanded", JSON.stringify([...expanded]));

// ---------- routing: #/<campaign>[/<content>] ----------
const go = (cid, xid) => { const h = cid ? `#/${cid}${xid ? "/" + xid : ""}` : "#/"; location.hash === h ? render() : (location.hash = h); };
window.addEventListener("hashchange", render);

async function render() {
  [data, busy] = await Promise.all([api("/api/campaigns"), api("/api/running").catch(() => ({}))]);
  const [, cid, xid] = location.hash.split("/");
  route = { cid, xid };
  if (cid) { expanded.add(cid); saveExpanded(); }
  sidebar();
  const c = data.find(c => c.id === cid);
  jobLoop++;  // stop any previous progress loop
  if (!c) return void ($("#main").innerHTML = '<p class="empty">Add a campaign on the left to get started.</p>');
  const x = xid && c.content.find(x => x.id === xid);
  if (xid && !x) return go(cid);
  x ? renderContent(c, x) : renderCampaign(c);
}

// ---------- sidebar ----------
function sidebar() {
  $("#campaigns").innerHTML = data.map(c => {
    const open = expanded.has(c.id), on = route.cid === c.id && !route.xid;
    const items = c.content.map(x => `
      <a class="ci ${route.xid === x.id ? "on" : ""}" href="#/${c.id}/${x.id}" title="${esc(x.title)}">
        <span>${ICON[x.kind] || "◻️"}</span><span class="t">${esc(x.title)}</span>
        ${busy.xid === x.id ? '<span class="dot" title="Generating">●</span>' : ""}
        <small>${x.clips.length || ""}</small></a>`).join("");
    return `<div class="camp">
      <a class="ch ${on ? "on" : ""}" href="#/${c.id}"><button class="caret" data-toggle="${c.id}">${open ? "▾" : "▸"}</button>
        <span class="t">${esc(c.name)}<small>${esc(c.platform)} · $${c.rate_cpm}/1k</small></span></a>
      ${open ? `<div class="items">${items}<button class="addc" data-add="${c.id}">+ Add content</button></div>` : ""}</div>`;
  }).join("") || '<p class="stats">No campaigns yet.</p>';
}

$("#campaigns").onclick = e => {  // campaigns and content are real links (#/cid, #/cid/xid); only the buttons need code
  const t = e.target.closest("[data-toggle]");
  if (t) { e.preventDefault(); const id = t.dataset.toggle; expanded.has(id) ? expanded.delete(id) : expanded.add(id); saveExpanded(); return sidebar(); }
  const add = e.target.closest("[data-add]");
  if (add) addContent(add.dataset.add);
};

$("#new").onclick = async () => {
  const name = prompt("Campaign name?");
  if (!name) return;
  const c = await api("/api/campaigns", { method: "POST", body: { name } });
  go(c.id);
};

async function addContent(cid) {
  const title = prompt("Name this content (e.g. the video title):");
  if (title === null) return;
  const x = await api(`/api/campaigns/${cid}/content`, { method: "POST", body: { title } });
  go(cid, x.id);
}

// ---------- campaign page: brief, settings, shared files ----------
function renderCampaign(c) {
  const briefs = c.assets.filter(a => a.role === "brief");
  const clips = c.content.flatMap(x => x.clips);
  const count = s => clips.filter(x => x.status === s).length;
  const earned = clips.reduce((a, x) => a + (+x.payout || 0), 0);
  $("#main").innerHTML = `<div class="page">
    <h2>${esc(c.name)}</h2>
    <div class="stats"><b>${c.content.length}</b> content · <b>${clips.length}</b> clips · <b>${count("approved")}</b> approved · <b>${count("posted")}</b> posted · <b>$${earned.toFixed(2)}</b> earned</div>

    <h3>Brief</h3>
    <div class="drop" id="briefDrop">Drop the brief here (PDF, Word, text) or <u>click to choose</u><input id="briefFile" type="file" accept=".pdf,.docx,.txt,.md" hidden></div>
    <div class="row"><input id="gdoc" placeholder="…or paste a Google Docs link (shared with 'anyone with the link')" style="flex:1;max-width:560px"><button id="gdocBtn">Read link</button>${briefs.length ? '<button id="reread">Re-read brief</button>' : ""}</div>
    <div class="stats" id="briefMsg">${briefs.length ? "Brief files: " + briefs.map(a => esc(a.path)).join(", ") : ""}</div>

    <h3>Settings</h3>
    <form id="f" class="grid">
      <label>Name<input name="name" value="${esc(c.name)}"></label>
      <label>Platform<select name="platform">${["TikTok","Instagram","YouTube Shorts","Any"].map(p => `<option ${p===c.platform?"selected":""}>${p}</option>`).join("")}</select></label>
      <label>Rate ($ per 1,000 views)<input name="rate_cpm" type="number" step="0.01" value="${c.rate_cpm}"></label>
      <label>Budget / payout notes<input name="budget_note" value="${esc(c.budget_note)}"></label>
      <label class="full">Rules / brief (Claude uses this to pick clips)<textarea name="rules" style="min-height:150px">${esc(c.rules)}</textarea></label>
      <label>Hashtags (space separated)<input name="hashtags" value="${esc(c.hashtags.join(" "))}"></label>
      <label>Disclosure tag<input name="disclosure" value="${esc(c.disclosure)}"></label>
      <label>Min seconds<input name="min_seconds" type="number" value="${c.min_seconds}"></label>
      <label>Max seconds<input name="max_seconds" type="number" value="${c.max_seconds}"></label>
      <label>Max clips per video<input name="clips_per_video" type="number" value="${c.clips_per_video}"></label>
      <label>Framing<select name="frame_mode"><option value="blur" ${c.frame_mode==="blur"?"selected":""}>Fit + blurred background</option><option value="fill" ${c.frame_mode==="fill"?"selected":""}>Fill (center crop)</option></select></label>
      <label>Logo position<select name="logo_position">${[["tr","Top right"],["tl","Top left"],["br","Bottom right"],["bl","Bottom left"],["off","Off"]].map(([v,t]) => `<option value="${v}" ${v===c.logo_position?"selected":""}>${t}</option>`).join("")}</select></label>
      <label>Logo size (fraction of width)<input name="logo_scale" type="number" step="0.01" min="0.05" max="0.6" value="${c.logo_scale}"></label>
      <label>Music volume (0 to 1)<input name="music_volume" type="number" step="0.01" min="0" max="1" value="${c.music_volume}"></label>
      <div class="full row"><button class="primary">Save</button><span id="saved" class="stats"></span></div>
    </form>

    <h3>Logo, music and other files</h3>
    <div class="drop" id="drop">Drop files or a folder here: logo, music, extra brief documents<div class="row" style="justify-content:center"><button type="button" id="pickDir">Choose folder</button><button type="button" id="pickFiles">Choose files</button></div><input id="dirIn" type="file" webkitdirectory multiple hidden><input id="fileIn" type="file" multiple hidden></div>
    <div class="stats" id="status">Videos dropped here become their own content item in the sidebar.</div>
    ${assetTable(c)}

    <h3>Content</h3>
    ${c.content.map(x => `<a class="crow" href="#/${c.id}/${x.id}">${ICON[x.kind] || "◻️"} <b>${esc(x.title)}</b> <span class="stats">${x.kind ? (x.duration ? dur(x.duration) + " · " : "") : "no source yet · "}${x.clips.length} clips</span></a>`).join("") || '<p class="empty">No content yet.</p>'}
    <div class="row"><button class="primary" id="addc">+ Add content</button><button id="del" class="danger" style="margin-left:auto">Delete campaign</button></div>
  </div>`;

  $("#f").onsubmit = async e => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    d.hashtags = d.hashtags.split(/\s+/).filter(Boolean);
    d.rate_cpm = parseFloat(d.rate_cpm) || 0;
    ["min_seconds", "max_seconds", "clips_per_video"].forEach(k => d[k] = parseInt(d[k]) || 0);
    await api(`/api/campaigns/${c.id}`, { method: "PATCH", body: d });
    data = await api("/api/campaigns"); sidebar();
    $("#saved").textContent = "Saved ✓";
  };
  $("#addc").onclick = () => addContent(c.id);
  $("#del").onclick = async () => {
    if (!confirm(`Delete "${c.name}" with all its content and clips?`)) return;
    try { await api(`/api/campaigns/${c.id}`, { method: "DELETE" }); go(); } catch (e) { alert(e.message); }
  };
  const drop = $("#drop");
  $("#pickDir").onclick = () => $("#dirIn").click();
  $("#pickFiles").onclick = () => $("#fileIn").click();
  $("#dirIn").onchange = e => uploadAssets(c.id, [...e.target.files].map(f => ({ file: f, path: f.webkitRelativePath || f.name })));
  $("#fileIn").onchange = e => uploadAssets(c.id, [...e.target.files].map(f => ({ file: f, path: f.name })));
  dropZone(drop, async items => uploadAssets(c.id, await collect(items)));
  const bf = $("#briefFile");
  $("#briefDrop").onclick = () => bf.click();
  dropZone($("#briefDrop"), async items => uploadAssets(c.id, (await collect(items)).slice(0, 1), true));
  bf.onchange = () => uploadAssets(c.id, [...bf.files].map(f => ({ file: f, path: f.name })), true);
  $("#gdocBtn").onclick = async () => {
    try {
      await api(`/api/campaigns/${c.id}/brief/gdoc`, { method: "POST", body: { url: $("#gdoc").value } });
      await render(); await analyze(c.id);
    } catch (e) { $("#briefMsg").textContent = e.message; }
  };
  if ($("#reread")) $("#reread").onclick = () => analyze(c.id);
  $$(".role").forEach(sel => sel.onchange = async () => {
    await api(`/api/campaigns/${c.id}/assets`, { method: "PATCH", body: { path: sel.dataset.path, role: sel.value } });
    render();
  });
  $$(".rm").forEach(b => b.onclick = async () => {
    await api(`/api/campaigns/${c.id}/assets?path=${encodeURIComponent(b.dataset.path)}`, { method: "DELETE" });
    render();
  });
}

function assetTable(c) {
  if (!c.assets.length) return '<p class="empty">No files yet.</p>';
  const rows = c.assets.map(a => {
    const detail = [a.duration ? dur(a.duration) : "", a.width ? `${a.width}×${a.height}` : "", a.alpha ? "transparent" : "", fmt(a.size)].filter(Boolean).join(" · ");
    return `<tr><td>${ICON[a.kind]} ${esc(a.path)}</td><td class="stats">${detail}</td>
      <td><select class="role" data-path="${esc(a.path)}">${ROLES.map(r => `<option ${r===a.role?"selected":""}>${r}</option>`).join("")}</select></td>
      <td><button class="rm danger" data-path="${esc(a.path)}">✕</button></td></tr>`;
  }).join("");
  return `<table class="assets"><tbody>${rows}</tbody></table>
    <p class="stats">logo = watermark on every clip · music = background track · brief = read for rules · image/ignore = unused</p>`;
}

async function analyze(cid) {
  const msg = $("#briefMsg");
  msg.textContent = "Reading brief…";
  try {
    const r = await api(`/api/campaigns/${cid}/brief/analyze`, { method: "POST" });
    const set = (n, v) => { const el = $(`[name=${n}]`); if (el && v !== undefined && v !== null) el.value = v; };
    set("rules", r.rules); set("hashtags", (r.hashtags || []).join(" ")); set("disclosure", r.disclosure);
    set("platform", r.platform); set("rate_cpm", r.rate_cpm); set("min_seconds", r.min_seconds); set("max_seconds", r.max_seconds);
    msg.textContent = "Filled in from the brief" + (r.note ? ` (${r.note})` : "") + ". Check it, then Save.";
  } catch (e) { msg.textContent = e.message; }
}

// ---------- content page: one video, its clips, its activity ----------
function renderContent(c, x) {
  const clips = x.clips;
  const count = s => clips.filter(k => k.status === s).length;
  const earned = clips.reduce((a, k) => a + (+k.payout || 0), 0);
  const src = !x.kind ? `
      <div class="drop" id="vdrop">Drop a video file here or <u>click to choose</u><input id="vfile" type="file" accept="video/*" hidden></div>
      <div class="row"><input id="yt" placeholder="…or paste a YouTube link (only videos the campaign lets you use)" style="flex:1;max-width:560px"><button id="ytBtn">Add video</button></div>
      <div class="stats" id="status">One video per content item. Add more content from the sidebar.</div>`
    : x.kind === "youtube" ? `
      <div class="srcinfo">▶️ <b>${esc(x.yt_title || x.title)}</b><span class="stats"> ${esc(x.channel)} · ${dur(x.duration)}${x.license ? " · " + esc(x.license) : ""} · <a href="${esc(x.url)}" target="_blank" rel="noopener">open on YouTube</a></span></div>`
    : `<div class="srcinfo">🎞 <b>${esc(x.file.split("/").pop())}</b><span class="stats"> ${dur(x.duration)} · ${x.width}×${x.height} · ${fmt(x.size)}</span></div>`;
  $("#main").innerHTML = `<div class="cols"><div class="left">
    <a class="crumb" href="#/${c.id}">← ${esc(c.name)}</a>
    <h2>${esc(x.title)} <button id="ren" class="mini" title="Rename">✎</button></h2>
    <div class="stats"><b>${clips.length}</b> clips · <b>${count("approved")}</b> approved · <b>${count("posted")}</b> posted · <b>$${earned.toFixed(2)}</b> earned</div>
    <h3>Source</h3>${src}
    ${x.kind ? `<div class="row"><button class="primary" id="gen">${clips.length ? "Find more clips" : "Generate clips"}</button>
      <span class="stats">${clips.length ? "Adds new moments that don't overlap the clips you already have." : "Transcribes, picks the best moments, then cuts and captions them."}</span></div>` : ""}
    <h3>Clips</h3>
    <div class="clips">${clips.map(clipCard(c.id, x.id)).join("") || '<p class="empty">Nothing generated yet.</p>'}</div>
    <div class="row" style="margin-top:28px"><button id="delx" class="danger">Delete this content and its clips</button></div>
    </div>
    <aside class="activity"><h3 style="margin-top:0">Activity <span id="pill" class="pill"></span></h3>
      <div class="bar" id="barwrap" hidden><div id="bar"></div></div>
      <div class="stats" id="elapsed"></div>
      <pre id="log" class="log">Nothing running. ${x.kind ? "Press Generate to start." : "Add a video first."}</pre></aside></div>`;

  $("#ren").onclick = async () => {
    const t = prompt("Rename content:", x.title);
    if (!t || t === x.title) return;
    await api(`/api/campaigns/${c.id}/content/${x.id}`, { method: "PATCH", body: { title: t } });
    render();
  };
  $("#delx").onclick = async () => {
    if (!confirm(`Delete "${x.title}", its video and its ${clips.length} clips?`)) return;
    try { await api(`/api/campaigns/${c.id}/content/${x.id}`, { method: "DELETE" }); go(c.id); } catch (e) { alert(e.message); }
  };
  if (!x.kind) {
    $("#vdrop").onclick = () => $("#vfile").click();
    dropZone($("#vdrop"), async items => { const f = (await collect(items))[0]; f && uploadVideo(c.id, x.id, f.file); });
    $("#vfile").onchange = e => e.target.files[0] && uploadVideo(c.id, x.id, e.target.files[0]);
    $("#ytBtn").onclick = async () => {
      const st = $("#status"), btn = $("#ytBtn");
      btn.disabled = true; st.textContent = "Looking up video…";
      try { await api(`/api/campaigns/${c.id}/content/${x.id}/youtube`, { method: "POST", body: { url: $("#yt").value } }); render(); }
      catch (e) { st.textContent = e.message; btn.disabled = false; }
    };
  } else {
    $("#gen").onclick = () => generate(c.id, x.id);
  }
  $$(".clip").forEach(el => {
    const f = encodeURIComponent(el.dataset.file), patch = body =>
      api(`/api/campaigns/${c.id}/content/${x.id}/clips/${f}`, { method: "PATCH", body });
    $(".st", el).onchange = async e => { await patch({ status: e.target.value }); el.className = `clip ${e.target.value}`; };
    $(".pay", el).onchange = e => patch({ payout: parseFloat(e.target.value) || 0 });
    $(".cp", el).onclick = () => navigator.clipboard.writeText($("p", el).textContent);
  });
  attachJob(c.id, x.id);
}

const clipCard = (cid, xid) => k => `
  <div class="clip ${k.status}" data-file="${esc(k.file)}">
    <video src="/media/${cid}/${encodeURIComponent(k.file)}?v=${Math.round(k.start * 100)}-${Math.round(k.end * 100)}" controls preload="metadata"></video>
    <b>${esc(k.title)}</b>
    <div class="stats">${tc(k.start)}–${tc(k.end)} in source · ${Math.round(k.end - k.start)}s${k.score ? ` · score ${k.score}` : ""} · #${k.file.match(/_(\d+)\.mp4$/)?.[1] ?? "?"}</div>
    <p>${esc(k.caption)}</p>
    <div class="row">
      <select class="st">${["new","approved","rejected","posted"].map(s => `<option ${s===k.status?"selected":""}>${s}</option>`).join("")}</select>
      <input class="pay" type="number" step="0.01" placeholder="$ paid" value="${k.payout || ""}" style="width:80px">
    </div>
    <div class="row"><button class="cp">Copy caption</button><a href="/media/${cid}/${encodeURIComponent(k.file)}" download><button>Download</button></a></div>
  </div>`;

// ---------- uploads ----------
function dropZone(el, onItems) {
  el.ondragover = e => { e.preventDefault(); el.classList.add("over"); };
  el.ondragleave = () => el.classList.remove("over");
  el.ondrop = e => { e.preventDefault(); el.classList.remove("over"); onItems(e.dataTransfer.items); };
}

// Walk dropped folders recursively (works for single files too).
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
  for (const e of [...items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean)) await walk(e, "");
  return out;
}

const xhrUpload = (url, fd, onProgress) => new Promise((res, rej) => {
  const x = new XMLHttpRequest();
  x.open("POST", url);
  x.upload.onprogress = e => e.lengthComputable && onProgress(Math.round(e.loaded / e.total * 100));
  x.onload = () => x.status < 300 ? res(JSON.parse(x.responseText)) : rej(new Error((() => { try { return JSON.parse(x.responseText).detail; } catch { return x.statusText; } })()));
  x.onerror = () => rej(new Error("Upload failed"));
  x.send(fd);
});

async function uploadVideo(cid, xid, file) {
  const st = $("#status");
  const fd = new FormData(); fd.append("file", file);
  try {
    await xhrUpload(`/api/campaigns/${cid}/content/${xid}/file`, fd, p => st.textContent = `Uploading ${file.name}… ${p}%`);
    render();
  } catch (e) { st.textContent = e.message; }
}

async function uploadAssets(cid, items, isBrief = false) {
  items = items.filter(i => !i.path.split("/").some(p => p.startsWith(".")));
  const st = $("#status"); let n = 0, sawBrief = false, videos = 0;
  for (const it of items) {
    const fd = new FormData(); fd.append("file", it.file); fd.append("path", it.path);
    try {
      const r = await xhrUpload(`/api/campaigns/${cid}/assets`, fd, p => st.textContent = `Uploading ${n + 1}/${items.length}: ${it.path} ${p}%`);
      sawBrief ||= r.role === "brief"; if (r.content) videos++;
    } catch (e) { st.textContent = `Failed: ${it.path} (${e.message})`; }
    n++;
  }
  const hadRules = ($("[name=rules]") || {}).value;
  await render();
  if (videos) $("#status").textContent = `${videos} video${videos > 1 ? "s" : ""} added as content: see the sidebar.`;
  if (isBrief || (sawBrief && !hadRules)) await analyze(cid);
}

// ---------- generation + live activity ----------
async function attachJob(cid, xid) {
  const loop = ++jobLoop;  // only the newest loop may update the page
  let was = false;
  for (;;) {
    let j;
    try { j = await api(`/api/campaigns/${cid}/content/${xid}/job`); } catch { return; }
    if (loop !== jobLoop || !$("#log")) return;
    if (!j.id) return;
    const running = j.state === "running";
    const log = $("#log"), stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.textContent = j.log.join("\n") || "Starting…";
    if (stick) log.scrollTop = log.scrollHeight;
    const pill = $("#pill"); pill.textContent = j.state; pill.className = `pill ${j.state}`;
    $("#elapsed").textContent = `${running ? "Running for" : "Took"} ${Math.floor(j.elapsed / 60)}m ${Math.floor(j.elapsed % 60)}s`;
    $("#barwrap").hidden = !running;
    $("#barwrap").classList.toggle("indet", j.percent == null);
    $("#bar").style.width = j.percent == null ? "35%" : `${j.percent}%`;
    if ($("#gen")) $("#gen").disabled = running;
    if (!running) { if (was) await render(); return; }
    was = true;
    await new Promise(r => setTimeout(r, 1500));
  }
}

async function generate(cid, xid) {
  try {
    await api(`/api/campaigns/${cid}/content/${xid}/generate`, { method: "POST" });
    busy = { cid, xid }; sidebar();
    attachJob(cid, xid);
  } catch (e) { $("#log").textContent = e.message; }
}

render();
