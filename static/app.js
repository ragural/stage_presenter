/* Stage Presenter — front-end logic */
const $ = (id) => document.getElementById(id);
const audio = $("audio");

const S = {
  cfg: null,
  voices: { edge: [], kokoro: [], piper: [] },
  refDur: {},          // slide index -> AI narration seconds
  recording: false,
  deck: null,
  cur: 0,
  gender: "Female",
  state: "idle",       // idle | playing | paused
  token: 0,            // increments to cancel an in-flight presentation
  sentences: [],
  sentIdx: -1,
  shadowWaiting: false,
  ttsCache: new Map(), // key -> Promise<{audio, sentences}>
  audioLive: false,    // true while the <audio> element holds the current slide's narration
  warnedFallback: false,
  editing: false,
};

/* ------------------------------------------------------------ utils */
function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), err ? 6000 : 2500);
}
function busy(on, text = "Working…") {
  $("busy").classList.toggle("hidden", !on);
  $("busyText").textContent = text;
}
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(url, opts = {}) {
  const isJson = opts.body && !(opts.body instanceof FormData);
  const r = await fetch(url, {
    headers: isJson ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: isJson && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body,
  });
  if (!r.ok) {
    let d = r.statusText;
    try { d = (await r.json()).detail || d; } catch {}
    throw new Error(d);
  }
  return r.json();
}
function splitSentences(text) {
  return (text.match(/[^.!?…]+(?:[.!?…]+["')\]]*|$)/g) || [text]).map((s) => s.trim()).filter(Boolean);
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("Copied to clipboard");
}

/* ------------------------------------------------------------ prefs */
const PREF_IDS = ["engine", "tone", "style", "length", "audience", "instructions", "locale", "voice", "rate", "pitch",
  "autoAdvance", "shadowMode", "hideScript", "gap", "captions"];
function savePrefs() {
  const p = { gender: S.gender };
  PREF_IDS.forEach((id) => { const el = $(id); p[id] = el.type === "checkbox" ? el.checked : el.value; });
  localStorage.setItem("stagePrefs", JSON.stringify(p));
}
function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem("stagePrefs") || "{}");
    if (p.gender) S.gender = p.gender;
    PREF_IDS.forEach((id) => {
      if (p[id] === undefined || id === "voice") return;
      const el = $(id);
      el.type === "checkbox" ? (el.checked = p[id]) : (el.value = p[id]);
    });
    return p;
  } catch { return {}; }
}
function prefs() {
  return {
    tone: $("tone").value, style: $("style").value, length: $("length").value,
    locale: $("locale").value, audience: $("audience").value, instructions: $("instructions").value,
  };
}
const voiceOpts = () => ({ engine: $("engine").value, voice: $("voice").value, locale: $("locale").value,
  rate: +$("rate").value, pitch: $("engine").value === "edge" ? +$("pitch").value : 0 });

/* ------------------------------------------------------------ voices */
function fillVoices(preferred) {
  const loc = $("locale").value, eng = $("engine").value;
  const sel = $("voice");
  const note = $("voiceNote");
  sel.innerHTML = "";
  note.classList.add("hidden");
  $("pitchRow").classList.toggle("hidden", eng !== "edge" && eng !== "browser");
  if (eng === "browser") {
    const bv = ("speechSynthesis" in window ? speechSynthesis.getVoices() : [])
      .filter((v) => v.lang.toLowerCase().replace("_", "-") === loc.toLowerCase());
    sel.add(new Option("Auto (best match)", ""));
    bv.forEach((v) => sel.add(new Option(v.name.replace(/Microsoft |Google /, ""), "b:" + v.name)));
    if (!bv.length) {
      note.textContent = `No installed Windows voice for ${loc}. Add one in Settings → Time & language → Speech.`;
      note.classList.remove("hidden");
    }
    if (preferred && [...sel.options].some((o) => o.value === preferred)) sel.value = preferred;
    return;
  }
  const all = S.voices[eng] || [];
  let list = all.filter((v) => v.locale === loc && v.gender === S.gender);
  if (!list.length) list = all.filter((v) => v.locale === loc);
  if (!list.length && eng === "edge" && S.cfg?.curated[loc]) {
    const name = S.cfg.curated[loc][S.gender === "Female" ? 0 : 1];
    list = [{ name, label: name.split("-").pop().replace("Neural", ""), gender: S.gender }];
  }
  list.forEach((v) => sel.add(new Option(v.label, v.name)));
  const say = (t) => { note.textContent = t; note.classList.remove("hidden"); };
  if (!list.length) {
    sel.add(new Option("(none installed)", ""));
    say(eng === "edge" ? "Edge voices need internet access." :
      `No ${eng} voice for ${loc}. Run: python download_models.py --only ${eng}`);
  } else if (list.every((v) => v.gender !== S.gender)) {
    say(`No ${S.gender.toLowerCase()} ${eng} voice for ${loc} — showing available ones.`);
  } else if (eng !== "edge" && loc === "en-IN") {
    say("Offline Indian English is approximate. Use Edge neural for authentic en-IN voices.");
  } else if (eng !== "edge" && !["en-US", "en-GB"].includes(loc)) {
    say(`Offline ${eng} voices are US/UK; ${loc} uses the closest accent.`);
  }
  const curated = eng === "edge" ? S.cfg?.curated[loc]?.[S.gender === "Female" ? 0 : 1] : null;
  const starred = list.find((v) => v.label.includes("★") && v.gender === S.gender);
  if (preferred && list.some((v) => v.name === preferred)) sel.value = preferred;
  else if (curated && list.some((v) => v.name === curated)) sel.value = curated;
  else if (starred) sel.value = starred.name;
}
function updateLabels() {
  const r = +$("rate").value, p = +$("pitch").value;
  $("rateVal").textContent = (r >= 0 ? "+" : "") + r + "%";
  $("pitchVal").textContent = (p >= 0 ? "+" : "") + p + "Hz";
  $("gapVal").textContent = $("gap").value + "s";
  $("teleprompter").classList.toggle("blurred", $("hideScript").checked);
}

/* ------------------------------------------------------------ deck */
async function refreshRecent() {
  const decks = await api("/api/decks").catch(() => []);
  const sel = $("recentDecks");
  sel.innerHTML = '<option value="">Recent decks…</option>';
  decks.forEach((d) => sel.add(new Option(`${d.name} (${d.slides})`, d.id)));
}
async function uploadFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pptx")) return toast("Please choose a .pptx file", true);
  const fd = new FormData();
  fd.append("file", file);
  busy(true, "Reading slides and speaker notes… (rendering slide images with PowerPoint)");
  try {
    const deck = await api("/api/upload", { method: "POST", body: fd });
    openDeck(deck);
    const noNotes = deck.slides.filter((s) => !s.notes).length;
    toast(`Loaded ${deck.slides.length} slides` + (noNotes ? ` — ${noNotes} without notes` : "") +
      (deck.rendered ? "" : " — slide images unavailable, showing text view"));
    refreshRecent();
  } catch (e) { toast(e.message, true); }
  finally { busy(false); $("fileInput").value = ""; }
}
function openDeck(deck) {
  stop();
  S.deck = deck;
  S.cur = 0;
  S.ttsCache.clear();
  $("empty").classList.add("hidden");
  $("stageArea").classList.remove("hidden");
  $("genAll").disabled = false;
  $("recBtn").disabled = !(S.cfg.stt.provider !== "none" && S.cfg.stt.available);
  $("exportMd").href = `/api/decks/${deck.id}/export.md`;
  $("exportTxt").href = `/api/decks/${deck.id}/export.txt`;
  document.title = `${deck.name} — Stage Presenter`;
  renderThumbs();
  showSlide(0);
}
function renderThumbs() {
  const box = $("thumbs");
  box.innerHTML = "";
  S.deck.slides.forEach((s, i) => {
    const d = document.createElement("div");
    d.className = "thumb";
    if (s.image) d.style.backgroundImage = `url(${s.image})`;
    else d.textContent = s.title;
    d.innerHTML += `<span class="n">${i + 1}</span>${s.script ? '<span class="dot" title="Script ready"></span>' : ""}`;
    d.onclick = () => { const was = S.state === "playing"; stop(); showSlide(i); if (was) present(i); };
    box.appendChild(d);
  });
}
function showSlide(i) {
  const s = S.deck.slides[i];
  S.cur = i;
  const img = $("slideImg"), txt = $("slideText");
  if (s.image) {
    txt.classList.add("hidden");
    img.classList.remove("hidden");
    img.classList.add("fade");
    const pre = new Image();
    pre.onload = () => { if (S.cur !== i) return; img.src = s.image; img.classList.remove("fade"); };
    pre.src = s.image;
  } else {
    img.classList.add("hidden");
    txt.classList.remove("hidden");
    txt.innerHTML = `<h1>${esc(s.title)}</h1><ul>${s.bullets.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>`;
  }
  $("slideCounter").textContent = `${i + 1} / ${S.deck.slides.length}`;
  $("scriptTitle").textContent = `${i + 1}. ${s.title}`;
  $("notesView").textContent = s.notes || "(no speaker notes on this slide)";
  document.querySelectorAll(".thumb").forEach((t, k) => t.classList.toggle("on", k === i));
  document.querySelectorAll(".thumb")[i]?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  setEditing(false);
  renderRehearsalHistory();
  if (!S.recording) $("feedback").innerHTML = "";
  renderTeleprompter(s.script ? splitSentences(s.script) : null);
  $("caption").classList.add("hidden");
}
function renderTeleprompter(sentences) {
  const tp = $("teleprompter");
  if (!sentences) {
    tp.innerHTML = `<p class="muted">No script yet. Press ▶ to generate and present, or “✨ Generate full script”.</p>`;
    return;
  }
  tp.innerHTML = sentences.map((t, k) => `<span class="s" data-k="${k}">${esc(t)}</span> `).join("");
}
function highlight(k) {
  if (k === S.sentIdx) return;
  S.sentIdx = k;
  const spans = $("teleprompter").querySelectorAll(".s");
  spans.forEach((sp, j) => { sp.classList.toggle("now", j === k); sp.classList.toggle("done", j < k); });
  spans[k]?.scrollIntoView({ block: "center", behavior: "smooth" });
  const cap = $("caption");
  if ($("captions").checked && S.sentences[k]) {
    cap.textContent = S.sentences[k].text;
    cap.classList.remove("hidden");
  } else cap.classList.add("hidden");
}

/* ------------------------------------------------------------ script generation */
async function ensureScript(i, force = false) {
  const s = S.deck.slides[i];
  if (s.script && !force) return s.script;
  if (s._pending && !force) return s._pending;
  s._pending = api(`/api/decks/${S.deck.id}/script`, { method: "POST", body: { slide: i + 1, ...prefs() } })
    .then((r) => {
      s.script = r.script;
      if (r.engine === "local" && !S._warnedLocal) {
        S._warnedLocal = true;
        toast("No LLM configured — using lightly polished notes. Set LLM_* in .env for eloquent rewriting.", true);
      }
      renderThumbs(); document.querySelectorAll(".thumb")[S.cur]?.classList.add("on");
      return r.script;
    })
    .finally(() => { s._pending = null; });
  return s._pending;
}
async function generateAll() {
  const btn = $("genAll"), bar = $("genProgress");
  btn.disabled = true;
  bar.classList.remove("hidden");
  const n = S.deck.slides.length;
  try {
    for (let i = 0; i < n; i++) {
      btn.textContent = `Writing slide ${i + 1} of ${n}…`;
      await ensureScript(i, true);
      bar.firstElementChild.style.width = `${((i + 1) / n) * 100}%`;
      if (i === S.cur && S.state === "idle") showSlide(i);
    }
    toast("Full script ready — copy or export it from the script panel");
  } catch (e) { toast(e.message, true); }
  finally {
    btn.disabled = false;
    btn.textContent = "✨ Generate full script";
    setTimeout(() => bar.classList.add("hidden"), 1200);
  }
}

/* ------------------------------------------------------------ speech */
function ttsFor(text) {
  const v = voiceOpts();
  const key = JSON.stringify([text, v]);
  if (!S.ttsCache.has(key)) {
    const p = v.engine !== "browser" && v.voice
      ? api("/api/tts", { method: "POST", body: { text, ...v } })
      : Promise.reject(new Error("browser voice selected"));
    p.catch(() => S.ttsCache.delete(key));
    S.ttsCache.set(key, p);
  }
  return S.ttsCache.get(key);
}
function prefetch(i) {
  if (!S.deck || i >= S.deck.slides.length) return;
  ensureScript(i).then((t) => ttsFor(t)).catch(() => {});
}

// Resolves once the presentation is not paused (or has been cancelled). Pause can be pressed while the
// script / audio of a slide is still being prepared; playback must then wait instead of starting anyway.
async function untilResumed(token) {
  while (S.state === "paused" && token === S.token) await sleep(150);
}

async function present(i) {
  const token = ++S.token;
  setState("playing");
  showSlide(i);
  try {
    $("caption").textContent = "…";
    let text = S.deck.slides[i].script;
    if (!text) {
      $("teleprompter").innerHTML = `<p class="muted">Composing narration…</p>`;
      text = await ensureScript(i);
    }
    if (token !== S.token) return;
    let speech = null;
    if ($("engine").value !== "browser") {
      try { speech = await ttsFor(text); }
      catch (e) {
        if (!S.warnedFallback) { S.warnedFallback = true; toast(e.message + " — using browser voice", true); }
      }
    }
    if (token !== S.token) return;
    prefetch(i + 1);
    if (S.state === "paused") {
      renderTeleprompter(splitSentences(text));
      $("caption").textContent = "⏸ Paused: press ▶ to start this slide";
      await untilResumed(token);
      if (token !== S.token) return;
    }
    if (speech) await playNeural(speech, text, token);
    else await playBrowser(text, token);
    if (token !== S.token) return;
    if ($("autoAdvance").checked && i < S.deck.slides.length - 1) {
      await sleep(+$("gap").value * 1000);
      await untilResumed(token);
      if (token !== S.token) return;
      return present(i + 1);
    }
    setState("idle");
    if (i === S.deck.slides.length - 1) toast("👏 End of presentation");
  } catch (e) {
    if (token === S.token) { toast(e.message, true); setState("idle"); }
  }
}

function playNeural(speech, text, token) {
  if (speech.sentences.length) S.refDur[S.cur] = speech.sentences.at(-1).end;
  S.sentences = speech.sentences.length ? speech.sentences : splitSentences(text).map((t) => ({ text: t, start: 0, end: 0 }));
  renderTeleprompter(S.sentences.map((s) => s.text));
  S.sentIdx = -1;
  let shadowDone = -1;
  return new Promise((resolve) => {
    const finish = () => { cleanup(); resolve(); };
    const onTime = () => {
      if (token !== S.token) return finish();
      const t = audio.currentTime;
      let k = S.sentences.findIndex((s) => t >= s.start && t < s.end + 0.15);
      if (k < 0) k = S.sentences.findLastIndex((s) => t >= s.start);
      if (k >= 0) highlight(k);
      // Shadow mode: pause at the end of each sentence so the trainee can repeat it.
      if ($("shadowMode").checked && k >= 0 && k > shadowDone && S.sentences[k].end && t >= S.sentences[k].end - 0.05) {
        shadowDone = k;
        const dur = Math.max(1.2, (S.sentences[k].end - S.sentences[k].start) * 1.15 + 0.6);
        audio.pause();
        S.shadowWaiting = true;
        $("caption").textContent = "🎙 Your turn — repeat: " + S.sentences[k].text;
        setTimeout(() => {
          if (token === S.token && S.shadowWaiting && S.state === "playing") { S.shadowWaiting = false; audio.play(); }
        }, dur * 1000);
      }
    };
    const onEnd = () => finish();
    const onErr = () => { toast("Audio playback failed", true); finish(); };
    const cleanup = () => {
      audio.removeEventListener("timeupdate", onTime);
      audio.removeEventListener("ended", onEnd);
      audio.removeEventListener("error", onErr);
      S._abort = null;
      S.audioLive = false;
    };
    S._abort = finish;
    S.audioLive = true;
    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("ended", onEnd);
    audio.addEventListener("error", onErr);
    audio.src = speech.audio;
    audio.play().catch((e) => { toast("Click ▶ to allow audio playback (" + e.message + ")", true); finish(); });
  });
}

function pickBrowserVoice() {
  const chosen = $("voice").value;
  if ($("engine").value === "browser" && chosen.startsWith("b:")) {
    const v = speechSynthesis.getVoices().find((x) => x.name === chosen.slice(2));
    if (v) return v;
  }
  const loc = $("locale").value.toLowerCase();
  const all = speechSynthesis.getVoices();
  const byLoc = all.filter((v) => v.lang.toLowerCase().replace("_", "-") === loc);
  const pool = byLoc.length ? byLoc : all.filter((v) => v.lang.toLowerCase().startsWith("en"));
  const fem = /female|zira|aria|jenny|heera|neerja|susan|hazel|sonia|libby|natasha|samantha|karen|moira|veena|catherine|emma|ava/i;
  const male = /male|david|mark|ravi|prabhat|george|ryan|guy|daniel|james|william|andrew|brian|rishi/i;
  const want = S.gender === "Female" ? fem : male;
  return pool.find((v) => want.test(v.name) && !(S.gender === "Female" ? /\bmale\b/i : /female/i).test(v.name)) || pool[0];
}
async function playBrowser(text, token) {
  const parts = splitSentences(text);
  S.sentences = parts.map((t) => ({ text: t }));
  renderTeleprompter(parts);
  S.sentIdx = -1;
  const voice = pickBrowserVoice();
  const t0 = performance.now();
  for (let k = 0; k < parts.length; k++) {
    if (token !== S.token) return;
    highlight(k);
    await new Promise((resolve) => {
      const u = new SpeechSynthesisUtterance(parts[k]);
      if (voice) u.voice = voice;
      u.lang = $("locale").value;
      u.rate = 1 + +$("rate").value / 100;
      u.pitch = 1 + +$("pitch").value / 40;
      u.onend = u.onerror = resolve;
      S._abort = () => { speechSynthesis.cancel(); resolve(); };
      speechSynthesis.speak(u);
    });
    if ($("shadowMode").checked && token === S.token) {
      $("caption").textContent = "🎙 Your turn — repeat: " + parts[k];
      await sleep(Math.max(1500, parts[k].split(/\s+/).length * 420));
      while (S.state === "paused" && token === S.token) await sleep(200);
    }
  }
  if (token === S.token && !$("shadowMode").checked) S.refDur[S.cur] = (performance.now() - t0) / 1000;
}

function setState(st) {
  S.state = st;
  $("play").textContent = st === "playing" ? "⏸" : "▶";
  $("play").title = st === "playing" ? "Pause (Space)" : "Present (Space)";
}
function togglePlay() {
  if (!S.deck) return;
  if (S.state === "idle") return present(S.cur);
  if (S.state === "playing") {
    setState("paused");
    S.shadowWaiting = false;
    audio.pause();
    if (speechSynthesis.speaking) speechSynthesis.pause();
  } else {
    setState("playing");
    if (speechSynthesis.paused) speechSynthesis.resume();
    else if (S.audioLive) audio.play();   // otherwise present() is still preparing and starts the right clip itself
  }
}
function stop() {
  S.token++;
  S.shadowWaiting = false;
  audio.pause();
  speechSynthesis.cancel();
  S._abort?.();
  setState("idle");
  $("caption").classList.add("hidden");
}
function go(delta) {
  if (!S.deck) return;
  const i = Math.min(Math.max(S.cur + delta, 0), S.deck.slides.length - 1);
  if (i === S.cur) return;
  const was = S.state !== "idle";
  stop();
  if (was) present(i); else showSlide(i);
}

/* ------------------------------------------------------------ editing */
function setEditing(on) {
  S.editing = on;
  $("scriptEditor").classList.toggle("hidden", !on);
  $("teleprompter").classList.toggle("hidden", on);
  $("editToggle").textContent = on ? "✓ Save" : "✎ Edit";
  if (on) $("scriptEditor").value = S.deck.slides[S.cur].script || S.deck.slides[S.cur].notes || "";
}
async function toggleEdit() {
  if (!S.deck) return;
  if (!S.editing) { stop(); return setEditing(true); }
  const s = S.deck.slides[S.cur];
  s.script = $("scriptEditor").value.trim();
  await api(`/api/decks/${S.deck.id}/script`, { method: "PUT", body: { slide: S.cur + 1, script: s.script } })
    .then(() => toast("Script saved")).catch((e) => toast(e.message, true));
  setEditing(false);
  renderRehearsalHistory();
  if (!S.recording) $("feedback").innerHTML = "";
  renderTeleprompter(s.script ? splitSentences(s.script) : null);
  renderThumbs(); document.querySelectorAll(".thumb")[S.cur]?.classList.add("on");
}

/* ------------------------------------------------------------ init */
async function init() {
  S.cfg = await api("/api/config");
  const fill = (id, arr) => arr.forEach((v) => $(id).add(new Option(v[0].toUpperCase() + v.slice(1), v)));
  fill("tone", S.cfg.tones); fill("style", S.cfg.styles); fill("length", S.cfg.lengths);
  $("style").options[1].text = "TED talk";
  renderStatus();
  pollStatus();
  initSettings();
  [...$("engine").options].forEach((o) => {
    if (o.value in S.cfg.engines && !S.cfg.engines[o.value]) o.text += " — not installed";
  });

  const saved = loadPrefs();
  document.querySelectorAll("#genderSeg button").forEach((b) => b.classList.toggle("on", b.dataset.g === S.gender));
  fillVoices(saved.voice);
  updateLabels();
  api("/api/voices").then((v) => {
    S.voices = v;
    if (v.errors?.edge && $("engine").value === "edge" && S.cfg.engines.kokoro) {
      $("engine").value = "kokoro";
      toast("Edge voices unreachable — switched to offline Kokoro");
    }
    fillVoices(saved.voice);
  }).catch((e) => toast(e.message, true));
  if ("speechSynthesis" in window) {
    speechSynthesis.getVoices();
    speechSynthesis.onvoiceschanged = () => $("engine").value === "browser" && fillVoices(saved.voice);
  }
  refreshRecent();

  // events
  $("fileInput").onchange = (e) => uploadFile(e.target.files[0]);
  const dz = document.body;
  dz.addEventListener("dragover", (e) => { e.preventDefault(); $("dropZone").classList.add("over"); });
  dz.addEventListener("dragleave", () => $("dropZone").classList.remove("over"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); $("dropZone").classList.remove("over"); uploadFile(e.dataTransfer.files[0]); });
  $("recentDecks").onchange = async (e) => {
    if (!e.target.value) return;
    busy(true, "Opening deck…");
    try { openDeck(await api(`/api/decks/${e.target.value}`)); } catch (err) { toast(err.message, true); }
    finally { busy(false); e.target.value = ""; }
  };
  $("locale").onchange = () => { fillVoices(); savePrefs(); };
  $("engine").onchange = () => { fillVoices(); savePrefs(); };
  document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== b.dataset.tab));
  }));
  $("recBtn").onclick = toggleRecord;
  document.querySelectorAll("#genderSeg button").forEach((b) => (b.onclick = () => {
    S.gender = b.dataset.g;
    document.querySelectorAll("#genderSeg button").forEach((x) => x.classList.toggle("on", x === b));
    fillVoices(); savePrefs();
  }));
  PREF_IDS.forEach((id) => $(id).addEventListener("change", () => { updateLabels(); savePrefs(); }));
  ["rate", "pitch", "gap"].forEach((id) => $(id).addEventListener("input", updateLabels));
  $("hideScript").addEventListener("change", updateLabels);
  $("previewVoice").onclick = async () => {
    stop();
    const sample = "Ladies and gentlemen, thank you for joining me today. What we are about to explore together has the power to change how we think, and how we lead.";
    try {
      const r = await ttsFor(sample);
      audio.src = r.audio; audio.play();
    } catch { const u = new SpeechSynthesisUtterance(sample); u.voice = pickBrowserVoice(); speechSynthesis.speak(u); }
  };
  $("genAll").onclick = generateAll;
  $("play").onclick = togglePlay;
  $("stop").onclick = stop;
  $("prev").onclick = () => go(-1);
  $("next").onclick = () => go(1);
  $("regen").onclick = async () => {
    if (!S.deck) return;
    stop();
    $("teleprompter").innerHTML = `<p class="muted">Rewriting…</p>`;
    try { await ensureScript(S.cur, true); showSlide(S.cur); } catch (e) { toast(e.message, true); showSlide(S.cur); }
  };
  $("editToggle").onclick = toggleEdit;
  $("copyOne").onclick = () => {
    if (!S.deck) return;
    const s = S.deck.slides[S.cur];
    copyText(S.editing ? $("scriptEditor").value : s.script || s.notes || "");
  };
  $("copyAll").onclick = () => {
    if (!S.deck) return;
    copyText(S.deck.slides.map((s) => `Slide ${s.index}: ${s.title}\n\n${s.script || s.notes || ""}`).join("\n\n"));
  };
  $("fullscreen").onclick = () => $("stage").requestFullscreen?.();
  $("stage").ondblclick = () => document.fullscreenElement ? document.exitFullscreen() : $("stage").requestFullscreen?.();
  document.addEventListener("keydown", (e) => {
    if (!$("settingsModal").classList.contains("hidden")) {
      if (e.key === "Escape") $("settingsModal").classList.add("hidden");
      return;
    }
    if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    else if (e.key === "ArrowRight" || e.key === "PageDown") go(1);
    else if (e.key === "ArrowLeft" || e.key === "PageUp") go(-1);
    else if (e.key.toLowerCase() === "f") $("stage").requestFullscreen?.();
    else if (e.key === "Escape") { stop(); $("settingsModal").classList.add("hidden"); }
  });
}
/* ------------------------------------------------------------ status pills */
function renderStatus() {
  const L = S.cfg.llm, T = S.cfg.stt;
  const st = $("llmStatus");
  if (L.provider === "none") {
    st.textContent = "LLM: off";
    st.className = "pill warn";
    st.title = "Turned off in ⚙ Settings: notes are only tidied up";
  } else if (!L.enabled || (L.error && !L.loaded)) {
    st.textContent = `LLM ⚠ ${L.provider === "openai" ? "endpoint" : "local"}`;
    st.className = "pill err";
    st.title = L.error || "Not configured: open ⚙ Settings";
  } else if (L.provider === "openvino") {
    st.textContent = `LLM · ${L.model} · ${L.device}${L.loaded ? "" : " (loading…)"}`;
    st.className = "pill " + (L.loaded ? "ok" : "warn");
    st.title = L.error || (L.loaded ? "Local OpenVINO LLM ready" : "Compiling the model for the device (first run can take a few minutes; later runs use the cache)");
  } else {
    const where = L.provider === "openai" ? "endpoint" : L.device;
    st.textContent = `LLM · ${L.model} · ${where}`;
    st.className = "pill ok";
    st.title = L.provider === "openai" ? "OpenAI-compatible endpoint" : `${L.device} (cloud)`;
  }
  const tt = $("sttStatus");
  if (T.provider === "none") { tt.textContent = "Whisper: off"; tt.className = "pill warn"; tt.title = "Turned off in ⚙ Settings"; }
  else if (!T.available || (T.error && !T.loaded)) { tt.textContent = "Whisper ⚠"; tt.className = "pill err"; tt.title = T.error || "Not configured: open ⚙ Settings"; }
  else if (T.provider === "openai") { tt.textContent = `Speech · ${T.model} · endpoint`; tt.className = "pill ok"; tt.title = "OpenAI-compatible transcription endpoint"; }
  else if (T.provider === "gemini") { tt.textContent = `Speech · ${T.model} · Gemini`; tt.className = "pill ok"; tt.title = "Gemini API transcription (cloud)"; }
  else {
    tt.textContent = `Whisper · ${T.loaded ? T.device : "loading…"}`;
    tt.className = "pill " + (T.loaded ? "ok" : "warn");
    tt.title = T.error || T.model || "";
  }
  const sttOn = T.provider !== "none" && T.available;
  $("recBtn").title = sttOn ? "" : "Speech recognition is off or not configured (⚙ Settings)";
  if (S.deck) $("recBtn").disabled = !sttOn;
}
let _pollTimer = null;
function pollStatus() {
  clearTimeout(_pollTimer);
  const pending = () => S.cfg.llm.loading || (S.cfg.llm.provider === "openvino" && S.cfg.llm.enabled && !S.cfg.llm.loaded && !S.cfg.llm.error)
    || S.cfg.stt.loading || (S.cfg.stt.provider === "openvino" && S.cfg.stt.available && !S.cfg.stt.loaded && !S.cfg.stt.error);
  if (!pending()) return;
  _pollTimer = setTimeout(async () => {
    try { const c = await api("/api/config"); S.cfg.llm = c.llm; S.cfg.stt = c.stt; renderStatus(); } catch {}
    pollStatus();
  }, 3000);
}

/* ------------------------------------------------------------ settings (model endpoints) */
const PROVIDERS = ["openvino", "anthropic", "gemini", "openai", "none"];
const SET = { data: null, clearKey: { llm: false, stt: false }, clearProv: {} };
const card = (sec) => document.querySelector(`.set-card[data-sec=${sec}]`);
const fld = (sec, f) => card(sec).querySelector(`[data-f=${f}]`);

function renderKeyHints() {
  const keys = SET.data.settings.keys;
  document.querySelectorAll("[data-kh]").forEach((el) => {
    const k = el.dataset.kh;
    if (k === "_section") {
      const sec = el.closest(".set-card").dataset.sec, v = SET.data.settings[sec];
      el.textContent = SET.clearKey[sec] ? "will be removed on save" : v.api_key_set ? `saved ${v.api_key_hint}` : "";
      return;
    }
    const v = keys[k];
    const typed = [...document.querySelectorAll(`[data-k=${k}]`)].some((i) => i.value.trim());
    el.textContent = typed ? "new key (unsaved)" : SET.clearProv[k] ? "will be removed on save"
      : v.set ? `saved ${v.hint}` : v.env ? (k === "bedrock" ? "using AWS environment credentials" : "using environment variable") : "required";
    el.style.color = (!typed && !v.set && !v.env && !SET.clearProv[k]) ? "#e07070" : "";
  });
}

async function openSettings() {
  try { SET.data = await api("/api/settings"); } catch (e) { return toast(e.message, true); }
  const d = SET.data;
  SET.clearProv = {};
  for (const sec of ["llm", "stt"]) {
    const c = card(sec), v = d.settings[sec];
    SET.clearKey[sec] = false;
    const dev = fld(sec, "ov_device");
    dev.innerHTML = "";
    d.devices.forEach((x) => dev.add(new Option(`${x.id} · ${x.name}`, x.id)));
    dev.value = v.ov_device;
    const mod = fld(sec, "ov_model");
    mod.innerHTML = "";
    const list = d.local_models[sec === "llm" ? "llm" : "whisper"];
    list.forEach((m) => mod.add(new Option(m, m)));
    if (!list.length) mod.add(new Option("(none installed)", ""));
    mod.value = list.includes(v.ov_model) ? v.ov_model : (list[0] || "");
    const pre = fld(sec, "_preset");
    pre.innerHTML = '<option value="">Custom…</option>';
    d.presets[sec].forEach((p) => pre.add(new Option(p.label, p.id)));
    pre.value = (d.presets[sec].find((p) => p.base_url === v.base_url) || {}).id || "";
    ["base_url", "model", "extra_body", "temperature", "max_tokens", "claude_model", "claude_effort", "gemini_model",
     "claude_access", "bedrock_api", "bedrock_region", "bedrock_model"]
      .forEach((f) => { const el = fld(sec, f); if (el) el.value = v[f] ?? ""; });
    c.querySelectorAll("[data-f=filler_prompt]").forEach((el) => (el.checked = !!v.filler_prompt));
    c.querySelectorAll("input[type=password]").forEach((el) => (el.value = ""));
    setProvider(sec, v.provider);
    if (sec === "llm") setClaudeAccess();
    c.querySelector(".test-out").classList.add("hidden");
    showSecStatus(sec, d.status[sec]);
  }
  renderKeyHints();
  $("settingsModal").classList.remove("hidden");
}
function setProvider(sec, p) {
  const c = card(sec);
  c.dataset.provider = p;
  c.querySelectorAll(".prov button").forEach((b) => b.classList.toggle("on", b.dataset.p === p));
  PROVIDERS.forEach((x) => c.querySelector(".p-" + x)?.classList.toggle("hidden", x !== p));
  c.querySelector("[data-act=test]").disabled = p === "none";
  if (!SET.data) return;
  if (p !== SET.data.settings[sec].provider) {
    const el = c.querySelector(".set-status");
    el.textContent = p === "none" ? "Will be turned off on save" : "Not saved yet: ▶ Test it, then Save & apply";
    el.className = "set-status small warn";
  } else showSecStatus(sec, SET.data.status[sec]);
}
function setClaudeAccess() {
  const c = card("llm"), a = fld("llm", "claude_access").value || "anthropic";
  ["anthropic", "bedrock"].forEach((x) => c.querySelector(".ca-" + x).classList.toggle("hidden", x !== a));
  const geo = (fld("llm", "bedrock_region").value.match(/^(us|eu|ap)-/) || [])[1];
  fld("llm", "bedrock_model").placeholder = fld("llm", "bedrock_api").value === "mantle" ? "anthropic.claude-opus-5-5"
    : `${geo === "ap" ? "apac" : geo || "us"}.anthropic.claude-opus-5-5`;
}
function showSecStatus(sec, st) {
  const el = card(sec).querySelector(".set-status");
  if (!st) { el.textContent = ""; return; }
  const ready = sec === "llm" ? st.loaded && st.enabled : st.loaded && st.available;
  if (st.provider === "none") { el.textContent = "Off"; el.className = "set-status small warn"; return; }
  if (st.loading) { el.textContent = `Loading on ${st.device || "device"}…`; el.className = "set-status small warn"; return; }
  if (st.error && !ready) { el.textContent = "⚠ " + st.error.slice(0, 120); el.className = "set-status small err"; return; }
  const where = st.device && st.device !== "remote" ? (st.provider === "openvino" ? " on " : " · ") + st.device : "";
  el.textContent = ready ? `● Ready · ${st.model || ""}${where}` : "Not loaded yet";
  el.className = "set-status small " + (ready ? "ok" : "warn");
}
function collect(sec) {
  const c = card(sec), out = { provider: c.dataset.provider };
  ["ov_device", "ov_model", "base_url", "model", "extra_body", "claude_model", "claude_effort", "gemini_model",
     "claude_access", "bedrock_api", "bedrock_region", "bedrock_model"]
    .forEach((f) => { const el = fld(sec, f); if (el) out[f] = el.value; });
  ["temperature", "max_tokens"].forEach((f) => { const el = fld(sec, f); if (el && el.value !== "") out[f] = +el.value; });
  const fp = c.querySelector(`.p-${out.provider} [data-f=filler_prompt]`) || c.querySelector("[data-f=filler_prompt]");
  if (fp) out.filler_prompt = fp.checked;
  const key = fld(sec, "api_key").value.trim();
  if (key) out.api_key = key;
  else if (SET.clearKey[sec]) out.api_key = "__clear__";
  return out;
}
function collectKeys() {
  const keys = {};
  for (const k of ["anthropic", "gemini", "bedrock"]) {
    const typed = [...document.querySelectorAll(`[data-k=${k}]`)].map((el) => el.value.trim()).filter(Boolean);
    if (typed.length) keys[k] = typed.at(-1);
    else if (SET.clearProv[k]) keys[k] = "__clear__";
  }
  return keys;
}
async function saveSettings() {
  const btn = $("saveSettings");
  btn.disabled = true;
  try {
    SET.data = await api("/api/settings", { method: "PUT", body: { llm: collect("llm"), stt: collect("stt"), keys: collectKeys() } });
    const c = await api("/api/config");
    S.cfg.llm = c.llm; S.cfg.stt = c.stt;
    setTimeout(async () => { try { const c2 = await api("/api/config"); S.cfg.llm = c2.llm; S.cfg.stt = c2.stt; renderStatus(); pollStatus(); } catch {} }, 800);
    renderStatus(); pollStatus();
    $("settingsModal").classList.add("hidden");
    toast("Settings saved. Local models reload in the background (see status pills).");
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
}
async function testSection(sec) {
  const c = card(sec), out = c.querySelector(".test-out"), btn = c.querySelector("[data-act=test]");
  out.classList.remove("hidden");
  out.textContent = sec === "llm" ? "Asking the model for one sentence…" : "Synthesizing a test phrase and transcribing it…";
  btn.disabled = true;
  try {
    const r = await api(`/api/settings/test-${sec}`, { method: "POST", body: { ...collect(sec), keys: collectKeys() } });
    out.textContent = sec === "llm"
      ? `✓ ${r.seconds}s on ${r.device}\n${r.reply}`
      : `✓ ${r.seconds}s on ${r.device}\nexpected: ${r.expected}\nheard:    ${r.heard}`;
    showSecStatus(sec, (await api("/api/settings")).status[sec]);
  } catch (e) { out.textContent = "✗ " + e.message; }
  finally { btn.disabled = false; }
}
async function listRemoteModels(sec, btn) {
  const c = card(sec), out = c.querySelector(".test-out");
  const wrap = btn.closest("label"), input = wrap.querySelector("input"), dl = wrap.querySelector("datalist");
  out.classList.remove("hidden");
  out.textContent = "Fetching models…";
  try {
    const r = await api("/api/settings/list-models", {
      method: "POST", body: { section: sec, ...collect(sec), provider: btn.dataset.prov, keys: collectKeys() } });
    dl.innerHTML = r.models.map((m) => `<option value="${esc(m)}">`).join("");
    out.textContent = (r.suggested ? "Bedrock has no model list here. Common ids for this region (your account needs access to them):\n"
      : `${r.models.length} models available: click the Model field to pick one.\n`) + r.models.slice(0, 40).join("\n");
    if (!input.value && r.models.length) input.value = r.models[0];
  } catch (e) { out.textContent = "✗ " + e.message; }
}
function initSettings() {
  $("openSettings").onclick = openSettings;
  $("closeSettings").onclick = $("cancelSettings").onclick = () => $("settingsModal").classList.add("hidden");
  $("settingsModal").addEventListener("click", (e) => e.target.id === "settingsModal" && $("settingsModal").classList.add("hidden"));
  $("saveSettings").onclick = saveSettings;
  for (const sec of ["llm", "stt"]) {
    const c = card(sec);
    c.querySelectorAll(".prov button").forEach((b) => (b.onclick = () => !b.disabled && setProvider(sec, b.dataset.p)));
    c.querySelector("[data-act=test]").onclick = () => testSection(sec);
    c.querySelectorAll("[data-act=list]").forEach((b) => (b.onclick = () => listRemoteModels(sec, b)));
    c.querySelectorAll("[data-act=clearkey]").forEach((b) => (b.onclick = () => {
      const k = b.dataset.key;
      if (k === "_section") { SET.clearKey[sec] = true; fld(sec, "api_key").value = ""; }
      else { SET.clearProv[k] = true; document.querySelectorAll(`[data-k=${k}]`).forEach((el) => (el.value = "")); }
      renderKeyHints();
    }));
    if (sec === "llm") ["claude_access", "bedrock_api", "bedrock_region"].forEach((f) =>
      fld(sec, f).addEventListener("input", setClaudeAccess));
    fld(sec, "_preset").onchange = (e) => {
      const p = SET.data.presets[sec].find((x) => x.id === e.target.value);
      if (!p) return;
      fld(sec, "base_url").value = p.base_url;
      fld(sec, "model").value = p.model || "";
      if (fld(sec, "extra_body")) fld(sec, "extra_body").value = p.extra_body || "";
    };
  }
  // typing a shared key in one card mirrors it into the other card's field
  document.querySelectorAll("[data-k]").forEach((el) => el.addEventListener("input", () => {
    document.querySelectorAll(`[data-k=${el.dataset.k}]`).forEach((o) => o !== el && (o.value = el.value));
    SET.clearProv[el.dataset.k] = false;
    renderKeyHints();
  }));
}

/* ------------------------------------------------------------ rehearsal recording */
const R = { rec: null, stream: null, chunks: [], t0: 0, timer: null, ctx: null, raf: null, url: null };

async function toggleRecord() {
  if (!S.deck) return;
  if (S.recording) return R.rec?.stop();
  stop();
  try {
    R.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    return toast("Microphone access denied: " + e.message, true);
  }
  R.chunks = [];
  R.rec = new MediaRecorder(R.stream);
  R.rec.ondataavailable = (e) => e.data.size && R.chunks.push(e.data);
  R.rec.onstop = finishRecording;
  R.rec.start(250);
  S.recording = true;
  R.t0 = performance.now();
  $("recBtn").textContent = "■ Stop & analyse";
  $("recBtn").classList.add("on");
  R.timer = setInterval(() => {
    const s = Math.floor((performance.now() - R.t0) / 1000);
    $("recTime").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` +
      (S.refDur[S.cur] ? `  /  AI ${Math.round(S.refDur[S.cur])}s` : "");
  }, 250);
  // live level meter
  R.ctx = new AudioContext();
  const an = R.ctx.createAnalyser();
  an.fftSize = 1024;
  R.ctx.createMediaStreamSource(R.stream).connect(an);
  const buf = new Float32Array(an.fftSize);
  const tick = () => {
    an.getFloatTimeDomainData(buf);
    const rms = Math.sqrt(buf.reduce((a, v) => a + v * v, 0) / buf.length);
    $("recLevel").style.width = Math.min(100, rms * 400) + "%";
    R.raf = requestAnimationFrame(tick);
  };
  tick();
  const script = S.deck.slides[S.cur].script;
  $("feedback").innerHTML = $("recShowScript").checked && script
    ? `<div class="fb-section"><h4>Teleprompter</h4><div class="transcript">${esc(script)}</div></div>`
    : `<p class="muted">Recording… deliver slide ${S.cur + 1} as you would on stage.</p>`;
}

async function finishRecording() {
  S.recording = false;
  clearInterval(R.timer);
  cancelAnimationFrame(R.raf);
  R.ctx?.close();
  R.stream?.getTracks().forEach((t) => t.stop());
  $("recBtn").textContent = "● Record again";
  $("recBtn").classList.remove("on");
  $("recLevel").style.width = "0";
  const blob = new Blob(R.chunks, { type: R.rec.mimeType });
  if (R.url) URL.revokeObjectURL(R.url);
  R.url = URL.createObjectURL(blob);
  const slideIdx = S.cur;
  $("feedback").innerHTML = `<p class="muted">Transcribing with Whisper (${esc(S.cfg.stt.device || "endpoint")})…</p>`;
  try {
    const wav = await toWav16k(blob);
    const fd = new FormData();
    fd.append("audio", new Blob([wav], { type: "audio/wav" }), "take.wav");
    fd.append("deck_id", S.deck.id);
    fd.append("slide", slideIdx + 1);
    fd.append("script", S.deck.slides[slideIdx].script || S.deck.slides[slideIdx].notes || "");
    fd.append("prefs", JSON.stringify(prefs()));
    fd.append("reference_seconds", S.refDur[slideIdx] || 0);
    fd.append("coach", $("recCoach").checked && S.cfg.llm.enabled);
    if ($("recCoach").checked && S.cfg.llm.enabled)
      setTimeout(() => { if (!$("feedback").querySelector(".score")) $("feedback").innerHTML += `<p class="muted">Your coach is reviewing the take…</p>`; }, 2500);
    const res = await api("/api/rehearse", { method: "POST", body: fd });
    renderFeedback(res, slideIdx);
    saveHistory(slideIdx, res.metrics);
    renderRehearsalHistory();
  } catch (e) {
    $("feedback").innerHTML = `<p class="muted">Analysis failed: ${esc(e.message)}</p>`;
  }
}

async function toWav16k(blob) {
  const ctx = new AudioContext();
  const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  ctx.close();
  const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
  const src = off.createBufferSource();
  src.buffer = decoded;
  src.connect(off.destination);
  src.start();
  const pcm = (await off.startRendering()).getChannelData(0);
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o, str) => [...str].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF"); out.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, 16000, true); out.setUint32(28, 32000, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  w(36, "data"); out.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true);
  return out.buffer;
}

function grade(v, good, warn, higherBetter = true) {
  if (v == null) return "";
  return higherBetter ? (v >= good ? "good" : v >= warn ? "warn" : "bad") : (v <= good ? "good" : v <= warn ? "warn" : "bad");
}

function renderFeedback(res, slideIdx) {
  const m = res.metrics;
  const color = m.score >= 80 ? "#7bd88f" : m.score >= 60 ? "#e8b04a" : "#e07070";
  const verdict = m.score >= 85 ? "Stage-ready" : m.score >= 70 ? "Strong — polish the details" : m.score >= 55 ? "Getting there" : "Keep practising";
  const paceCls = m.wpm >= 120 && m.wpm <= 165 ? "good" : m.wpm >= 100 && m.wpm <= 185 ? "warn" : "bad";
  const paceHint = m.wpm < 120 ? "slow" : m.wpm > 165 ? "fast" : "ideal";
  const durCmp = m.reference_duration ? ` vs AI ${m.reference_duration}s` : "";
  const fillerList = Object.entries(m.fillers).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ×${v}`).join(", ") || "none";
  let transcript = esc(res.transcript || "(no speech detected)");
  Object.keys(m.fillers).forEach((f) => {
    transcript = transcript.replace(new RegExp(`\\b(${f.replace(/ /g, "\\s+")})\\b`, "gi"), "<mark>$1</mark>");
  });
  const coach = res.coaching
    ? `<div class="fb-section"><h4>Coach (${esc(res.llm_device || S.cfg.llm.model || "LLM")})</h4><pre>${esc(res.coaching)}</pre></div>` : "";
  $("feedback").innerHTML = `
    <div class="score">
      <div class="ring" style="--v:${m.score};--c:${color}"><span>${m.score}</span></div>
      <div><b>${verdict}</b><br><small class="muted">Slide ${slideIdx + 1} · STT ${m.stt_seconds}s on ${esc(res.stt_device || "")}
        ${m.coach_seconds ? ` · coach ${m.coach_seconds}s` : ""}</small><br>
        <audio controls src="${R.url}" style="height:28px;margin-top:4px;width:200px"></audio></div>
    </div>
    <div class="metrics">
      <div class="metric ${paceCls}"><b>${m.wpm}</b><small>words/min (${paceHint}; 120–165 ideal)</small></div>
      <div class="metric ${grade(m.fillers_per_min, 2, 5, false)}"><b>${m.filler_total}</b><small>fillers (${m.fillers_per_min}/min)</small></div>
      <div class="metric ${grade(m.coverage, 80, 60)}"><b>${m.coverage ?? "–"}%</b><small>key points covered</small></div>
      <div class="metric ${grade(m.long_pauses.filter((p) => p.duration > 3).length, 0, 1, false)}"><b>${m.long_pauses.length}</b><small>long pauses (>1.2s)</small></div>
      <div class="metric"><b>${m.duration}s</b><small>duration${durCmp}</small></div>
      <div class="metric"><b>${m.fidelity ?? "–"}%</b><small>wording match to script</small></div>
    </div>
    <div class="fb-section"><h4>Filler words</h4><pre>${esc(fillerList)}</pre></div>
    ${m.missed.length ? `<div class="fb-section"><h4>Points you missed</h4>${m.missed.map((x) => `<div class="missed">“${esc(x)}”</div>`).join("")}</div>` : ""}
    ${coach}
    <div class="fb-section"><h4>What you said</h4><div class="transcript">${transcript}</div>
      <button class="btn ghost small" id="copyTranscript" style="margin-top:6px">⧉ Copy transcript</button></div>`;
  $("copyTranscript").onclick = () => copyText(res.transcript);
}

function historyKey() { return `stageHist:${S.deck.id}`; }
function saveHistory(i, m) {
  const h = JSON.parse(localStorage.getItem(historyKey()) || "{}");
  (h[i] ||= []).push({ t: Date.now(), score: m.score, wpm: m.wpm, fillers: m.filler_total, coverage: m.coverage });
  h[i] = h[i].slice(-10);
  localStorage.setItem(historyKey(), JSON.stringify(h));
}
function renderRehearsalHistory() {
  if (!S.deck) return;
  const rows = (JSON.parse(localStorage.getItem(historyKey()) || "{}")[S.cur] || []).slice().reverse();
  $("history").innerHTML = rows.length ? `<h3>Your takes on this slide</h3><table>
    <tr><th>When</th><th>Score</th><th>WPM</th><th>Fillers</th><th>Covered</th></tr>
    ${rows.map((r) => `<tr><td>${new Date(r.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</td>
      <td>${r.score}</td><td>${r.wpm}</td><td>${r.fillers}</td><td>${r.coverage ?? "–"}%</td></tr>`).join("")}</table>` : "";
}

init().catch((e) => toast("Failed to start: " + e.message, true));
