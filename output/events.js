// Pixel Campus events (private beta)
//
// Loaded by index.html only when the beta is switched on for this device (?beta=1). Signed-in Brown
// students post events with a flyer, moderators approve them, and approved events appear as pins on the
// map. Every rule (who can post, see, approve, delete) is enforced by the database: supabase/schema.sql.
// This file is only the interface, so nothing here needs to be trusted.

const EV_SUPABASE_URL = "https://ioomfxnbjfavzgcaqhbf.supabase.co";
// The public "anon" key: safe to publish. On its own it can only do what the database rules allow.
const EV_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imlvb21meG5iamZhdnpnY2FxaGJmIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTIzODI0MzgsImV4cCI6MjEwNzk1ODQzOH0.LEiv414HEBSBpPtclAOn-OuFN6vPwr7ljByda0YAdAw";
const PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/";

const sb = supabase.createClient(EV_SUPABASE_URL, EV_SUPABASE_KEY, {
  auth: { persistSession: true, detectSessionInUrl: true, flowType: "implicit" },
});

const EV_LIMITS = { aheadDays: 60, maxDays: 14, fileBytes: 10 * 1024 * 1024, pdfKeepBytes: 2 * 1024 * 1024,
                    fullBytes: 600 * 1024, thumbBytes: 60 * 1024 };
const EV_COLORS = { now: "#e07a55", today: "#e9b949", later: "#7fa7d6" };
const EV_REPORT_REASONS = ["Spam or advertising", "Inappropriate content", "Wrong time or place", "Something else"];
// a pinned paper flyer, drawn inside the same tile frame as the dining pins
const POSTER = ["....RR.....", "..WWRRWWW..", "..WWWWWWW..", "..WkkkkkW..", "..WWWWWWW..",
                "..WkkkWWW..", "..WWWWWWW..", "..WkkkkWW..", "..WWWWWWW.."];

const EV = {
  session: null, isMod: false, events: [], signed: new Map(),
  open: false, view: "list", eventId: null, group: null,
  pick: false, draft: null, flyer: null, busy: false, msg: "", confirm: null, reporting: false,
};

// ---------------------------------------------------------------------------------------------
// Panel, button, styles
// ---------------------------------------------------------------------------------------------
document.head.insertAdjacentHTML("beforeend", `<style>
  #events .ev-tabs { display: flex; gap: 5px; flex-wrap: wrap; margin: 2px 0 8px; }
  #events .ev-account { color: var(--muted); font-size: 12px; margin: 0 0 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  #events .ev-account button { padding: 2px 8px; font-size: 12px; }
  #events .ev-post { width: 100%; margin: 4px 0 8px; background: var(--accent); color: #1c1a18; }
  #events h3 { margin: 6px 0 2px; font-size: 16px; }
  #events h4 { margin: 12px 0 4px; font-size: 13px; color: var(--accent); }
  #events .ev-row { width: 100%; display: grid; grid-template-columns: 48px 1fr; gap: 10px; align-items: center; text-align: left;
    background: transparent; border-radius: 0; border-bottom: 1px solid #3a3530; padding: 7px 2px; }
  #events .ev-row:hover { background: #3a3530; }
  #events .ev-row small { display: block; color: var(--muted); font-size: 12px; }
  #events .ev-thumb { width: 48px; height: 48px; object-fit: cover; background: #3a3530; border-radius: 3px; display: block; }
  #events .ev-flyer { width: 100%; display: block; border-radius: 4px; margin: 8px 0; background: #3a3530; min-height: 120px; }
  #events .ev-meta { color: var(--muted); font-size: 13px; margin: 2px 0; }
  #events .ev-desc { white-space: pre-wrap; font-size: 14px; margin: 8px 0; overflow-wrap: anywhere; }
  #events .ev-actions { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 4px; }
  #events .ev-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; margin-left: 6px; vertical-align: 1px; }
  #events .ev-badge.pending { background: #e9b949; color: #1c1a18; }
  #events .ev-badge.rejected, #events .ev-badge.removed { background: #8f3f3f; color: #fff; }
  #events .ev-badge.approved { background: #4f8f3f; color: #fff; }
  #events .ev-msg { color: #e9b949; font-size: 13px; margin: 6px 0; }
  #events .ev-empty { color: var(--muted); }
  #events label { display: block; margin: 9px 0 3px; font-size: 13px; color: var(--muted); }
  #events input, #events textarea, #events select { width: 100%; box-sizing: border-box; background: #2f2b27; color: var(--text);
    border: 1px solid #45403a; border-radius: 4px; padding: 6px 8px; font: inherit; font-size: 14px; }
  #events textarea { min-height: 80px; resize: vertical; }
  #events input[type=checkbox] { width: auto; margin-right: 6px; }
  #events .ev-inline { display: flex; gap: 6px; align-items: center; }
  #events .ev-inline > * { flex: 1; }
  #events .ev-preview { display: flex; gap: 10px; align-items: center; margin-top: 6px; font-size: 12px; color: var(--muted); }
  #events .ev-preview img { width: 64px; height: 64px; object-fit: cover; border-radius: 3px; }
  #events .ev-danger { background: #8f3f3f; }
  #ev-hint { top: 64px; left: 50%; transform: translateX(-50%); display: flex; gap: 10px; align-items: center; z-index: 6; }
  #ev-hint[hidden] { display: none; }
</style>`);

const evPanel = document.createElement("aside");
evPanel.id = "events";
evPanel.className = "panel side-panel";
evPanel.hidden = true;
evPanel.setAttribute("aria-label", "Campus events");
evPanel.innerHTML = '<div id="ev-body"></div>';
document.getElementById("dining").after(evPanel);
const evBody = document.getElementById("ev-body");

const evHint = document.createElement("div");
evHint.id = "ev-hint";
evHint.className = "panel";
evHint.hidden = true;
evHint.innerHTML = '<span>Tap the map where the event is</span><button data-act="cancel-pick">Cancel</button>';
document.body.appendChild(evHint);

const evButton = document.createElement("button");
evButton.id = "events-btn";
evButton.textContent = "Events";
evButton.setAttribute("aria-pressed", "false");
document.getElementById("controls").prepend(evButton);
evButton.onclick = () => (EV.open ? evClose() : evOpen("list"));
layoutPanels();

// ---------------------------------------------------------------------------------------------
// Time helpers (reuse the dining layer's Providence-time formatting)
// ---------------------------------------------------------------------------------------------
const evNow = () => diningNow().getTime();
function evState(e, now = evNow()) {
  const s = Date.parse(e.starts_at), en = Date.parse(e.ends_at);
  if (s <= now && now < en) return "now";
  return ymd(new Date(s)) === ymd(new Date(now)) ? "today" : "later";
}
function evWhen(e) {
  const s = new Date(e.starts_at), en = new Date(e.ends_at), now = new Date(evNow());
  const day = ymd(s) === ymd(now) ? "Today" : s.toLocaleDateString("en-US", { timeZone: TZ, weekday: "short", month: "short", day: "numeric" });
  return `${day} ${fmtTime(s)}–${fmtTime(en)}`;
}
const evOnMap = (e, now = evNow()) => e.status === "approved" && Date.parse(e.ends_at) > now
  && Date.parse(e.starts_at) < now + 7 * 864e5;
const evMine = e => EV.session && e.owner_id === EV.session.user.id;

// ---------------------------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------------------------
async function evLoad() {
  const { data, error } = await sb.from("events")
    .select("id,owner_id,title,description,building,pos_x,pos_y,starts_at,ends_at,status,reject_reason,created_at," +
            "profiles(display_name),flyers(image_path,thumb_path,pdf_path,width,height)")
    .order("starts_at");
  if (error) { console.warn("events load failed", error); EV.msg = "Couldn't load events right now."; return evRender(); }
  EV.events = data.map(e => ({ ...e, flyer: Array.isArray(e.flyers) ? e.flyers[0] || null : e.flyers,
                                poster: e.profiles?.display_name || "a student" }));
  await evSign(EV.events.filter(e => e.flyer).map(e => e.flyer.thumb_path));
  evRender();
  requestDraw();
}

// Flyers live in a private bucket, so images are shown through short-lived signed links
async function evSign(paths) {
  const need = paths.filter(p => p && !(EV.signed.get(p)?.until > Date.now()));
  if (!need.length) return;
  const { data, error } = await sb.storage.from("flyers").createSignedUrls(need, 3600);
  if (error) return console.warn("signing failed", error);
  for (const d of data) if (d.signedUrl) EV.signed.set(d.path, { url: d.signedUrl, until: Date.now() + 3500e3 });
}
const evUrl = path => EV.signed.get(path)?.url || "";

// ---------------------------------------------------------------------------------------------
// Account
// ---------------------------------------------------------------------------------------------
sb.auth.onAuthStateChange((_event, session) => {
  EV.session = session;
  setTimeout(evAfterAuth, 0);   // never await other Supabase calls inside this callback
});
async function evAfterAuth() {
  EV.isMod = EV.session ? (await sb.rpc("is_moderator")).data === true : false;
  if (EV.session && EV.view === "signin") EV.view = "list";
  await evLoad();
}

async function evSendLink(email) {
  email = email.trim().toLowerCase();
  if (!/^[^@\s]+@brown\.edu$/.test(email)) { EV.msg = "Use your @brown.edu email address."; return evRender(); }
  EV.busy = true; evRender();
  const { error } = await sb.auth.signInWithOtp({ email, options: { emailRedirectTo: location.origin + location.pathname } });
  EV.busy = false;
  EV.msg = error ? `Couldn't send the link: ${error.message}` : `Sign-in link sent to ${email}. Open it on this device.`;
  evRender();
}

// ---------------------------------------------------------------------------------------------
// Flyer shrinking: everything happens in the browser before upload
// ---------------------------------------------------------------------------------------------
let evPdfjs = null;
function evLoadScript(src) {
  return new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = fail;
    document.head.appendChild(s);
  });
}
async function evPdfLib() {
  if (!evPdfjs) {
    await evLoadScript(PDFJS + "pdf.min.js");
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS + "pdf.worker.min.js";
    evPdfjs = window.pdfjsLib;
  }
  return evPdfjs;
}

// Draw the source scaled to fit, then encode as WebP (JPEG where the browser can't make WebP),
// stepping quality down until the file fits. Re-encoding drops all photo metadata (GPS, camera).
async function evEncode(src, w, h, maxW, maxH, maxBytes) {
  const k = Math.min(1, maxW / w, maxH / h), cw = Math.max(1, Math.round(w * k)), ch = Math.max(1, Math.round(h * k));
  const c = document.createElement("canvas");
  c.width = cw; c.height = ch;
  const cx = c.getContext("2d");
  cx.fillStyle = "#ffffff"; cx.fillRect(0, 0, cw, ch);   // transparent PNGs get a white backing
  cx.imageSmoothingQuality = "high";
  cx.drawImage(src, 0, 0, cw, ch);
  let blob = null;
  for (const q of [0.82, 0.72, 0.6, 0.5, 0.4]) {
    blob = await new Promise(r => c.toBlob(r, "image/webp", q));
    if (!blob || blob.type !== "image/webp") blob = await new Promise(r => c.toBlob(r, "image/jpeg", q));
    if (blob.size <= maxBytes) break;
  }
  return { blob, w: cw, h: ch };
}

async function evShrink(file) {
  const allowed = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
  if (!allowed.includes(file.type)) throw new Error("Use a JPEG, PNG, WebP or PDF file.");
  if (file.size > EV_LIMITS.fileBytes) throw new Error("That file is over 10 MB.");
  let src, w, h;
  if (file.type === "application/pdf") {
    const pdfjs = await evPdfLib();
    const pdf = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
    const page = await pdf.getPage(1);
    const base = page.getViewport({ scale: 1 });
    const vp = page.getViewport({ scale: Math.min(4, 1200 / base.width) });
    src = document.createElement("canvas");
    src.width = Math.round(vp.width); src.height = Math.round(vp.height);
    await page.render({ canvasContext: src.getContext("2d"), viewport: vp }).promise;
    w = src.width; h = src.height;
  } else {
    src = await createImageBitmap(file, { imageOrientation: "from-image" });
    w = src.width; h = src.height;
  }
  const full = await evEncode(src, w, h, 1200, 1800, EV_LIMITS.fullBytes);
  const thumb = await evEncode(src, w, h, 300, 450, EV_LIMITS.thumbBytes);
  return { full, thumb, pdf: file.type === "application/pdf" ? file : null, name: file.name, origBytes: file.size };
}

const evKB = n => n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`;
const evExt = blob => (blob.type === "image/webp" ? "webp" : "jpg");

// ---------------------------------------------------------------------------------------------
// Posting
// ---------------------------------------------------------------------------------------------
function evLocalInput(d) {      // a Date as the value of a datetime-local input (viewer's clock)
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function evNewDraft() {
  const start = new Date(); start.setMinutes(0, 0, 0); start.setHours(start.getHours() + 2);
  const end = new Date(start.getTime() + 2 * 3600e3);
  return { title: "", description: "", building: "", pos: null, start: evLocalInput(start), end: evLocalInput(end), keepPdf: false };
}

function evValidate(d) {
  const s = new Date(d.start).getTime(), e = new Date(d.end).getTime(), now = Date.now();
  if (d.title.trim().length < 3) return "Give the event a title (at least 3 characters).";
  if (!d.pos) return "Choose where the event is on the map.";
  if (!(s && e)) return "Set a start and end time.";
  if (e <= s) return "The event has to end after it starts.";
  if (e < now) return "That end time has already passed.";
  if (s > now + EV_LIMITS.aheadDays * 864e5) return `Events can be posted at most ${EV_LIMITS.aheadDays} days ahead.`;
  if (e - s > EV_LIMITS.maxDays * 864e5) return `Events can last at most ${EV_LIMITS.maxDays} days.`;
  if (EV.flyer?.working) return "Wait for the flyer to finish processing.";
  return null;
}

function evPickAt(sx, sy) {
  const [wx, wy] = toWorld(sx, sy);
  if (edgeDist(wx, wy) < FOG_DEPTH) return;          // under the clouds: not a real spot
  const inside = buildings.find(b => wx >= b.bbox[0] && wx <= b.bbox[2] && wy >= b.bbox[1] && wy <= b.bbox[3]
    && b.polygons.some(p => inPoly(wx, wy, p)));
  let name = inside?.name;
  if (!name) {
    let best = null, bd = 45;                         // otherwise the nearest named building within 45 m
    for (const b of buildings) { const d = Math.hypot(b.center[0] - wx, b.center[1] - wy); if (d < bd) { best = b; bd = d; } }
    name = best ? `Outside ${best.name}` : "Outdoor spot";
  }
  Object.assign(EV.draft, { pos: [Math.round(wx * 10) / 10, Math.round(wy * 10) / 10], building: name.slice(0, 120) });
  evSetPick(false);
}

function evSetPick(on) {
  EV.pick = on;
  evHint.hidden = !on;
  evPanel.hidden = on || !EV.open;
  updatePanelState();
  if (on) evHint.style.top = Math.round(document.getElementById("controls").getBoundingClientRect().bottom + 8) + "px";
  if (!on) evRender();
  requestDraw();
}

async function evSubmit() {
  const d = EV.draft, problem = evValidate(d);
  if (problem) { EV.msg = problem; return evRender(); }
  EV.busy = true; EV.msg = "Posting…"; evRender();
  const uid = EV.session.user.id;
  const { data: row, error } = await sb.from("events").insert({
    title: d.title.trim(), description: d.description.trim(), building: d.building,
    pos_x: d.pos[0], pos_y: d.pos[1],
    starts_at: new Date(d.start).toISOString(), ends_at: new Date(d.end).toISOString(),
  }).select("id").single();
  if (error) { EV.busy = false; EV.msg = `Couldn't post: ${error.message}`; return evRender(); }

  const uploaded = [];
  try {
    if (EV.flyer?.full) {
      const base = `${uid}/${row.id}/`, f = EV.flyer, store = sb.storage.from("flyers");
      const put = async (name, blob, type) => {
        const { error: upErr } = await store.upload(base + name, blob, { contentType: type, upsert: false });
        if (upErr) throw upErr;
        uploaded.push(base + name);
        return base + name;
      };
      const image = await put(`flyer.${evExt(f.full.blob)}`, f.full.blob, f.full.blob.type);
      const thumb = await put(`thumb.${evExt(f.thumb.blob)}`, f.thumb.blob, f.thumb.blob.type);
      const pdf = f.pdf && d.keepPdf && f.pdf.size <= EV_LIMITS.pdfKeepBytes ? await put("original.pdf", f.pdf, "application/pdf") : null;
      const { error: fe } = await sb.from("flyers").insert({
        event_id: row.id, image_path: image, thumb_path: thumb, pdf_path: pdf,
        width: f.full.w, height: f.full.h, bytes: f.full.blob.size + f.thumb.blob.size,
      });
      if (fe) throw fe;
    }
  } catch (err) {
    // undo the half-made post so no empty event is left behind
    if (uploaded.length) await sb.storage.from("flyers").remove(uploaded);
    await sb.from("events").delete().eq("id", row.id);
    EV.busy = false; EV.msg = `Upload failed, nothing was posted: ${err.message || err}`;
    return evRender();
  }
  EV.busy = false; EV.draft = null; EV.flyer = null;
  EV.view = "mine"; EV.msg = "Submitted. A moderator will review it before it appears on the map.";
  await evLoad();
}

// ---------------------------------------------------------------------------------------------
// Moderation, reports, deleting
// ---------------------------------------------------------------------------------------------
async function evModerate(id, status, reason) {
  EV.busy = true; evRender();
  const { error } = await sb.rpc("moderate_event", { event_id: id, new_status: status, reason: reason || null });
  EV.busy = false;
  EV.msg = error ? `Couldn't update: ${error.message}` : `Event ${status}.`;
  await evLoad();
}

async function evReport(id, reason) {
  const { error } = await sb.from("reports").insert({ event_id: id, reason });
  EV.reporting = false;
  EV.msg = error ? (error.code === "23505" ? "You've already reported this event." : `Couldn't report: ${error.message}`)
                 : "Thanks. A moderator will take a look.";
  await evLoad();
}

async function evDelete(id) {
  const e = EV.events.find(x => x.id === id);
  EV.busy = true; evRender();
  if (e?.flyer) await sb.storage.from("flyers").remove([e.flyer.image_path, e.flyer.thumb_path, e.flyer.pdf_path].filter(Boolean));
  const { error } = await sb.from("events").delete().eq("id", id);
  EV.busy = false;
  EV.msg = error ? `Couldn't delete: ${error.message}` : "Event deleted.";
  if (!error) { EV.view = evMine(e) ? "mine" : "list"; EV.eventId = null; }
  await evLoad();
}

// ---------------------------------------------------------------------------------------------
// Panel rendering
// ---------------------------------------------------------------------------------------------
function evOpen(which, id = null) {     // "which", not "view": the map's camera is the global "view"
  if (diningOpen) setDiningOpen(false);
  for (const f of ext.panelOpened) f("events");
  Object.assign(EV, { open: true, view: which, eventId: id, confirm: null, reporting: false });
  if (which !== "list") EV.group = null;
  evPanel.hidden = false;
  evButton.setAttribute("aria-pressed", "true");
  layoutPanels(); updatePanelState();
  if (which === "event") {
    const e = EV.events.find(x => x.id === id);
    if (e) {
      flyTo(e.pos_x, e.pos_y, Math.max(view.scale, 2.6));
      evSign([e.flyer?.image_path]).then(evRender);
    }
  }
  evRender();
  requestDraw();
}
function evClose() {
  if (!EV.open && !EV.pick) return;
  EV.open = false; EV.msg = "";
  if (EV.pick) evSetPick(false);
  evPanel.hidden = true;
  evButton.setAttribute("aria-pressed", "false");
  updatePanelState(); requestDraw();
}

function evRow(e) {
  const thumb = e.flyer ? `<img class="ev-thumb" src="${esc(evUrl(e.flyer.thumb_path))}" alt="">` : `<span class="ev-thumb"></span>`;
  const badge = e.status !== "approved" ? ` <span class="ev-badge ${esc(e.status)}">${esc(e.status)}</span>` : "";
  return `<button class="ev-row" data-act="event" data-id="${esc(e.id)}">${thumb}
    <span>${esc(e.title)}${badge}<small>${esc(evWhen(e))} · ${esc(e.building)}</small></span></button>`;
}

function evRender() {
  if (!EV.open) return;
  const s = EV.session, now = evNow();
  const tab = (v, label) => `<button class="chip${EV.view === v ? " on" : ""}" data-act="view" data-v="${v}">${label}</button>`;
  const pending = EV.events.filter(e => e.status === "pending").length;
  let html = `<div class="dn-head"><h2>Events <small style="color:var(--muted);font-size:12px">beta</small></h2>
    <button data-act="close" aria-label="Close">✕</button></div>
    <div class="ev-account">${s ? `Signed in as ${esc(s.user.email)} <button data-act="signout">Sign out</button>`
                                 : `<button data-act="view" data-v="signin">Sign in to post</button>`}</div>
    <div class="ev-tabs">${tab("list", "Upcoming")}${s ? tab("mine", "My events") : ""}${EV.isMod ? tab("review", `Review (${pending})`) : ""}</div>
    ${s && EV.view !== "post" ? `<button class="ev-post" data-act="view" data-v="post">+ Post an event</button>` : ""}
    ${EV.msg ? `<p class="ev-msg" role="status">${esc(EV.msg)}</p>` : ""}`;

  if (EV.view === "list") {
    let list = EV.events.filter(e => evOnMap(e, now));
    let heading = "";
    if (EV.group) {
      list = list.filter(e => EV.group.includes(e.id));
      heading = `<p class="ev-meta">At ${esc(list[0]?.building || "this spot")} · <button class="chip" data-act="all">Show all</button></p>`;
    }
    const sections = [["Happening now", "now"], ["Today", "today"], ["This week", "later"]].map(([title, st]) => {
      const rows = list.filter(e => evState(e, now) === st);
      return rows.length ? `<h4>${title}</h4>${rows.map(evRow).join("")}` : "";
    }).join("");
    html += heading + (sections || `<p class="ev-empty">No events in the next 7 days yet.${s ? "" : " Sign in to post one."}</p>`);
  }

  else if (EV.view === "mine") {
    const mine = EV.events.filter(evMine).sort((a, b) => b.created_at.localeCompare(a.created_at));
    html += mine.length ? mine.map(evRow).join("") : `<p class="ev-empty">You haven't posted anything yet.</p>`;
  }

  else if (EV.view === "review") {
    const queue = EV.events.filter(e => e.status === "pending").sort((a, b) => a.created_at.localeCompare(b.created_at));
    html += queue.length ? `<h4>Waiting for review</h4>${queue.map(evRow).join("")}` : `<p class="ev-empty">Nothing to review.</p>`;
  }

  else if (EV.view === "signin") {
    html += `<label for="ev-email">Your Brown email</label>
      <div class="ev-inline"><input id="ev-email" type="email" autocomplete="email" placeholder="you@brown.edu">
      <button data-act="send-link"${EV.busy ? " disabled" : ""}>Send link</button></div>
      <p class="ev-meta">We'll email you a sign-in link. Only @brown.edu addresses can sign in.</p>`;
  }

  else if (EV.view === "post") {
    if (!s) { EV.view = "signin"; return evRender(); }
    const d = EV.draft || (EV.draft = evNewDraft()), f = EV.flyer;
    html += `<h3>Post an event</h3><p class="ev-meta">A moderator reviews every event before it appears on the map.</p>
      <label for="ev-title">Title</label><input id="ev-title" data-field="title" maxlength="120" value="${esc(d.title)}">
      <label>Where</label><div class="ev-inline"><span>${d.building ? esc(d.building) : '<span class="ev-empty">Not chosen yet</span>'}</span>
        <button data-act="pick">${d.pos ? "Change" : "Choose on map"}</button></div>
      <label for="ev-start">Starts</label><input id="ev-start" type="datetime-local" data-field="start" value="${esc(d.start)}">
      <label for="ev-end">Ends</label><input id="ev-end" type="datetime-local" data-field="end" value="${esc(d.end)}">
      <label for="ev-desc">Description</label><textarea id="ev-desc" data-field="description" maxlength="2000">${esc(d.description)}</textarea>
      <label for="ev-file">Flyer (optional): JPEG, PNG or PDF, up to 10 MB</label>
      <input id="ev-file" type="file" accept="image/jpeg,image/png,image/webp,application/pdf">
      ${f?.working ? `<p class="ev-meta">Shrinking ${esc(f.name)}…</p>` : ""}
      ${f?.full ? `<div class="ev-preview"><img src="${esc(f.preview)}" alt=""><span>${esc(f.name)}<br>
        ${evKB(f.origBytes)} → ${evKB(f.full.blob.size)} (${f.full.w}×${f.full.h})</span></div>` : ""}
      ${f?.pdf ? (f.pdf.size <= EV_LIMITS.pdfKeepBytes
        ? `<label><input type="checkbox" data-field="keepPdf"${d.keepPdf ? " checked" : ""}>Also offer the original PDF for download</label>`
        : `<p class="ev-meta">The original PDF is over 2 MB, so only the image is posted.</p>`) : ""}
      <div class="ev-actions"><button class="ev-post" data-act="submit"${EV.busy ? " disabled" : ""}>Submit for review</button>
        <button data-act="view" data-v="list">Cancel</button></div>`;
  }

  else if (EV.view === "event") {
    const e = EV.events.find(x => x.id === EV.eventId);
    if (!e) { EV.view = "list"; return evRender(); }
    const mine = evMine(e), status = e.status !== "approved"
      ? ` <span class="ev-badge ${esc(e.status)}">${e.status === "pending" ? "waiting for review" : esc(e.status)}</span>` : "";
    const full = e.flyer && (evUrl(e.flyer.image_path) || evUrl(e.flyer.thumb_path));
    html += `<button class="chip" data-act="back">‹ Back</button>
      <h3>${esc(e.title)}${status}</h3>
      <p class="ev-meta">${esc(evWhen(e))}</p>
      <p class="ev-meta">${esc(e.building)} · <button class="chip" data-act="show" data-id="${esc(e.id)}">Show on map</button></p>
      <p class="ev-meta">Posted by ${esc(e.poster)}</p>
      ${e.reject_reason && (mine || EV.isMod) ? `<p class="ev-msg">Moderator note: ${esc(e.reject_reason)}</p>` : ""}
      ${full ? `<img class="ev-flyer" src="${esc(full)}" alt="Flyer for ${esc(e.title)}">` : ""}
      ${e.description ? `<p class="ev-desc">${esc(e.description)}</p>` : ""}
      <div class="ev-actions">`;
    if (e.flyer?.pdf_path) html += `<button data-act="pdf" data-id="${esc(e.id)}">Download PDF</button>`;
    if (s && !mine && e.status === "approved") html += `<button data-act="report-open">Report</button>`;
    if (EV.isMod && e.status === "pending") html += `<button class="ev-post" style="width:auto;margin:0" data-act="approve" data-id="${esc(e.id)}">Approve</button>`;
    if (EV.isMod && e.status !== "rejected" && e.status !== "removed")
      html += `<button data-act="reject-open">${e.status === "approved" ? "Remove" : "Reject"}</button>`;
    if (mine || EV.isMod)
      html += `<button class="ev-danger" data-act="delete" data-id="${esc(e.id)}">${EV.confirm === e.id ? "Tap again to delete" : "Delete"}</button>`;
    html += `</div>`;
    if (EV.reporting === "report") {
      html += `<label for="ev-reason">What's wrong?</label><div class="ev-inline"><select id="ev-reason">
        ${EV_REPORT_REASONS.map(r => `<option>${esc(r)}</option>`).join("")}</select>
        <button data-act="report" data-id="${esc(e.id)}">Send report</button></div>`;
    }
    if (EV.reporting === "reject") {
      const verb = e.status === "approved" ? "remove" : "reject";
      html += `<label for="ev-reject">Reason (the poster sees this)</label><div class="ev-inline">
        <input id="ev-reject" maxlength="500" placeholder="e.g. not a campus event">
        <button class="ev-danger" data-act="${verb}" data-id="${esc(e.id)}">${verb[0].toUpperCase() + verb.slice(1)}</button></div>`;
    }
  }
  evBody.innerHTML = html;
}

evBody.addEventListener("input", e => {
  const field = e.target.dataset.field;
  if (field && EV.draft) EV.draft[field] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
});
evBody.addEventListener("change", async e => {
  if (e.target.id !== "ev-file" || !e.target.files[0]) return;
  const file = e.target.files[0];
  EV.flyer = { working: true, name: file.name }; EV.msg = ""; evRender();
  try {
    const f = await evShrink(file);
    if (EV.flyer?.preview) URL.revokeObjectURL(EV.flyer.preview);
    EV.flyer = { ...f, preview: URL.createObjectURL(f.thumb.blob) };
  } catch (err) {
    EV.flyer = null; EV.msg = err.message || "Couldn't read that file.";
  }
  evRender();
});
evBody.addEventListener("click", async e => {
  const b = e.target.closest("button[data-act]");
  if (!b || b.disabled) return;
  const act = b.dataset.act, id = b.dataset.id;
  if (act !== "delete") EV.confirm = null;
  if (act !== "submit" && act !== "send-link") EV.msg = "";
  if (act === "close") return evClose();
  if (act === "view") { EV.view = b.dataset.v; EV.reporting = false; if (EV.view !== "list") EV.group = null; }
  else if (act === "all") EV.group = null;
  else if (act === "event") return evOpen("event", id);
  else if (act === "back") EV.view = EV.group ? "list" : (evMine(EV.events.find(x => x.id === EV.eventId)) ? "mine" : "list");
  else if (act === "show") { const ev = EV.events.find(x => x.id === id); if (ev) flyTo(ev.pos_x, ev.pos_y, Math.max(view.scale, 3)); }
  else if (act === "signout") { await sb.auth.signOut(); EV.view = "list"; }
  else if (act === "send-link") return evSendLink(document.getElementById("ev-email").value);
  else if (act === "pick") return evSetPick(true);
  else if (act === "submit") return evSubmit();
  else if (act === "report-open") EV.reporting = EV.reporting === "report" ? false : "report";
  else if (act === "reject-open") EV.reporting = EV.reporting === "reject" ? false : "reject";
  else if (act === "report") return evReport(id, document.getElementById("ev-reason").value);
  else if (act === "approve") return evModerate(id, "approved");
  else if (act === "reject" || act === "remove")
    return evModerate(id, act === "reject" ? "rejected" : "removed", document.getElementById("ev-reject").value.trim());
  else if (act === "delete") { if (EV.confirm === id) return evDelete(id); EV.confirm = id; }
  else if (act === "pdf") {
    const ev = EV.events.find(x => x.id === id);
    await evSign([ev.flyer.pdf_path]);
    const url = evUrl(ev.flyer.pdf_path);
    if (url) window.open(url, "_blank", "noopener");
  }
  evRender();
});
evHint.addEventListener("click", e => { if (e.target.closest("[data-act=cancel-pick]")) evSetPick(false); });
addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  if (EV.pick) evSetPick(false); else if (EV.open) evClose();
});

// ---------------------------------------------------------------------------------------------
// Pins on the map
// ---------------------------------------------------------------------------------------------
// Events within 15 m of each other share one pin (same building, or neighbours on a lawn)
function evGroups() {
  const now = evNow(), groups = [], rank = { now: 0, today: 1, later: 2 };
  for (const e of EV.events) {
    if (!evOnMap(e, now) || edgeDist(e.pos_x, e.pos_y) < FOG_DEPTH * 0.5) continue;
    const g = groups.find(g => Math.hypot(g.x - e.pos_x, g.y - e.pos_y) < 15);
    if (g) g.events.push(e);
    else groups.push({ x: e.pos_x, y: e.pos_y, events: [e] });
  }
  for (const g of groups) {
    g.events.sort((a, b) => a.starts_at.localeCompare(b.starts_at));
    g.ids = g.events.map(e => e.id);
    g.state = g.events.map(e => evState(e, now)).sort((a, b) => rank[a] - rank[b])[0];
  }
  return groups;
}

function evLayout(g) {
  const p = view.scale < 1.2 ? 2 : 3;
  const sx = g.x * view.scale + view.x, sy = g.y * view.scale + view.y;
  const x = Math.round(sx - 7.5 * p), y = Math.round(sy - 13 * p);
  const box = [x, y, x + 15 * p, y + 13 * p];
  let chip = null;
  if (view.scale >= 1.5) {
    pctx.font = `700 12px "Pixelify Sans", monospace`;
    const e = g.events[0], n = g.events.length;
    const when = g.state === "now" ? "now" : g.state === "today" ? fmtTime(new Date(e.starts_at))
               : new Date(e.starts_at).toLocaleDateString("en-US", { timeZone: TZ, weekday: "short" });
    const title = e.title.length > 22 ? e.title.slice(0, 21) + "…" : e.title;
    const label = n > 1 ? `${n} events · ${when}` : `${title} · ${when}`;
    const w = pctx.measureText(label).width + 12;
    chip = { label, x: box[2] + 4, y: y + 5.5 * p - 9, w, h: 18 };
    box[2] = chip.x + w;
  }
  return { ...g, p, x, y, box, chip };
}

function evVisiblePins() {
  return evGroups().map(evLayout)
    .filter(g => g.box[2] > 0 && g.box[3] > 0 && g.box[0] < innerWidth && g.box[1] < innerHeight);
}
function eventPinAt(sx, sy, pad = 0) {
  let best = null, bestD = Infinity;
  for (const g of evVisiblePins()) {
    const d = Math.hypot(Math.max(g.box[0] - sx, 0, sx - g.box[2]), Math.max(g.box[1] - sy, 0, sy - g.box[3]));
    if (d <= pad && d <= bestD) { best = g; bestD = d; }
  }
  return best;
}

function drawEventPins(ms) {
  // the spot being chosen for a new event
  if (EV.draft?.pos && (EV.view === "post" || EV.pick) && (EV.open || EV.pick)) {
    const sx = EV.draft.pos[0] * view.scale + view.x, sy = EV.draft.pos[1] * view.scale + view.y;
    pctx.fillStyle = "#e07a55";
    pctx.fillRect(Math.round(sx) - 9, Math.round(sy) - 1, 18, 3);
    pctx.fillRect(Math.round(sx) - 1, Math.round(sy) - 9, 3, 18);
  }
  const bob = REDUCED_MOTION ? 0 : Math.floor(ms / 600) % 2;
  for (const g of evVisiblePins()) {
    const col = EV_COLORS[g.state], p = g.p, lift = g.state === "now" ? bob * p : 0;
    const rect = (x, y, w, h, c) => { pctx.fillStyle = c; pctx.fillRect(g.x + x * p, g.y + y * p - lift, w * p, h * p); };
    rect(2, 1, 13, 11, "rgba(10,8,20,0.28)"); rect(1, 2, 15, 9, "rgba(10,8,20,0.28)");      // shadow, down-right
    rect(1, 0, 13, 11, col); rect(0, 1, 15, 9, col); rect(1, 1, 13, 9, "#1c1a18");
    rect(6, 11, 3, 1, col); rect(7, 12, 1, 1, col);
    POSTER.forEach((row, r) => [...row].forEach((ch, c) => {
      if (ch !== ".") rect(2 + c, 1 + r, 1, 1, ch === "W" ? "#f4efe6" : ch === "k" ? "#8d8680" : col);
    }));
    if (g.events.length > 1) {                                  // count badge on the top-right corner
      pctx.fillStyle = "#f4efe6"; pctx.fillRect(g.x + 11 * p, g.y - 3 * p - lift, 6 * p, 6 * p);
      pctx.fillStyle = "#1c1a18"; pctx.font = `700 ${4 * p + 2}px "Pixelify Sans", monospace`;
      pctx.textAlign = "center"; pctx.textBaseline = "middle";
      pctx.fillText(String(Math.min(g.events.length, 9)), g.x + 14 * p, g.y - lift + 0.5);
    }
    if (g.chip) {
      const { x, y, w, h, label } = g.chip;
      pctx.fillStyle = "rgba(28,26,24,0.85)"; pctx.fillRect(x, y, w, h);
      pctx.fillStyle = col; pctx.fillRect(x, y, 3, h);
      pctx.fillStyle = "#f4efe6"; pctx.textBaseline = "middle"; pctx.textAlign = "left";
      pctx.font = `700 12px "Pixelify Sans", monospace`;
      pctx.fillText(label, x + 8, y + h / 2 + 1);
    }
  }
}

ext.drawPins.push(drawEventPins);
ext.pinBoxes.push(() => evVisiblePins().map(g => g.box));
ext.panelOpened.push(name => { if (name !== "events") evClose(); });
ext.hover.push((sx, sy) => {
  const g = eventPinAt(sx, sy);
  if (!g) return null;
  const e = g.events[0];
  return g.events.length > 1 ? `${esc(e.building)}<br><small>${g.events.length} events</small>`
                             : `${esc(e.title)}<br><small>${esc(evWhen(e))} · ${esc(e.building)}</small>`;
});
ext.click.push((sx, sy, touch) => {
  if (EV.pick) { evPickAt(sx, sy); return true; }
  const g = eventPinAt(sx, sy, touch ? 16 : 0);
  if (!g) return false;
  if (g.events.length === 1) evOpen("event", g.events[0].id);
  else { evOpen("list"); EV.group = g.ids; evRender(); }
  return true;
});

// keep the list fresh while the page is open
setInterval(() => { if (!document.hidden) evLoad(); }, 60000);
