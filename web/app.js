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
const money = n => "$" + (+n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const ROLES = ["logo", "music", "image", "brief", "font", "ignore"];
const STATUSES = ["new", "approved", "rejected", "posted"];
const TAGL = { hook: "Punchline first", keyword: "Keyword", loop: "Loop", edited: "Edited" };

// ---------- icons (stroke, 24px grid) ----------
const PATHS = {
  chevron: '<path d="m9 18 6-6-6-6"/>', plus: '<path d="M5 12h14M12 5v14"/>', back: '<path d="m12 19-7-7 7-7M19 12H5"/>',
  pencil: '<path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
  trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  youtube: '<rect x="2" y="5" width="20" height="14" rx="4"/><path d="m10 9 5 3-5 3Z"/>',
  film: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 12h18M3 16h4M17 8h4M17 16h4"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4M8 13h8M8 17h8"/>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>', link: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  empty: '<rect width="18" height="18" x="3" y="3" rx="2" stroke-dasharray="3 3"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"/>',
};
const I = (n, s = 16) => `<svg class="i" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${PATHS[n]}</svg>`;
const KIND = { youtube: "youtube", video: "film", image: "image", audio: "music", doc: "file", font: "file", other: "file" };
const kindIcon = (k, s) => I(KIND[k] || "empty", s);

let toastTimer;
function toast(msg, kind = "") {
  const t = $("#toast"); t.textContent = msg; t.className = `show ${kind}`;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.className = "", 2600);
}

let data = [];        // campaigns, each with its content items and their clips
let route = {};       // { cid, xid }
let busy = {};        // which content is generating right now
let jobLoop = 0;
let clipFilter = "all";
const expanded = new Set(JSON.parse(localStorage.getItem("expanded") || "[]"));
const saveExpanded = () => localStorage.setItem("expanded", JSON.stringify([...expanded]));

// ---------- routing: #/<campaign>[/<content>] ----------
const go = (cid, xid) => { const h = cid ? `#/${cid}${xid ? "/" + xid : ""}` : "#/"; location.hash === h ? render() : (location.hash = h); };
window.addEventListener("hashchange", render);

async function render() {
  [data, busy] = await Promise.all([api("/api/campaigns"), api("/api/running").catch(() => ({}))]);
  const [, cid, xid, file] = location.hash.split("/");
  route = { cid, xid, file };
  window.editorCleanup && window.editorCleanup();
  if (cid) { expanded.add(cid); saveExpanded(); }
  sidebar();
  const c = data.find(c => c.id === cid);
  jobLoop++;  // stop any previous progress loop
  if (!c) return void ($("#main").innerHTML = `<div class="welcome"><h1>Clip</h1><p>${data.length ? "Pick a campaign on the left." : "Create your first campaign to get started."}</p></div>`);
  const x = xid && c.content.find(x => x.id === xid);
  if (xid && !x) return go(cid);
  $("#main").scrollTop = 0;
  if (x && file) return renderEditor(c, x, decodeURIComponent(file));
  x ? renderContent(c, x) : renderCampaign(c);
}

// ---------- sidebar ----------
function sidebar() {
  $("#campaigns").innerHTML = data.map(c => {
    const open = expanded.has(c.id), on = route.cid === c.id && !route.xid;
    const items = c.content.map(x => `
      <a class="ci ${route.xid === x.id ? "on" : ""}" href="#/${c.id}/${x.id}" title="${esc(x.title)}">
        <span class="ico">${kindIcon(x.kind, 15)}</span><span class="t">${esc(x.title)}</span>
        ${busy.xid === x.id ? '<span class="dot" title="Generating"></span>' : ""}
        ${x.clips.length ? `<span class="n">${x.clips.length}</span>` : ""}</a>`).join("");
    return `<div class="camp ${open ? "open" : ""}">
      <a class="ch ${on ? "on" : ""}" href="#/${c.id}"><button class="caret" data-toggle="${c.id}" aria-label="Show or hide content">${I("chevron", 14)}</button>
        <span class="t"><b>${esc(c.name)}</b><small>${esc(c.platform)} · $${c.rate_cpm}/1k views</small></span>${busy.cid === c.id ? '<span class="dot" title="Generating"></span>' : ""}</a>
      <div class="items-wrap"><div class="items-in"><div class="items">${items}
        <button class="addc" data-add="${c.id}">${I("plus", 15)}<span>Add content</span></button></div></div></div></div>`;
  }).join("") || '<p class="empty">No campaigns yet.</p>';
}

$("#campaigns").onclick = e => {  // campaigns and content are real links; only the buttons need code
  const t = e.target.closest("[data-toggle]");
  if (t) {
    e.preventDefault();
    const id = t.dataset.toggle, camp = t.closest(".camp");
    camp.classList.toggle("open");  // class toggle (not a re-render) so the drop-down animates
    camp.classList.contains("open") ? expanded.add(id) : expanded.delete(id); saveExpanded();
    return;
  }
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
const CAP_PRESETS = {
  Classic: { cap_font: "Inter", cap_size: 84, cap_color: "#FFFFFF", cap_highlight: "#FFE500", cap_style: "outline", cap_pos: "low", cap_words: 3, cap_upper: true, cap_pop: false },
  Pop: { cap_font: "League Spartan", cap_size: 96, cap_color: "#FFFFFF", cap_highlight: "#FFE500", cap_style: "outline", cap_pos: "low", cap_words: 2, cap_upper: true, cap_pop: true },
  Clean: { cap_font: "Inter", cap_size: 72, cap_color: "#FFFFFF", cap_highlight: "#7EE8FA", cap_style: "shadow", cap_pos: "low", cap_words: 4, cap_upper: false, cap_pop: false },
  Boxed: { cap_font: "Roboto", cap_size: 70, cap_color: "#FFFFFF", cap_highlight: "#FFD400", cap_style: "box", cap_pos: "low", cap_words: 3, cap_upper: false, cap_pop: false },
  Impact: { cap_font: "Bebas Neue", cap_size: 116, cap_color: "#FFFFFF", cap_highlight: "#FF4D4D", cap_style: "outline", cap_pos: "low", cap_words: 3, cap_upper: true, cap_pop: true },
};
const field = (label, input, cls = "") => `<label class="field ${cls}">${label}${input}</label>`;
const opts = (list, cur) => list.map(([v, t]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${t}</option>`).join("");

function renderCampaign(c) {
  const briefs = c.assets.filter(a => a.role === "brief");
  const clips = c.content.flatMap(x => x.clips);
  const count = s => clips.filter(x => x.status === s).length;
  const earned = clips.reduce((a, x) => a + (+x.payout || 0), 0);
  $("#main").innerHTML = `<div class="page">
    <header class="ph"><div><h1>${esc(c.name)}</h1><p class="sub">${esc(c.platform)} · $${c.rate_cpm} per 1,000 views</p></div>
      <button class="btn primary" id="addc">${I("plus", 15)}Add content</button></header>
    <div class="tiles">
      <div class="tile"><span>Content</span><b>${c.content.length}</b></div><div class="tile"><span>Clips</span><b>${clips.length}</b></div>
      <div class="tile"><span>Approved</span><b>${count("approved")}</b></div><div class="tile"><span>Posted</span><b>${count("posted")}</b></div>
      <div class="tile money"><span>Earned</span><b>${money(earned)}</b></div></div>

    <section class="card">
      <header><h2>Brief</h2>${briefs.length ? `<button class="btn sm" id="reread">Re-read brief</button>` : ""}</header>
      <p class="hint">Drop the campaign brief and Claude fills in the rules, tags and lengths below. You can edit anything afterwards.</p>
      <div class="drop" id="briefDrop"><span class="ico">${I("upload", 22)}</span><span>Drop the brief here or <u>click to choose</u></span><small>PDF, Word or text</small>
        <input id="briefFile" type="file" accept=".pdf,.docx,.txt,.md" hidden></div>
      <div class="row" style="margin-top:10px"><input id="gdoc" placeholder="…or paste a Google Docs link (shared with “anyone with the link”)" style="flex:1;min-width:240px"><button class="btn" id="gdocBtn">Read link</button></div>
      ${briefs.length ? `<div class="chips">${briefs.map(a => `<span class="chip">${I("file", 13)}${esc(a.path)}</span>`).join("")}</div>` : ""}
      <div class="note" id="briefMsg"></div>
    </section>

    <form class="card" id="f">
      <header><h2>Settings</h2></header>
      <div class="group"><h3>Campaign</h3><div class="grid">
        ${field("Name", `<input name="name" value="${esc(c.name)}">`)}
        ${field("Platform", `<select name="platform">${opts(["TikTok", "Instagram", "YouTube Shorts", "Any"].map(p => [p, p]), c.platform)}</select>`)}
        ${field("Rate ($ per 1,000 views)", `<input name="rate_cpm" type="number" step="0.01" value="${c.rate_cpm}">`)}
        ${field("Budget / payout notes", `<input name="budget_note" value="${esc(c.budget_note)}" placeholder="e.g. $300 cap per clip">`)}</div></div>
      <div class="group"><h3>Rules</h3><div class="grid">
        ${field(`Brief and rules <small>Claude uses this to pick clips and write captions.</small>`, `<textarea name="rules" style="min-height:170px">${esc(c.rules)}</textarea>`, "full")}</div></div>
      <div class="group"><h3>Post caption</h3><div class="grid">
        ${field("Hashtags (space separated)", `<input name="hashtags" value="${esc(c.hashtags.join(" "))}" placeholder="#example #tags">`)}
        ${field("Disclosure tag", `<input name="disclosure" value="${esc(c.disclosure)}">`)}</div></div>
      <div class="group"><h3>Clip style</h3><div class="grid three">
        ${field("Min seconds", `<input name="min_seconds" type="number" value="${c.min_seconds}">`)}
        ${field("Max seconds", `<input name="max_seconds" type="number" value="${c.max_seconds}">`)}
        ${field("Max clips per video", `<input name="clips_per_video" type="number" value="${c.clips_per_video}">`)}
        ${field("Framing", `<select name="frame_mode">${opts([["blur", "Fit + blurred background"], ["fill", "Fill (center crop)"]], c.frame_mode)}</select>`, "full")}</div></div>
      <div class="group"><h3>Subtitles</h3><div class="capstyle"><div>
        <div class="presets" id="capPresets">${Object.keys(CAP_PRESETS).map(n => `<button type="button" class="btn sm" data-p="${n}">${n}</button>`).join("")}</div>
        <div class="grid" style="margin-top:12px">
        ${field("Font", `<select name="cap_font"><option>${esc(c.cap_font)}</option></select>`)}
        ${field("Size", `<input name="cap_size" type="number" min="40" max="160" value="${c.cap_size}">`)}
        ${field("Text colour", `<input name="cap_color" type="color" value="${esc(c.cap_color)}">`)}
        ${field("Highlight colour", `<input name="cap_highlight" type="color" value="${esc(c.cap_highlight)}">`)}
        ${field("Look", `<select name="cap_style">${opts([["outline", "Outline"], ["shadow", "Soft shadow"], ["box", "Dark box"]], c.cap_style)}</select>`)}
        ${field("Position", `<select name="cap_pos">${opts([["low", "Low"], ["mid", "Middle"], ["high", "High"]], c.cap_pos)}</select>`)}
        ${field("Words per line", `<input name="cap_words" type="number" min="1" max="5" value="${c.cap_words}">`)}
        <div class="field"><span>Options</span><div class="row" style="height:36px;gap:18px"><label class="check"><input type="checkbox" name="cap_upper" ${c.cap_upper ? "checked" : ""}> Uppercase</label><label class="check"><input type="checkbox" name="cap_pop" ${c.cap_pop ? "checked" : ""}> Pop the spoken word</label></div></div>
        </div><p class="mute" style="margin-top:10px">Upload a font file (.ttf or .otf) under “Logo, music and other files” and it appears in the Font list.</p></div>
        <div class="capprev"><img id="capprev" alt="Subtitle preview"><span class="mute">Preview, drawn by the real renderer</span></div></div></div>
      <div class="group"><h3>Branding</h3><div class="grid three">
        ${field("Logo position", `<select name="logo_position">${opts([["tr", "Top right"], ["tl", "Top left"], ["br", "Bottom right"], ["bl", "Bottom left"], ["off", "Off"]], c.logo_position)}</select>`)}
        ${field("Logo size (fraction of width)", `<input name="logo_scale" type="number" step="0.01" min="0.05" max="0.6" value="${c.logo_scale}">`)}
        ${field("Music volume (0 to 1)", `<input name="music_volume" type="number" step="0.01" min="0" max="1" value="${c.music_volume}">`)}</div></div>
      <div class="savebar"><button class="btn primary" id="save" disabled>Save changes</button><span class="state" id="saved">All changes saved</span></div>
    </form>

    ${checksCard(c)}

    <section class="card">
      <header><h2>Logo, music and other files</h2></header>
      <p class="hint">A logo is placed on every clip, music plays under the audio. A video dropped here becomes its own content item.</p>
      <div class="drop static" id="drop"><span class="ico">${I("upload", 22)}</span><span>Drop files or a folder here</span>
        <div class="row"><button type="button" class="btn sm" id="pickDir">${I("film", 14)}Choose folder</button><button type="button" class="btn sm" id="pickFiles">Choose files</button></div>
        <input id="dirIn" type="file" webkitdirectory multiple hidden><input id="fileIn" type="file" multiple hidden></div>
      <div class="note" id="status"></div>
      ${assetTable(c)}
    </section>

    <section class="card">
      <header><h2>Content</h2></header>
      ${c.content.map(x => `<a class="crow" href="#/${c.id}/${x.id}"><span class="ico">${kindIcon(x.kind, 18)}</span>
        <span class="t"><b>${esc(x.title)}</b><span class="mute">${x.kind ? (x.duration ? dur(x.duration) + " · " : "") : "No source yet · "}${x.clips.length} clip${x.clips.length === 1 ? "" : "s"}</span></span>${I("chevron", 16)}</a>`).join("")
        || '<p class="hint" style="margin:0">Nothing yet. Use “Add content” to add your first video.</p>'}
    </section>
    <div class="danger-zone"><button id="del" class="btn danger">${I("trash", 15)}Delete campaign</button></div>
  </div>`;

  bindChecks(c);
  const form = $("#f"), save = $("#save"), state = $("#saved");
  // subtitle style: font list, presets and a live preview from the real renderer
  const capKeys = ["cap_font", "cap_size", "cap_color", "cap_highlight", "cap_style", "cap_pos", "cap_words"];
  let prevTimer;
  const refreshPreview = () => {
    clearTimeout(prevTimer);
    prevTimer = setTimeout(() => {
      const q = new URLSearchParams(Object.fromEntries(capKeys.map(k => [k, form.elements[k].value])));
      q.set("cap_upper", form.elements.cap_upper.checked ? "1" : "0"); q.set("cap_pop", form.elements.cap_pop.checked ? "1" : "0"); q.set("t", Date.now());
      $("#capprev").src = `/api/campaigns/${c.id}/caption-preview.png?${q}`;
    }, 250);
  };
  api(`/api/campaigns/${c.id}/fonts`).then(f => {
    const sel = form.elements.cap_font, cur = c.cap_font, names = [...f.builtin, ...f.uploaded.map(u => u.family)];
    if (!names.includes(cur)) names.push(cur);
    sel.innerHTML = names.map(n => `<option ${n === cur ? "selected" : ""}>${esc(n)}</option>`).join(""); refreshPreview();
  }).catch(refreshPreview);
  $$("#capPresets button").forEach(b => b.onclick = () => {
    const p = CAP_PRESETS[b.dataset.p];
    for (const [k, v] of Object.entries(p)) { const el = form.elements[k]; if (!el) continue; if (el.type === "checkbox") el.checked = v; else { if (el.tagName === "SELECT" && ![...el.options].some(o => o.value === String(v))) el.add(new Option(v, v)); el.value = v; } }
    form.dispatchEvent(new Event("input", { bubbles: true })); refreshPreview();
  });
  // getAttribute, not .name: the form has a field called "name" that shadows form.name
  const capEvent = e => { const n = e.target.getAttribute && e.target.getAttribute("name"); if (n && n.startsWith("cap_")) refreshPreview(); };
  form.addEventListener("input", capEvent); form.addEventListener("change", capEvent);
  const dirty = () => { save.disabled = false; state.textContent = "Unsaved changes"; state.className = "state"; };
  form.addEventListener("input", dirty); form.addEventListener("change", dirty);
  form.onsubmit = async e => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(form));
    d.hashtags = d.hashtags.split(/\s+/).filter(Boolean);
    ["rate_cpm", "logo_scale", "music_volume"].forEach(k => d[k] = d[k] === "" ? null : parseFloat(d[k]));
    ["min_seconds", "max_seconds", "clips_per_video", "cap_size", "cap_words"].forEach(k => d[k] = d[k] === "" ? null : parseInt(d[k]));
    d.cap_upper = form.elements.cap_upper.checked; d.cap_pop = form.elements.cap_pop.checked;
    Object.keys(d).forEach(k => d[k] === null && delete d[k]);  // blank = keep the current value
    try {
      await api(`/api/campaigns/${c.id}`, { method: "PATCH", body: d });
      data = await api("/api/campaigns"); sidebar();
      save.disabled = true; state.textContent = "Saved ✓"; state.className = "state ok"; toast("Settings saved");
    } catch (err) { toast(err.message, "bad"); }
  };
  $("#addc").onclick = () => addContent(c.id);
  $("#del").onclick = async () => {
    if (!confirm(`Delete "${c.name}" with all its content and clips?`)) return;
    try { await api(`/api/campaigns/${c.id}`, { method: "DELETE" }); go(); } catch (e) { toast(e.message, "bad"); }
  };
  $("#pickDir").onclick = () => $("#dirIn").click();
  $("#pickFiles").onclick = () => $("#fileIn").click();
  $("#dirIn").onchange = e => uploadAssets(c.id, [...e.target.files].map(f => ({ file: f, path: f.webkitRelativePath || f.name })));
  $("#fileIn").onchange = e => uploadAssets(c.id, [...e.target.files].map(f => ({ file: f, path: f.name })));
  dropZone($("#drop"), async items => uploadAssets(c.id, await collect(items)));
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


// ---------- campaign page: the pre-submission checklist ----------
function checksCard(c) {
  const items = c.checks.items, kinds = [["caption", "Caption"], ["visual", "Video frames"], ["speech", "Speech"], ["length", "Length"], ["manual", "Reminder"]];
  const row = (i, n) => `<div class="ckrow" data-n="${n}"><input type="checkbox" class="ckon" ${i.enabled ? "checked" : ""} aria-label="Enabled">
      <input class="cktext" value="${esc(i.text)}"><select class="ckkind">${kinds.map(([v, t]) => `<option value="${v}" ${i.kind === v ? "selected" : ""}>${t}</option>`).join("")}</select>
      <button class="icon-btn ckrm" title="Remove">${I("x", 15)}</button></div>`;
  return `<section class="card" id="checks">
    <header><h2>Pre-submission checks</h2><div class="row"><button class="btn sm" id="ckgen">${I("shield", 14)}${items.length ? "Rebuild from brief" : "Build from brief"}</button><button class="btn sm" id="ckadd">${I("plus", 13)}Add check</button></div></header>
    <p class="hint">Claude judges every clip against this list using its caption, transcript and frames from the video. Reminders can't be seen in a clip, so they show as a checklist before you post.
      Always checked: a clean start and end, caption quality, readable subtitles, no other brands, length and required tags.</p>
    ${c.checks.stale ? '<div class="note" style="display:block;margin:0 0 12px">The rules changed since this checklist was built. Use “Rebuild from brief” to refresh it (your edits will be replaced).</div>' : ""}
    ${items.map(row).join("") || '<p class="hint" style="margin:0">No checks yet. Add the brief above, then build the checklist from it.</p>'}
  </section>`;
}
function bindChecks(c) {
  const save = async items => {
    try { c.checks = await api(`/api/campaigns/${c.id}/checks`, { method: "PUT", body: { items, hash: c.checks.hash } }); toast("Checks saved"); }
    catch (e) { toast(e.message, "bad"); }
  };
  const read = () => $$(".ckrow").map(r => ({ id: c.checks.items[+r.dataset.n]?.id || "", text: $(".cktext", r).value, kind: $(".ckkind", r).value, enabled: $(".ckon", r).checked }));
  $$(".ckrow").forEach(r => {
    $(".ckon", r).onchange = () => save(read()); $(".ckkind", r).onchange = () => save(read()); $(".cktext", r).onchange = () => save(read());
    $(".ckrm", r).onclick = async () => { const n = +r.dataset.n; await save(read().filter((_, i) => i !== n)); render(); };
  });
  $("#ckadd").onclick = async () => { await save([...read(), { id: "", text: "New check", kind: "speech", enabled: true }]); render(); };
  $("#ckgen").onclick = async () => {
    const b = $("#ckgen"); b.disabled = true; b.textContent = "Reading the brief…";
    try { await api(`/api/campaigns/${c.id}/checks/generate`, { method: "POST" }); toast("Checklist built"); render(); }
    catch (e) { toast(e.message, "bad"); b.disabled = false; b.textContent = "Build from brief"; }
  };
}

function assetTable(c) {
  if (!c.assets.length) return "";
  const rows = c.assets.map(a => {
    const detail = [a.duration ? dur(a.duration) : "", a.width ? `${a.width}×${a.height}` : "", a.alpha ? "transparent" : "", fmt(a.size)].filter(Boolean).join(" · ");
    return `<tr><td><span class="fname"><span class="ico">${kindIcon(a.kind, 16)}</span>${esc(a.path)}</span></td><td class="mute">${detail}</td>
      <td><select class="role" data-path="${esc(a.path)}">${ROLES.map(r => `<option ${r === a.role ? "selected" : ""}>${r}</option>`).join("")}</select></td>
      <td><button class="icon-btn rm" data-path="${esc(a.path)}" title="Remove">${I("x", 15)}</button></td></tr>`;
  }).join("");
  return `<table class="assets"><tbody>${rows}</tbody></table>`;
}

async function analyze(cid) {
  const msg = $("#briefMsg");
  msg.textContent = "Reading brief…";
  try {
    const r = await api(`/api/campaigns/${cid}/brief/analyze`, { method: "POST" });
    const set = (n, v) => { const el = $(`[name=${n}]`); if (el && v !== undefined && v !== null) { el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); } };
    set("rules", r.rules); set("hashtags", (r.hashtags || []).join(" ")); set("disclosure", r.disclosure);
    set("platform", r.platform); set("rate_cpm", r.rate_cpm); set("min_seconds", r.min_seconds); set("max_seconds", r.max_seconds);
    msg.textContent = "Filled in from the brief" + (r.note ? ` (${r.note})` : "") + ". Check the settings below, then save.";
  } catch (e) { msg.textContent = e.message; }
}

// ---------- content page: one video, its clips, its activity ----------
let picked = new Set();   // clip files ticked for editing

function renderContent(c, x) {
  picked = new Set();
  const clips = x.clips;
  const count = s => clips.filter(k => k.status === s).length;
  const earned = clips.reduce((a, k) => a + (+k.payout || 0), 0);
  const generate_btn = x.kind ? `<button class="btn primary" id="gen">${clips.length ? "Find more clips" : "Generate clips"}</button>` : "";
  const source = !x.kind ? `
      <header><h2>Add the source</h2></header>
      <p class="hint">One video per content item. Upload a file or paste a YouTube link.</p>
      <div class="pair">
        <div class="drop" id="vdrop"><span class="ico">${I("upload", 22)}</span><span>Drop a video here or <u>click to choose</u></span><small>mp4, mov, mkv, webm</small>
          <input id="vfile" type="file" accept="video/*" hidden></div>
        <div class="or"><label for="yt">YouTube link</label><input id="yt" placeholder="https://www.youtube.com/watch?v=…"><button class="btn" id="ytBtn">${I("youtube", 15)}Add video</button>
          <small class="mute">Only use videos the campaign lets you clip.</small></div></div>
      <div class="note" id="status"></div>`
    : `<div class="source"><span class="big">${I(x.kind === "youtube" ? "youtube" : "film", 22)}</span>
        <div class="info"><b>${esc(x.kind === "youtube" ? (x.yt_title || x.title) : x.file.split("/").pop())}</b>
          <div class="meta">${x.kind === "youtube"
            ? `<span>${esc(x.channel)}</span><span>${dur(x.duration)}</span>${x.license ? `<span>${esc(x.license)}</span>` : ""}<a href="${esc(x.url)}" target="_blank" rel="noopener">Open on YouTube ${I("link", 12)}</a>`
            : `<span>${dur(x.duration)}</span><span>${x.width}×${x.height}</span><span>${fmt(x.size)}</span>`}</div></div>
        <div class="go">${generate_btn}<p class="hint">${clips.length ? "Adds moments that don't overlap your existing clips." : "Transcribes, picks the best moments, then cuts and captions them."}</p></div></div>`;
  $("#main").innerHTML = `<div class="cols"><div class="left">
    <a class="crumb" href="#/${c.id}">${I("back", 14)}${esc(c.name)}</a>
    <header class="ph"><div><h1>${esc(x.title)}<button id="ren" class="icon-btn" title="Rename">${I("pencil", 15)}</button></h1></div></header>
    <div class="tiles"><div class="tile"><span>Clips</span><b>${clips.length}</b></div><div class="tile"><span>Approved</span><b>${count("approved")}</b></div>
      <div class="tile"><span>Posted</span><b>${count("posted")}</b></div><div class="tile money"><span>Earned</span><b>${money(earned)}</b></div></div>
    <section class="card">${source}</section>
    <div class="bar-head"><h2>Clips</h2><div class="row" style="gap:10px">${clips.length ? `<button class="btn sm" id="qaall" title="Check every clip against the campaign's rules">${I("shield", 14)}Check all</button>` : ""}<div class="tabs" id="tabs">${["all", ...STATUSES].map(s => `<button class="tab ${clipFilter === s ? "on" : ""}" data-f="${s}">${s[0].toUpperCase() + s.slice(1)}<em></em></button>`).join("")}</div></div></div>
    <div class="clips" id="clips">${clips.map(clipCard(c.id)).join("") || `<div class="empty-state"><b>No clips yet</b>${x.kind ? "Press Generate to create them." : "Add a source above first."}</div>`}</div>
    <div class="danger-zone" style="margin-top:32px"><button id="delx" class="btn danger">${I("trash", 15)}Delete this content</button></div>
    <div class="selbar" id="selbar" hidden><span id="selcount"></span><button class="btn primary sm" id="editsel">${I("scissors", 14)}Edit</button><button class="btn sm" id="checksel">${I("shield", 14)}Check</button><button class="btn ghost sm" id="clearsel">Clear</button></div>
    </div>
    <aside class="activity card"><header><h2>Activity</h2><span id="pill" class="pill"></span></header>
      <div class="prog" id="barwrap" hidden><div id="bar"></div></div>
      <div class="elapsed" id="elapsed"></div>
      <div class="log" id="log"><div class="none">Nothing running. ${x.kind ? "Press Generate to start." : "Add a video first."}</div></div></aside></div>`;

  $("#ren").onclick = async () => {
    const t = prompt("Rename content:", x.title);
    if (!t || t === x.title) return;
    await api(`/api/campaigns/${c.id}/content/${x.id}`, { method: "PATCH", body: { title: t } });
    render();
  };
  $("#delx").onclick = async () => {
    if (!confirm(`Delete "${x.title}", its video and its ${clips.length} clips?`)) return;
    try { await api(`/api/campaigns/${c.id}/content/${x.id}`, { method: "DELETE" }); go(c.id); } catch (e) { toast(e.message, "bad"); }
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
  $$(".tab").forEach(t => t.onclick = () => { clipFilter = t.dataset.f; applyFilter(); });
  $$(".clip").forEach(el => {
    const f = encodeURIComponent(el.dataset.file), patch = body =>
      api(`/api/campaigns/${c.id}/content/${x.id}/clips/${f}`, { method: "PATCH", body });
    $$(".seg button", el).forEach(b => b.onclick = async () => {
      try { await patch({ status: b.dataset.s }); } catch (e) { return toast(e.message, "bad"); }
      el.className = `clip ${b.dataset.s}`; el.dataset.status = b.dataset.s;
      $$(".seg button", el).forEach(o => o.classList.toggle("on", o === b));
      applyFilter();
    });
    $(".pay input", el).onchange = e => patch({ payout: parseFloat(e.target.value) || 0 }).then(() => toast("Saved"));
    const copy = () => navigator.clipboard.writeText($(".cap p", el).textContent).then(() => toast("Caption copied"));
    $(".cp", el).onclick = copy;
    $(".cap", el).onclick = e => e.currentTarget.classList.toggle("open");
  });
  const sync = () => {
    $$(".clip").forEach(el => { const on = picked.has(el.dataset.file); el.classList.toggle("picked", on); $(".pick", el).checked = on; });
    $("#selbar").hidden = !picked.size; $("#selcount").textContent = `${picked.size} selected`;
    $("#editsel").lastChild.textContent = picked.size > 1 ? "Edit together" : "Edit";
  };
  $$(".clip").forEach(el => $(".pick", el).onchange = e => { e.target.checked ? picked.add(el.dataset.file) : picked.delete(el.dataset.file); sync(); });
  $("#clearsel").onclick = () => { picked.clear(); sync(); };
  const openQA = async (k, chip) => { const rerun = () => runQA(c, x, k, chip).then(q => q && qaDialog(c, x, k, rerun)); k.qa ? qaDialog(c, x, k, rerun) : rerun(); };
  $$(".clip").forEach(el => { const k = clips.find(z => z.file === el.dataset.file); $(".qachip", el).onclick = e => openQA(k, e.currentTarget); });
  const checkMany = async files => {
    try { await api(`/api/campaigns/${c.id}/content/${x.id}/qa-all`, { method: "POST", body: { files } }); busy = { cid: c.id, xid: x.id }; sidebar(); attachJob(c.id, x.id); toast("Checking. Follow it in Activity."); }
    catch (e) { toast(e.message, "bad"); }
  };
  if ($("#qaall")) $("#qaall").onclick = () => checkMany([]);
  $("#checksel").onclick = () => picked.size === 1 ? openQA(clips.find(z => z.file === [...picked][0]), $(`.clip[data-file="${[...picked][0]}"] .qachip`)) : checkMany([...picked]);
  $("#editsel").onclick = () => picked.size === 1 ? (location.hash = `#/${c.id}/${x.id}/${encodeURIComponent([...picked][0])}`) : batchDialog(c, x, [...picked]);
  applyFilter();
  attachJob(c.id, x.id);
}

function batchDialog(c, x, files) {
  const m = document.createElement("div"); m.className = "modal";
  m.innerHTML = `<div class="box"><h2>Edit ${files.length} clips together</h2>
    <p class="hint">Each clip becomes a new edited version next to the original. Claude picks the lines and keywords; the words are never changed, only the order.</p>
    <div class="field" style="margin-bottom:12px"><span>Opening</span><div class="seg wide" id="bopen">${[["none", "Normal"], ["hook", "Punchline first"], ["loop", "Seamless loop"]].map(([v, t], i) => `<button data-v="${v}" class="${i === 1 ? "on" : ""}">${t}</button>`).join("")}</div></div>
    <label class="check"><input type="checkbox" id="bkw" checked> Keyword on screen</label>
    <div class="note" id="bmsg"></div>
    <div class="row end" style="margin-top:16px"><button class="btn" id="bcancel">Cancel</button><button class="btn primary" id="bgo">Create ${files.length} edited clips</button></div></div>`;
  document.body.appendChild(m);
  let opening = "hook";
  $$("#bopen button", m).forEach(b => b.onclick = () => { opening = b.dataset.v; $$("#bopen button", m).forEach(o => o.classList.toggle("on", o === b)); });
  const close = () => m.remove();
  m.onclick = e => e.target === m && close(); $("#bcancel", m).onclick = close;
  $("#bgo", m).onclick = async () => {
    const modes = [opening !== "none" ? opening : null, $("#bkw", m).checked ? "keyword" : null].filter(Boolean);
    if (!modes.length) return void ($("#bmsg", m).textContent = "Pick an opening style or Keyword first.");
    try { await api(`/api/campaigns/${c.id}/content/${x.id}/batch-edit`, { method: "POST", body: { files, modes } }); close(); picked.clear(); busy = { cid: c.id, xid: x.id }; sidebar(); attachJob(c.id, x.id); toast("Editing started. Follow it in Activity."); }
    catch (e) { $("#bmsg", m).textContent = e.message; }
  };
}

function applyFilter() {
  const cards = $$(".clip");
  $$(".tab").forEach(t => {
    const f = t.dataset.f, n = f === "all" ? cards.length : cards.filter(c => c.dataset.status === f).length;
    $("em", t).textContent = n; t.classList.toggle("on", f === clipFilter);
  });
  cards.forEach(c => c.hidden = clipFilter !== "all" && c.dataset.status !== clipFilter);
}


// ---------- pre-submission checks ----------
const QA_ICON = { pass: ["check", "ok"], warn: ["alert", "warn"], unsure: ["alert", "warn"], fail: ["x", "bad"] };
function qaCounts(qa) { const n = s => qa.items.filter(i => s.includes(i.status)).length; return { bad: n(["fail"]), review: n(["warn", "unsure"]), ok: n(["pass"]) }; }
function qaChip(k) {
  if (!k.qa) return `<button class="qachip" data-qa>${I("shield", 13)}Check</button>`;
  const n = qaCounts(k.qa), label = n.bad ? `${n.bad} issue${n.bad > 1 ? "s" : ""}` : n.review ? `${n.review} to review` : "Ready";
  return `<button class="qachip ${k.qa.verdict}" data-qa title="Open the checks">${I(k.qa.verdict === "pass" ? "check" : k.qa.verdict === "warn" ? "alert" : "x", 13)}${label}</button>`;
}
function qaDialog(c, x, k, rerun) {
  const m = document.createElement("div"); m.className = "modal";
  const manual = c.checks.items.filter(i => i.enabled && i.kind === "manual");
  const row = i => `<div class="qa-item ${i.status}"><span class="qa-ico ${QA_ICON[i.status][1]}">${I(QA_ICON[i.status][0], 15)}</span><div><b>${esc(i.label)}</b><p>${esc(i.detail)}</p></div></div>`;
  const q = k.qa, n = qaCounts(q);
  const order = ["fail", "warn", "unsure", "pass"], sorted = [...q.items].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  const attention = sorted.filter(i => i.status !== "pass"), passed = sorted.filter(i => i.status === "pass");
  m.innerHTML = `<div class="box wide"><h2>Checks for ${esc(k.title)}</h2>
    <div class="verdict ${q.verdict}">${I(q.verdict === "pass" ? "check" : q.verdict === "warn" ? "alert" : "x", 16)}<span>${q.verdict === "pass" ? "Looks ready to post" : q.verdict === "warn" ? "Worth a look before you post" : "Fix these before you post"}</span></div>
    ${q.summary ? `<p class="hint" style="margin:10px 0 4px">${esc(q.summary)}</p>` : ""}
    <div class="qa-list">${attention.map(row).join("") || '<p class="hint">Nothing needs attention.</p>'}</div>
    <details class="qa-pass"><summary>${n.ok} passed</summary><div class="qa-list">${passed.map(row).join("")}</div></details>
    ${manual.length ? `<div class="qa-manual"><h3>Before you post</h3>${manual.map(i => `<label class="check"><input type="checkbox"> ${esc(i.text)}</label>`).join("")}</div>` : ""}
    <div class="row end" style="margin-top:16px"><span class="mute" style="margin-right:auto">${esc(q.tokens || "")} · ${new Date(q.at * 1000).toLocaleString()}</span>
      <button class="btn" id="qre">${I("shield", 14)}Check again</button><button class="btn primary" id="qclose">Close</button></div></div>`;
  document.body.appendChild(m);
  const close = () => m.remove(); m.onclick = e => e.target === m && close(); $("#qclose", m).onclick = close;
  $("#qre", m).onclick = () => { close(); rerun(); };
}
async function runQA(c, x, k, chip) {
  if (chip) { chip.disabled = true; chip.innerHTML = `${I("shield", 13)}Checking…`; }
  try {
    k.qa = await api(`/api/campaigns/${c.id}/content/${x.id}/clips/${encodeURIComponent(k.file)}/qa`, { method: "POST" });
    if (chip) chip.outerHTML = qaChip(k);
    return k.qa;
  } catch (e) { toast(e.message, "bad"); if (chip) { chip.disabled = false; chip.outerHTML = qaChip(k); } }
}

const clipCard = cid => k => `
  <article class="clip ${k.status}" data-file="${esc(k.file)}" data-status="${k.status}">
    <div class="vid"><video src="/media/${cid}/${encodeURIComponent(k.file)}?v=${Math.round(k.start * 100)}-${Math.round(k.end * 100)}-${Math.round((k.duration || 0) * 10)}" controls preload="metadata"></video>
      <span class="badge">#${k.file.match(/_(\d+)(?:_e(\d+))?\.mp4$/)?.slice(1).filter(Boolean).join(" · edit ") ?? "?"}</span>
      <label class="sel" title="Select to edit"><input type="checkbox" class="pick" aria-label="Select clip"><span>${I("scissors", 13)}</span></label></div>
    <div class="cbody">
      <h4>${esc(k.title)}</h4>
      <div class="cmeta"><span>${tc(k.start)}–${tc(k.end)}</span><span>·</span><span>${Math.round(k.duration || k.end - k.start)}s</span>${k.score ? `<span>·</span><span class="score">score ${k.score}</span>` : ""}</div>
      <div class="tags">${(k.tags || []).map(t => `<span class="tag t-${t}">${TAGL[t] || t}</span>`).join("")}${qaChip(k)}</div>
      <div class="cap" title="Click to expand"><p>${esc(k.caption)}</p></div>
      <div class="seg">${STATUSES.map(s => `<button data-s="${s}" class="${k.status === s ? "on" : ""}">${s[0].toUpperCase() + s.slice(1)}</button>`).join("")}</div>
      <div class="foot"><label class="pay" title="Amount paid"><span>$</span><input type="number" step="0.01" placeholder="paid" value="${k.payout || ""}" aria-label="Amount paid"></label>
        <button class="btn sm cp" title="Copy caption">${I("copy", 13)}Copy</button>
        <a class="btn sm ic" href="/media/${cid}/${encodeURIComponent(k.file)}" download title="Download clip">${I("download", 14)}</a></div>
    </div>
  </article>`;

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
  if (videos) $("#status").textContent = `${videos} video${videos > 1 ? "s" : ""} added as content. Find ${videos > 1 ? "them" : "it"} in the sidebar.`;
  if (isBrief || (sawBrief && !hadRules)) await analyze(cid);
}

// ---------- generation + live activity ----------
const logHtml = lines => lines.map(l => {
  const m = l.match(/^\[(\d\d:\d\d)\] (.*)$/), t = m ? m[1] : "", msg = m ? m[2] : l;
  const cls = /^ERROR/.test(msg) ? "err" : /^(Finished|All done)/.test(msg) ? "ok" : "";
  return `<div class="ll ${cls}"><time>${t}</time><span>${esc(msg)}</span></div>`;
}).join("");

async function attachJob(cid, xid) {
  const loop = ++jobLoop;  // only the newest loop may update the page
  let was = false;
  for (;;) {
    let j;
    try { j = await api(`/api/campaigns/${cid}/content/${xid}/job`); } catch { return; }
    if (loop !== jobLoop || !$("#log")) return;
    if (!j.id) return;
    $(".activity").classList.add("live");
    const running = j.state === "running";
    const log = $("#log"), stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.innerHTML = logHtml(j.log) || '<div class="none">Starting…</div>';
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
  } catch (e) { toast(e.message, "bad"); }
}

render();
