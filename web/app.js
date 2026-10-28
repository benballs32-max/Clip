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
  const total = clips.reduce((a, x) => a + (+x.payout || 0), 0);
  const count = s => clips.filter(x => x.status === s).length;
  $("#main").innerHTML = `
    <h2>${esc(c.name)}</h2>
    <div class="stats"><b>${clips.length}</b> clips · <b>${count("approved")}</b> approved · <b>${count("posted")}</b> posted · <b>$${total.toFixed(2)}</b> earned</div>
    <h3>Campaign brief</h3>
    <form id="f" class="grid">
      <label>Name<input name="name" value="${esc(c.name)}"></label>
      <label>Platform<select name="platform">${["TikTok","Instagram","YouTube Shorts","Any"].map(p => `<option ${p===c.platform?"selected":""}>${p}</option>`).join("")}</select></label>
      <label>Rate ($ per 1,000 views)<input name="rate_cpm" type="number" step="0.01" value="${c.rate_cpm}"></label>
      <label>Budget / payout notes<input name="budget_note" value="${esc(c.budget_note)}"></label>
      <label class="full">Rules / brief (Claude uses this to pick clips)<textarea name="rules">${esc(c.rules)}</textarea></label>
      <label>Hashtags (space separated)<input name="hashtags" value="${esc(c.hashtags.join(" "))}"></label>
      <label>Disclosure tag<input name="disclosure" value="${esc(c.disclosure)}"></label>
      <label>Min seconds<input name="min_seconds" type="number" value="${c.min_seconds}"></label>
      <label>Max seconds<input name="max_seconds" type="number" value="${c.max_seconds}"></label>
      <label>Clips per video<input name="clips_per_video" type="number" value="${c.clips_per_video}"></label>
      <label>Framing<select name="frame_mode"><option value="blur" ${c.frame_mode==="blur"?"selected":""}>Fit + blurred background</option><option value="fill" ${c.frame_mode==="fill"?"selected":""}>Fill (center crop)</option></select></label>
      <div class="full row"><button class="primary">Save</button><button type="button" id="del" class="danger">Delete campaign</button></div>
    </form>
    <h3>Source footage</h3>
    <div class="drop" id="drop">Drop videos here or <u>click to choose</u><input id="file" type="file" accept="video/*" multiple hidden></div>
    <ul class="src">${c.sources.map(s => `<li class="${c.sources_done.includes(s) ? "done" : ""}">${c.sources_done.includes(s) ? "✓" : "•"} ${esc(s)}</li>`).join("") || "<li>No footage yet.</li>"}</ul>
    <div class="row"><button class="primary" id="gen">Generate clips</button><span id="status" class="stats"></span></div>
    <pre class="log" id="log" hidden></pre>
    <h3>Clips</h3>
    <div class="clips">${clips.map(clipCard(id)).join("") || '<p class="empty">Nothing generated yet.</p>'}</div>`;
  bind(c);
}

const clipCard = id => x => `
  <div class="clip ${x.status}" data-file="${esc(x.file)}">
    <video src="/media/${id}/${encodeURIComponent(x.file)}" controls preload="metadata"></video>
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
  const drop = $("#drop"), file = $("#file");
  drop.onclick = () => file.click();
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); upload(c.id, e.dataTransfer.files); };
  file.onchange = () => upload(c.id, file.files);
  $("#gen").onclick = () => generate(c.id);
  document.querySelectorAll(".clip").forEach(el => {
    const f = encodeURIComponent(el.dataset.file), patch = body =>
      api(`/api/campaigns/${c.id}/clips/${f}`, { method: "PATCH", body });
    $(".st", el).onchange = async e => { await patch({ status: e.target.value }); el.className = `clip ${e.target.value}`; };
    $(".pay", el).onchange = e => patch({ payout: parseFloat(e.target.value) || 0 });
    $(".cp", el).onclick = () => navigator.clipboard.writeText($("p", el).textContent);
  });
}

function upload(id, files) {
  const st = $("#status"); let done = 0;
  const next = () => {
    if (done >= files.length) return open(id);
    const fd = new FormData(); fd.append("file", files[done]);
    const x = new XMLHttpRequest();
    x.open("POST", `/api/campaigns/${id}/sources`);
    x.upload.onprogress = e => st.textContent = `Uploading ${files[done].name}… ${Math.round(e.loaded / e.total * 100)}%`;
    x.onload = () => { done++; next(); };
    x.onerror = () => st.textContent = "Upload failed.";
    x.send(fd);
  };
  next();
}

async function generate(id) {
  const log = $("#log"), btn = $("#gen");
  btn.disabled = true; log.hidden = false; log.textContent = "Starting…";
  try {
    const { job } = await api(`/api/campaigns/${id}/generate`, { method: "POST" });
    for (;;) {
      await new Promise(r => setTimeout(r, 1500));
      const j = await api(`/api/jobs/${job}`);
      log.textContent = j.log.join("\n"); log.scrollTop = log.scrollHeight;
      if (j.state !== "running") break;
    }
    await open(id);
  } catch (e) { log.textContent = e.message; btn.disabled = false; }
}

loadList();
