/* Clip editor: instant in-browser preview of an edit decision list, then a full-quality export.
   The preview plays a small copy of the video, jumping between segments, and draws the framing,
   captions and text on a canvas. Nothing is rendered until you press Export. */

const CW = 360, CH = 640, K = CW / 1080;           // canvas size and scale from the 1080x1920 export
const SEG_COLORS = ["#6d7cff", "#4cc38a", "#e2a336", "#d46bd9", "#4fb6d9", "#ef6b6b"];
const fT = t => { t = Math.max(0, t); return `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`; };
const pT = s => { s = String(s).trim(); if (!s) return NaN; const p = s.split(":"); return p.length === 2 ? (+p[0]) * 60 + (+p[1]) : +p[0]; };
const clone = o => JSON.parse(JSON.stringify(o));

let ED = null;      // the open editing session

window.editorCleanup = () => {
  if (!ED) return;
  ED.dead = true; cancelAnimationFrame(ED.raf); clearTimeout(ED.draftTimer);
  document.removeEventListener("keydown", ED.onKey);
  ED.pv && ED.pv.pause();
  ED = null;
};

async function renderEditor(c, x, file) {
  const clip = x.clips.find(k => k.file === file);
  if (!clip) return go(c.id, x.id);
  const base = `/api/campaigns/${c.id}/content/${x.id}/clips/${encodeURIComponent(file)}/editor`;
  $("#main").innerHTML = `<div class="editor"><a class="crumb" href="#/${c.id}/${x.id}">${I("back", 14)}${esc(x.title)}</a>
    <header class="ph"><div><h1>Edit clip</h1><p class="sub">${esc(clip.title)}</p></div></header>
    <section class="card" id="prep"><header><h2>Getting your clip ready</h2></header>
      <p class="hint" id="prepmsg">Loading…</p><div class="prog indet"><div></div></div></section></div>`;
  const dead = () => !$("#prep");
  let info;
  try {
    info = await api(base);
    if (!info.ready) {
      $("#prepmsg").textContent = "Building a small preview copy of the video so editing is instant. This takes a moment, once per clip.";
      const { job, ready } = await api(`${base}/prepare`, { method: "POST" });
      for (let j = job && await api(`/api/jobs/${job}`); j && !ready; j = await api(`/api/jobs/${job}`)) {
        if (dead()) return;
        const bar = $("#prep .prog"); bar.classList.toggle("indet", j.percent == null);
        if (j.percent != null) $("#prep .prog div").style.width = j.percent + "%";
        if (j.state === "error") throw new Error(j.error);
        if (j.state === "done") break;
        await new Promise(r => setTimeout(r, 700));
      }
      info = await api(base);
    }
  } catch (e) {
    if (!dead()) $("#prepmsg").textContent = "Couldn't open the editor: " + e.message;
    return;
  }
  if (dead()) return;
  mountEditor(c, x, clip, info, base);
}

function mountEditor(c, x, clip, info, base) {
  const E = ED = { c, x, clip, info, base, edl: clone(info.edl), hist: [], redo: [], sel: 0, t: 0, playing: false,
                   opening: (info.edl.tags || []).includes("hook") ? "hook" : (info.edl.tags || []).includes("loop") ? "loop" : "none",
                   keyword: (info.edl.tags || []).includes("keyword") };
  E.edl.captions = E.edl.captions !== false;
  const S = info.settings;
  $("#main").innerHTML = `<div class="editor">
    <a class="crumb" href="#/${c.id}/${x.id}">${I("back", 14)}${esc(x.title)}</a>
    <header class="ph"><div><h1>Edit clip</h1><p class="sub">${esc(clip.title)} · source ${tc(info.lo)}–${tc(info.hi)} available</p></div>
      <div class="row"><button class="btn ghost" id="undo" title="Undo (⌘Z)">Undo</button><button class="btn ghost" id="redo" title="Redo (⇧⌘Z)">Redo</button>
        <button class="btn" id="reset">Start over</button><button class="btn primary" id="export">${I("download", 15)}Export new clip</button></div></header>
    <div class="ed-grid">
      <div class="ed-stage"><div class="phone"><canvas id="cv" width="${CW}" height="${CH}"></canvas><div class="loading" id="loading">Loading preview…</div></div>
        <div class="transport"><button class="btn" id="play" style="width:84px">${I("youtube", 15)}Play</button>
          <input type="range" id="scrub" min="0" max="1000" value="0" aria-label="Position"><span class="mono" id="tcode"></span></div>
        <label class="check"><input type="checkbox" id="loopprev"> Loop the preview</label></div>
      <div class="ed-side">
        <section class="card"><header><h2>AI styles</h2></header>
          <p class="hint">Claude picks the lines and the keyword. It only chooses where to cut and the order. The words are never changed.</p>
          <div class="field" style="margin-bottom:12px"><span>Opening</span>
            <div class="seg wide" id="opening">${[["none", "Normal"], ["hook", "Punchline first"], ["loop", "Seamless loop"]].map(([v, t]) => `<button data-v="${v}" class="${E.opening === v ? "on" : ""}">${t}</button>`).join("")}</div></div>
          <label class="check"><input type="checkbox" id="kwon" ${E.keyword ? "checked" : ""}> Keyword on screen</label>
          <div class="row" style="margin-top:12px"><button class="btn" id="suggest">Suggest with AI</button><span class="mute" id="sugmsg"></span></div>
          <div class="note" id="notes"></div></section>
        <section class="card"><header><h2>Text on screen</h2><button class="btn sm" id="addtext">${I("plus", 13)}Add text</button></header><div id="overlays"></div></section>
        <section class="card"><header><h2>Sequence</h2><div class="row"><label class="check"><input type="checkbox" id="caps" ${E.edl.captions ? "checked" : ""}> Captions</label>
          <button class="btn sm" id="addseg">${I("plus", 13)}Add segment</button></div></header>
          <p class="hint" style="margin-bottom:10px">Plays top to bottom. <b>S</b> splits at the playhead, <b>Delete</b> removes the selected piece.</p><div id="segs"></div></section>
      </div></div>
    <section class="card"><header><h2>Source timeline</h2><span class="mute">Drag the edges of the selected piece to trim. Click a piece to select it.</span></header>
      <div class="strip" id="strip"></div></section>
    <div class="exporting" id="exporting" hidden><div class="box"><h2>Exporting</h2><p class="hint" id="exmsg">Starting…</p><div class="prog"><div id="exbar"></div></div></div></div>
    <video id="pv" playsinline preload="auto" style="position:absolute;left:-9999px;width:2px;height:2px;opacity:0"></video></div>`;

  const cv = $("#cv"), ctx = cv.getContext("2d"), pv = E.pv = $("#pv");
  const tiny = document.createElement("canvas"); tiny.width = 36; tiny.height = 64; const tctx = tiny.getContext("2d");
  pv.src = info.proxy;
  pv.addEventListener("loadeddata", () => { $("#loading").hidden = true; seek(E.t); });

  // ---------- timeline maths ----------
  const segs = () => E.edl.segments;
  const lens = () => segs().map(s => s.end - s.start);
  const total = () => lens().reduce((a, b) => a + b, 0);
  const offs = () => { let o = 0; return segs().map(s => { const r = o; o += s.end - s.start; return r; }); };
  const srcAt = t => { const o = offs(), L = lens(); for (let i = 0; i < o.length; i++) if (t < o[i] + L[i] - 1e-6 || i === o.length - 1) return { i, src: segs()[i].start + Math.min(Math.max(t - o[i], 0), L[i]) }; return { i: 0, src: info.lo }; };
  const toOut = (i, src) => offs()[i] + (src - segs()[i].start);
  const words = () => {  // output-time words, as the export maps them
    const key = JSON.stringify(segs()); if (E._wk === key) return E._w;
    const out = []; let off = 0;
    for (const s of segs()) { for (const w of info.words) if (s.start - 0.05 <= (w.start + w.end) / 2 && (w.start + w.end) / 2 <= s.end) out.push({ word: w.word, start: Math.max(w.start - s.start, 0) + off, end: Math.min(w.end - s.start + off, off + s.end - s.start) }); off += s.end - s.start; }
    E._wk = key; return E._w = out;
  };

  // ---------- drawing ----------
  function textOut(txt, x0, y0, size, fill, mode = "outline", fam = "-apple-system, Arial, sans-serif") {
    ctx.font = `800 ${size}px ${fam}`; ctx.lineJoin = "round";
    if (mode === "outline") { ctx.lineWidth = Math.max(2, size * 0.15); ctx.strokeStyle = "#000"; ctx.strokeText(txt, x0, y0); }
    if (mode === "shadow") { ctx.shadowColor = "rgba(0,0,0,.6)"; ctx.shadowBlur = size * 0.12; ctx.shadowOffsetY = size * 0.04; }
    ctx.fillStyle = fill; ctx.fillText(txt, x0, y0); ctx.shadowColor = "transparent"; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
  }
  function draw() {
    if (E.dead) return;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, CW, CH);
    if (pv.readyState >= 2 && pv.videoWidth) {
      const vw = pv.videoWidth, vh = pv.videoHeight, keys = info.track && info.track.keys && info.track.keys.length ? info.track.keys : null;
      if (keys && (S.frame_mode === "follow" || S.frame_mode === "follow45")) {  // crop a window that follows the speaker
        const ts = srcAt(E.t).src; let cx = keys[keys.length - 1][1];
        if (ts <= keys[0][0]) cx = keys[0][1]; else for (let n = 0; n < keys.length - 1; n++) if (ts < keys[n + 1][0]) { const [t0, c0] = keys[n], [t1, c1] = keys[n + 1]; cx = t1 - t0 < 1e-6 ? c0 : c0 + (c1 - c0) * (ts - t0) / (t1 - t0); break; }
        const sw = vh * (S.frame_mode === "follow" ? 9 / 16 : 4 / 5), sx = Math.min(Math.max(cx * vw - sw / 2, 0), vw - sw);
        if (S.frame_mode === "follow") ctx.drawImage(pv, sx, 0, sw, vh, 0, 0, CW, CH);
        else {
          const sc = Math.max(36 / vw, 64 / vh); tctx.drawImage(pv, (36 - vw * sc) / 2, (64 - vh * sc) / 2, vw * sc, vh * sc);
          ctx.imageSmoothingEnabled = true; ctx.drawImage(tiny, 0, 0, CW, CH);
          const h = CW * vh / sw; ctx.drawImage(pv, sx, 0, sw, vh, 0, (CH - h) / 2, CW, h);
        }
      } else if (S.frame_mode === "fill") { const sc = Math.max(CW / vw, CH / vh); ctx.drawImage(pv, (CW - vw * sc) / 2, (CH - vh * sc) / 2, vw * sc, vh * sc); }
      else {
        const sc = Math.max(36 / vw, 64 / vh); tctx.drawImage(pv, (36 - vw * sc) / 2, (64 - vh * sc) / 2, vw * sc, vh * sc);
        ctx.imageSmoothingEnabled = true; ctx.drawImage(tiny, 0, 0, CW, CH);
        const h = CW * vh / vw; ctx.drawImage(pv, 0, (CH - h) / 2, CW, h);
      }
    }
    const t = E.t;
    if (E.edl.captions) {  // chunks of N words (campaign setting), the spoken word highlighted
      const w = words(), per = S.cap_words || 3; let j = -1;
      for (let n = 0; n < w.length; n++) { const e = (n % per !== per - 1 && w[n + 1]) ? w[n + 1].start : w[n].end; if (w[n].start <= t && t < e) { j = n; break; } }
      if (j >= 0) {
        const c0 = j - j % per, size = (S.cap_size || 84) * K, up = S.cap_upper !== false, fam = `"${S.cap_font || "Inter"}", -apple-system, Arial, sans-serif`;
        const ch = w.slice(c0, c0 + per).map(x => up ? x.word.toUpperCase() : x.word), mode = S.cap_style || "outline";
        ctx.font = `800 ${size}px ${fam}`;
        const gap = ctx.measureText(" ").width, widths = ch.map(s => ctx.measureText(s).width), tw = widths.reduce((a, b) => a + b, 0) + gap * (ch.length - 1);
        const y = S.cap_pos === "mid" ? CH / 2 + size * 0.35 : S.cap_pos === "high" ? 380 * K + size * 0.85 : CH - 520 * K;
        let x0 = (CW - tw) / 2; ctx.textBaseline = "alphabetic";
        if (mode === "box") { ctx.fillStyle = "#181818"; const pad = size * 0.17; ctx.fillRect(x0 - pad, y - size * 0.92, tw + pad * 2, size * 1.32); }
        ch.forEach((s, n) => { textOut(s, x0, y, size, n === j - c0 ? (S.cap_highlight || "#FFE500") : (S.cap_color || "#fff"), mode, fam); x0 += widths[n] + gap; });
      }
    }
    for (const o of E.edl.overlays) {
      if (!o.text.trim() || t < o.start || t >= o.start + o.dur) continue;
      const a = Math.min(1, (t - o.start) / 0.15, (o.start + o.dur - t) / 0.2), size = 104 * K * (0.88 + 0.12 * Math.min(1, (t - o.start) / 0.18));
      ctx.globalAlpha = Math.max(0, a); ctx.font = `800 ${size}px -apple-system, Arial, sans-serif`; ctx.textAlign = "center";
      const lines = []; let cur = "";
      for (const wd of o.text.toUpperCase().split(" ")) { const tr = cur ? cur + " " + wd : wd; if (ctx.measureText(tr).width > CW - 70 * K * 2 && cur) { lines.push(cur); cur = wd; } else cur = tr; }
      lines.push(cur);
      const lh = size * 1.15, y0 = o.pos === "center" ? CH / 2 - (lines.length - 1) * lh / 2 + size * 0.35 : o.pos === "low" ? CH - 800 * K - (lines.length - 1) * lh : 300 * K + size * 0.85;
      lines.forEach((ln, n) => textOut(ln, CW / 2, y0 + n * lh, size, "#fff")); ctx.textAlign = "left"; ctx.globalAlpha = 1;
    }
  }

  // ---------- playback: play the small copy, jumping between segments ----------
  function seek(t) {
    E.t = Math.min(Math.max(t, 0), total()); const { i, src } = srcAt(E.t); E.i = i;
    pv.currentTime = Math.max(0, src - info.lo); updateUI(); draw();
  }
  pv.addEventListener("seeked", () => { draw(); });
  function frame() {
    if (E.dead || !E.playing) return;
    const s = segs()[E.i]; if (!s) return stop();
    const src = pv.currentTime + info.lo;
    if (src >= s.end - 0.02 || pv.ended) {
      if (E.i + 1 < segs().length) { E.i++; pv.currentTime = segs()[E.i].start - info.lo; }
      else if ($("#loopprev").checked) { E.i = 0; pv.currentTime = segs()[0].start - info.lo; }
      else { E.t = total(); stop(); updateUI(); draw(); return; }
    } else E.t = toOut(E.i, src);
    updateUI(); draw(); E.raf = requestAnimationFrame(frame);
  }
  function play() { if (E.t >= total() - 0.05) seek(0); E.playing = true; pv.play().catch(() => {}); $("#play").lastChild.textContent = "Pause"; E.raf = requestAnimationFrame(frame); }
  function stop() { E.playing = false; pv.pause(); cancelAnimationFrame(E.raf); $("#play").lastChild.textContent = "Play"; }
  $("#play").onclick = () => E.playing ? stop() : play();
  $("#scrub").oninput = e => { if (E.playing) stop(); seek(e.target.value / 1000 * total()); };

  // ---------- state changes with undo ----------
  const snap = () => JSON.stringify({ edl: E.edl, opening: E.opening, keyword: E.keyword });
  function mutate(fn) {
    if (E.playing) stop();
    E.hist.push(snap()); if (E.hist.length > 80) E.hist.shift(); E.redo = [];
    fn(E.edl); clampAll(); E.t = Math.min(E.t, total()); refresh(); scheduleDraft();
  }
  function restore(s) { const o = JSON.parse(s); E.edl = o.edl; E.opening = o.opening; E.keyword = o.keyword; E.sel = Math.min(E.sel, segs().length - 1); refresh(); scheduleDraft(); }
  $("#undo").onclick = () => { if (!E.hist.length) return; E.redo.push(snap()); restore(E.hist.pop()); };
  $("#redo").onclick = () => { if (!E.redo.length) return; E.hist.push(snap()); restore(E.redo.pop()); };
  $("#reset").onclick = () => mutate(e => { Object.assign(e, clone(info.original)); E.opening = "none"; E.keyword = false; E.sel = 0; });
  function clampAll() {
    for (const s of segs()) { s.start = Math.min(Math.max(s.start, info.lo), info.hi - 0.3); s.end = Math.min(Math.max(s.end, s.start + 0.3), info.hi); s.start = +s.start.toFixed(2); s.end = +s.end.toFixed(2); }
    E.sel = Math.min(E.sel, segs().length - 1);
  }
  function scheduleDraft() {
    clearTimeout(E.draftTimer);
    E.draftTimer = setTimeout(() => api(`/api/campaigns/${c.id}/content/${x.id}/clips/${encodeURIComponent(clip.file)}`, { method: "PATCH", body: { draft: E.edl } }).catch(() => {}), 900);
  }

  // ---------- panels ----------
  function updateUI() {
    const tot = total(); $("#scrub").value = tot ? Math.round(E.t / tot * 1000) : 0;
    $("#tcode").textContent = `${fT(E.t)} / ${fT(tot)}`;
    const ph = $("#playhead"); if (ph) ph.style.left = ((srcAt(E.t).src - info.lo) / (info.hi - info.lo) * 100) + "%";
    $("#undo").disabled = !E.hist.length; $("#redo").disabled = !E.redo.length;
  }
  function renderSegs() {
    const L = lens(), o = offs();
    $("#segs").innerHTML = segs().map((s, i) => `<div class="segrow ${i === E.sel ? "on" : ""}" data-i="${i}">
      <span class="dotc" style="background:${SEG_COLORS[i % SEG_COLORS.length]}">${i + 1}</span>
      <input class="tin" data-k="start" value="${fT(s.start)}" aria-label="Start"><span class="mute">→</span><input class="tin" data-k="end" value="${fT(s.end)}" aria-label="End">
      <span class="mute len">${L[i].toFixed(1)}s</span>
      <button class="icon-btn" data-a="up" title="Move earlier" ${i === 0 ? "disabled" : ""}>${I("chevron", 14).replace("<svg", '<svg style="transform:rotate(-90deg)"')}</button>
      <button class="icon-btn" data-a="down" title="Move later" ${i === segs().length - 1 ? "disabled" : ""}>${I("chevron", 14).replace("<svg", '<svg style="transform:rotate(90deg)"')}</button>
      <button class="icon-btn" data-a="split" title="Split at the playhead (S)">${I("film", 14)}</button>
      <button class="icon-btn" data-a="del" title="Delete (Del)" ${segs().length < 2 ? "disabled" : ""}>${I("trash", 14)}</button></div>`).join("");
    $$(".segrow", $("#segs")).forEach(row => {
      const i = +row.dataset.i;
      row.onclick = e => { if (e.target.closest("button,input")) return; E.sel = i; renderSegs(); renderStrip(); seek(o[i]); };
      $$(".tin", row).forEach(inp => {
        inp.onfocus = () => { E.sel = i; $$(".segrow").forEach((r, n) => r.classList.toggle("on", n === i)); renderStrip(); };
        inp.onchange = () => { const v = pT(inp.value); if (isNaN(v)) return renderSegs(); mutate(e => e.segments[i][inp.dataset.k] = v); };
        inp.onkeydown = e => { if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); const v = pT(inp.value) + (e.key === "ArrowUp" ? 1 : -1) * (e.shiftKey ? 1 : 0.1); mutate(ed => ed.segments[i][inp.dataset.k] = v); } };
      });
      $$("[data-a]", row).forEach(b => b.onclick = () => ({ up: () => moveSeg(i, -1), down: () => moveSeg(i, 1), split: () => splitSeg(), del: () => delSeg(i) })[b.dataset.a]());
    });
  }
  const moveSeg = (i, d) => mutate(e => { const [s] = e.segments.splice(i, 1); e.segments.splice(i + d, 0, s); E.sel = i + d; });
  const delSeg = i => segs().length > 1 && mutate(e => { e.segments.splice(i, 1); E.sel = Math.max(0, i - 1); });
  function splitSeg() {
    const { i, src } = srcAt(E.t), s = segs()[i];
    if (!s || src < s.start + 0.2 || src > s.end - 0.2) return toast("Move the playhead inside a piece to split it", "bad");
    mutate(e => { e.segments.splice(i, 1, { start: s.start, end: +src.toFixed(2) }, { start: +src.toFixed(2), end: s.end }); E.sel = i + 1; });
  }
  $("#addseg").onclick = () => { const src = srcAt(E.t).src; mutate(e => { e.segments.push({ start: src, end: Math.min(src + 4, info.hi) }); E.sel = e.segments.length - 1; }); };
  function renderOverlays() {
    $("#overlays").innerHTML = E.edl.overlays.map((o, i) => `<div class="ovrow" data-i="${i}">
      <input class="otext" value="${esc(o.text)}" placeholder="Keyword or title" maxlength="80" aria-label="Text">
      <div class="row"><label class="mini-f">Start<input class="ostart" type="number" step="0.1" min="0" value="${o.start}"></label>
        <label class="mini-f">Length<input class="odur" type="number" step="0.1" min="0.3" value="${o.dur}"></label>
        <label class="mini-f">Position<select class="opos">${[["top", "Top"], ["center", "Middle"], ["low", "Lower"]].map(([v, t]) => `<option value="${v}" ${o.pos === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <button class="icon-btn orm" title="Remove">${I("x", 15)}</button></div></div>`).join("") || '<p class="hint" style="margin:0">No text. Add a keyword that appears at the start, or use the AI keyword option.</p>';
    $$(".ovrow").forEach(r => {
      const i = +r.dataset.i, set = (k, v) => mutate(e => e.overlays[i][k] = v);
      $(".otext", r).onchange = e => set("text", e.target.value); $(".ostart", r).onchange = e => set("start", Math.max(0, +e.target.value || 0));
      $(".odur", r).onchange = e => set("dur", Math.max(0.3, +e.target.value || 3)); $(".opos", r).onchange = e => set("pos", e.target.value);
      $(".orm", r).onclick = () => mutate(e => e.overlays.splice(i, 1));
    });
  }
  $("#addtext").onclick = () => mutate(e => e.overlays.push({ text: "", start: 0, dur: Math.min(3, total()), pos: "top" }));
  $("#caps").onchange = e => mutate(ed => ed.captions = e.target.checked);

  function renderStrip() {
    const R = info.hi - info.lo, lanes = []; const place = segs().map((s, i) => { let l = 0; while (lanes[l] && lanes[l].some(([a, b]) => s.start < b && s.end > a)) l++; (lanes[l] = lanes[l] || []).push([s.start, s.end]); return l; });
    const ticks = []; for (let t = Math.ceil(info.lo / 5) * 5; t <= info.hi; t += 5) ticks.push(t);
    $("#strip").innerHTML = `<div class="ruler">${ticks.map(t => `<span style="left:${(t - info.lo) / R * 100}%">${tc(t)}</span>`).join("")}</div>
      <div class="lanes" style="height:${Math.max(1, lanes.length) * 30 + 8}px">${segs().map((s, i) => `<div class="blk ${i === E.sel ? "on" : ""}" data-i="${i}" style="left:${(s.start - info.lo) / R * 100}%;width:${(s.end - s.start) / R * 100}%;top:${place[i] * 30 + 4}px;background:${SEG_COLORS[i % SEG_COLORS.length]}"><b>${i + 1}</b>
        ${i === E.sel ? '<i class="h l"></i><i class="h r"></i>' : ""}</div>`).join("")}<div class="playhead" id="playhead"></div></div>`;
    $$(".blk", $("#strip")).forEach(b => {
      const i = +b.dataset.i;
      b.onpointerdown = e => {
        const edge = e.target.classList.contains("h") ? (e.target.classList.contains("l") ? "start" : "end") : null, box = $(".lanes", $("#strip")).getBoundingClientRect();
        const t0 = info.lo + (e.clientX - box.left) / box.width * R;
        if (!edge) { E.sel = i; renderSegs(); renderStrip(); if (E.playing) stop(); const s = segs()[i]; seek(toOut(i, Math.min(Math.max(t0, s.start), s.end - 0.05))); return; }
        e.preventDefault(); if (E.playing) stop(); b.setPointerCapture(e.pointerId); const before = snap(); let moved = false;
        b.onpointermove = ev => {
          const t = Math.round((info.lo + (ev.clientX - box.left) / box.width * R) * 20) / 20, s = segs()[i];
          if (edge === "start") s.start = Math.min(Math.max(t, info.lo), s.end - 0.3); else s.end = Math.max(Math.min(t, info.hi), s.start + 0.3);
          moved = true; renderStrip(); renderSegs(); E.t = Math.min(E.t, total()); seek(toOut(i, edge === "start" ? s.start : s.end - 0.05));
        };
        b.onpointerup = () => { b.onpointermove = b.onpointerup = null; if (moved) { E.hist.push(before); E.redo = []; scheduleDraft(); } renderStrip(); updateUI(); };
      };
    });
    updateUI();
  }
  const refresh = () => { renderSegs(); renderOverlays(); renderStrip(); $("#caps").checked = E.edl.captions; $$("#opening button").forEach(b => b.classList.toggle("on", b.dataset.v === E.opening)); $("#kwon").checked = E.keyword; seek(E.t); };

  // ---------- AI styles ----------
  $$("#opening button").forEach(b => b.onclick = () => { E.opening = b.dataset.v; $$("#opening button").forEach(o => o.classList.toggle("on", o === b)); });
  $("#kwon").onchange = e => E.keyword = e.target.checked;
  $("#suggest").onclick = async () => {
    const modes = [E.opening !== "none" ? E.opening : null, E.keyword ? "keyword" : null].filter(Boolean);
    if (!modes.length) return void ($("#sugmsg").textContent = "Pick an opening style or Keyword first.");
    const btn = $("#suggest"); btn.disabled = true; $("#sugmsg").textContent = "Claude is choosing…";
    try {
      const r = await api(`${base}/suggest`, { method: "POST", body: { modes } });
      mutate(e => Object.assign(e, r.edl, { captions: e.captions })); E.sel = 0; refresh();
      $("#sugmsg").textContent = ""; $("#notes").textContent = r.notes.join("  ·  ");
      seek(0);
    } catch (e) { $("#sugmsg").textContent = e.message; }
    btn.disabled = false;
  };

  // ---------- export ----------
  $("#export").onclick = async () => {
    if (E.playing) stop();
    const ex = $("#exporting"); ex.hidden = false; $("#exmsg").textContent = "Starting…"; $("#exbar").style.width = "0%";
    try {
      const { job } = await api(`${base}/export`, { method: "POST", body: { edl: { ...E.edl, tags: [E.opening !== "none" ? E.opening : null, E.edl.overlays.some(o => o.text.trim()) ? "keyword" : null].filter(Boolean) } } });
      for (;;) {
        if (E.dead) return;
        const j = await api(`/api/jobs/${job}`);
        $("#exmsg").textContent = (j.log[j.log.length - 1] || "").replace(/^\[\d\d:\d\d\] /, "");
        if (j.percent != null) $("#exbar").style.width = j.percent + "%";
        if (j.state === "error") throw new Error(j.error);
        if (j.state === "done") break;
        await new Promise(r => setTimeout(r, 800));
      }
      await api(`/api/campaigns/${c.id}/content/${x.id}/clips/${encodeURIComponent(clip.file)}`, { method: "PATCH", body: { draft: null } }).catch(() => {});
      toast("Exported as a new clip"); go(c.id, x.id);
    } catch (e) { ex.hidden = true; toast(e.message, "bad"); }
  };

  // ---------- keyboard ----------
  E.onKey = e => {
    if (e.target.closest("input,textarea,select")) { return; }
    const meta = e.metaKey || e.ctrlKey;
    if (e.code === "Space") { e.preventDefault(); $("#play").click(); }
    else if (e.key === "s" && !meta) splitSeg();
    else if ((e.key === "Delete" || e.key === "Backspace") && segs().length > 1) delSeg(E.sel);
    else if (meta && e.key.toLowerCase() === "z") { e.preventDefault(); (e.shiftKey ? $("#redo") : $("#undo")).click(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); if (E.playing) stop(); seek(E.t + (e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 1 : 0.1)); }
  };
  document.addEventListener("keydown", E.onKey);
  refresh();
}
