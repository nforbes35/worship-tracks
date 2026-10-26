import { Player } from "./player.js";

const view = document.getElementById("view");
let cleanup = () => {};

// ---------- helpers ----------

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function fmt(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, { credentials: "same-origin", ...options });
  if (response.status === 401 && path !== "/login") {
    location.hash = "#/login";
    throw new Error("Enter the team passcode");
  }
  if (!response.ok) {
    let message = `Something went wrong (${response.status})`;
    try { message = (await response.json()).detail || message; } catch { /* not JSON */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

const icons = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M12 5V2L7 6l5 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z"/></svg>',
  fwd: '<svg viewBox="0 0 24 24"><path d="M12 5V2l5 4-5 4V7a5 5 0 1 0 5 5h2a7 7 0 1 1-7-7z"/></svg>',
};

function loadMix(id) {
  try { return JSON.parse(localStorage.getItem(`mix:${id}`)) || {}; } catch { return {}; }
}
function saveMix(id, mix) {
  try { localStorage.setItem(`mix:${id}`, JSON.stringify(mix)); } catch { /* private mode */ }
}

// ---------- router ----------

async function route() {
  cleanup();
  cleanup = () => {};
  const hash = location.hash || "#/";
  const song = hash.match(/^#\/song\/([0-9a-f]{12})$/);
  try {
    if (hash === "#/login") return await loginView();
    if (song) return await songView(song[1]);
    return await libraryView();
  } catch (err) {
    view.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}
window.addEventListener("hashchange", route);
route();

// ---------- login ----------

async function loginView() {
  view.innerHTML = `
    <form class="card stack" id="login">
      <h1>Team passcode</h1>
      <p class="muted">Ask whoever runs the Mac mini for the passcode. You only enter it once on this device.</p>
      <input type="password" name="passcode" autocomplete="current-password" required autofocus>
      <p class="error" id="msg" hidden></p>
      <button class="primary" type="submit">Continue</button>
    </form>`;
  const form = view.querySelector("#login");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = form.querySelector("#msg");
    try {
      await api("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passcode: form.passcode.value }),
      });
      location.hash = "#/";
    } catch (err) {
      msg.textContent = err.message;
      msg.hidden = false;
    }
  });
}

// ---------- library ----------

async function libraryView() {
  view.innerHTML = `
    <section class="card stack">
      <label class="file-drop" id="drop">
        <input type="file" id="file" accept="audio/*,.mp3,.m4a,.wav,.aiff,.flac">
        <strong>Add a song</strong><br>
        <span class="muted small" id="picked">Choose an MP3, M4A or WAV file</span>
      </label>
      <input type="text" id="title" placeholder="Song title (optional)" maxlength="120">
      <p class="small error" id="upload-msg" hidden></p>
      <button class="primary" id="upload" disabled>Make play-along tracks</button>
    </section>
    <h2>Songs</h2>
    <ul class="songs" id="songs"><li class="muted">Loading…</li></ul>`;

  const fileInput = view.querySelector("#file");
  const drop = view.querySelector("#drop");
  const uploadBtn = view.querySelector("#upload");
  const msg = view.querySelector("#upload-msg");
  let file = null;

  const pick = (f) => {
    file = f || null;
    view.querySelector("#picked").textContent = file ? file.name : "Choose an MP3, M4A or WAV file";
    uploadBtn.disabled = !file;
    const title = view.querySelector("#title");
    if (file && !title.value) title.value = file.name.replace(/\.[^.]+$/, "");
  };
  fileInput.addEventListener("change", () => pick(fileInput.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    pick(e.dataTransfer.files[0]);
  });

  uploadBtn.addEventListener("click", () => {
    if (!file) return;
    msg.hidden = true;
    uploadBtn.disabled = true;
    const form = new FormData();
    form.append("file", file);
    form.append("title", view.querySelector("#title").value);
    // XHR rather than fetch, for upload progress.
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/songs");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) uploadBtn.textContent = `Uploading… ${Math.round((e.loaded / e.total) * 100)}%`;
    };
    xhr.onload = () => {
      uploadBtn.textContent = "Make play-along tracks";
      if (xhr.status === 401) return (location.hash = "#/login");
      if (xhr.status >= 300) {
        let detail = "Upload failed";
        try { detail = JSON.parse(xhr.responseText).detail || detail; } catch { /* not JSON */ }
        msg.textContent = detail;
        msg.hidden = false;
        uploadBtn.disabled = false;
        return;
      }
      fileInput.value = "";
      view.querySelector("#title").value = "";
      pick(null);
      refresh();
    };
    xhr.onerror = () => {
      uploadBtn.textContent = "Make play-along tracks";
      msg.textContent = "Couldn't reach the Mac mini. Check you're connected and try again.";
      msg.hidden = false;
      uploadBtn.disabled = false;
    };
    xhr.send(form);
  });

  let timer = null;
  const list = view.querySelector("#songs");
  async function refresh() {
    clearTimeout(timer);
    const songs = await api("/songs");
    list.innerHTML = songs.length
      ? songs.map(songRow).join("")
      : '<li class="muted">No songs yet. Add one above.</li>';
    if (songs.some((s) => s.status === "queued" || s.status === "processing")) {
      timer = setTimeout(() => refresh().catch(() => {}), 2000);
    }
  }
  cleanup = () => clearTimeout(timer);
  await refresh();
}

function songRow(s) {
  let status = "";
  if (s.status === "ready") status = `${fmt(s.duration)} · ${s.tracks.filter((t) => t.present).length} tracks`;
  else if (s.status === "failed") status = '<span class="error">Couldn\'t make tracks · tap to retry</span>';
  else status = `${esc(s.step)}<div class="bar"><div style="width:${Math.round(s.progress * 100)}%"></div></div>`;
  return `<li><a class="card song" href="#/song/${s.id}">
      <div class="grow"><div class="title">${esc(s.title)}</div><div class="small muted">${status}</div></div>
      <span class="chev" aria-hidden="true">›</span></a></li>`;
}

// ---------- song ----------

async function songView(id) {
  let song = await api(`/songs/${id}`);
  let timer = null;
  cleanup = () => clearTimeout(timer);

  // Not ready yet: show progress until it is.
  while (song.status !== "ready") {
    view.innerHTML = `
      <a href="#/" class="small">‹ All songs</a>
      <h1>${esc(song.title)}</h1>
      <div class="card stack">
        ${song.status === "failed"
          ? `<p class="error">Couldn't make tracks for this song.</p><p class="small muted">${esc(song.error || "")}</p>
             <div class="row"><button class="primary" id="retry">Try again</button><button class="danger" id="delete">Delete song</button></div>`
          : `<div>${esc(song.step)}…</div><div class="bar"><div style="width:${Math.round(song.progress * 100)}%"></div></div>
             <p class="small muted">${song.status === "queued" ? "Another song is being processed first." : "This usually takes a minute or two. You can leave this page; it keeps going."}</p>`}
      </div>`;
    if (song.status === "failed") {
      view.querySelector("#retry").onclick = async () => { await api(`/songs/${id}/retry`, { method: "POST" }); route(); };
      view.querySelector("#delete").onclick = () => deleteSong(song);
      return;
    }
    await new Promise((resolve) => { timer = setTimeout(resolve, 2000); });
    if (location.hash !== `#/song/${id}`) return;
    song = await api(`/songs/${id}`);
  }

  await playerView(song);
}

async function deleteSong(song) {
  if (!confirm(`Delete "${song.title}" for everyone?`)) return;
  await api(`/songs/${song.id}`, { method: "DELETE" });
  location.hash = "#/";
}

async function playerView(song) {
  const base = `/api/songs/${song.id}/files`;
  const version = Math.round(song.created) + (song.processing_seconds || 0);
  const saved = loadMix(song.id);
  saved.tracks ||= {};
  const tracks = song.tracks.filter((t) => t.present);
  const missing = song.tracks.filter((t) => !t.present);

  view.innerHTML = `
    <a href="#/" class="small">‹ All songs</a>
    <h1>${esc(song.title)}</h1>
    <section class="card stack">
      <input type="range" id="seek" min="0" max="${song.duration}" step="0.1" value="0" aria-label="Position">
      <div class="time"><span id="pos">0:00</span><span>${fmt(song.duration)}</span></div>
      <div class="transport">
        <button class="icon" id="back" aria-label="Back 10 seconds">${icons.back}</button>
        <button class="big primary" id="play" aria-label="Play" disabled>${icons.play}</button>
        <button class="icon" id="fwd" aria-label="Forward 10 seconds">${icons.fwd}</button>
      </div>
      <p class="small muted" id="status" style="text-align:center">Loading tracks…</p>
      <div class="loop">
        <button id="loop-a">Loop from here</button>
        <button id="loop-b">Loop to here</button>
        <button id="loop-toggle">Loop off</button>
        <span class="small muted" id="loop-range"></span>
      </div>
    </section>

    <h2>Tracks</h2>
    <ul class="tracks card" style="padding:0">
      ${tracks.map((t) => trackRow(t, saved.tracks[t.id] || {})).join("")}
      <li class="track" data-id="mix">
        <div><div class="name">Original recording</div><div class="small muted">The song as uploaded</div></div>
        <div class="controls">
          <button class="toggle" data-action="reference">Off</button>
          <input type="range" min="0" max="1.2" step="0.01" value="${saved.tracks.mix?.volume ?? 0.8}" data-action="volume" aria-label="Original recording volume">
        </div>
      </li>
    </ul>
    ${missing.length ? `<p class="small muted">Not found in this song: ${missing.map((t) => esc(t.label)).join(", ")}.</p>` : ""}

    <h2>Master volume</h2>
    <div class="card"><input type="range" id="master" min="0" max="1.5" step="0.01" value="${saved.master ?? 1}" aria-label="Master volume"></div>

    <h2>Song</h2>
    <div class="card stack">
      <p class="small muted">The AI writes down each part and plays it back with virtual instruments. Switch a track to <b>Original</b> to hear the real instrument pulled out of the recording instead.</p>
      <div class="row" style="flex-wrap:wrap">
        <button id="rename">Rename</button>
        <button class="danger" id="delete">Delete song</button>
      </div>
    </div>`;

  const player = new Player();
  const buffers = new Map(); // url -> Promise<AudioBuffer>
  const bufferFor = (url) => {
    if (!buffers.has(url)) buffers.set(url, player.load(url));
    return buffers.get(url);
  };
  const state = {}; // id -> { source: "ai" | "original" }

  for (const t of tracks) {
    const s = saved.tracks[t.id] || {};
    state[t.id] = { source: s.source === "original" ? "original" : "ai" };
    player.addChannel(t.id, { pan: t.pan, volume: s.volume ?? 1, muted: !!s.muted, solo: !!s.solo });
  }
  player.addChannel("mix", { volume: saved.tracks.mix?.volume ?? 0.8, muted: true });
  player.setMasterVolume(saved.master ?? 1);

  const urlFor = (id) =>
    id === "mix" ? `${base}/mix.m4a?v=${version}`
      : `${base}/${state[id].source === "original" ? "original" : "tracks"}/${id}.m4a?v=${version}`;

  const persist = () => {
    const mix = { master: Number(view.querySelector("#master").value), tracks: {} };
    for (const [id, c] of player.channels) {
      mix.tracks[id] = { volume: c.volume, muted: c.muted, solo: c.solo, source: state[id]?.source };
    }
    delete mix.tracks.mix.muted; // the reference starts off every time
    saveMix(song.id, mix);
  };

  const statusEl = view.querySelector("#status");
  const playBtn = view.querySelector("#play");
  let loaded = 0;
  try {
    await Promise.all(tracks.map(async (t) => {
      player.setBuffer(t.id, await bufferFor(urlFor(t.id)));
      statusEl.textContent = `Loading tracks… ${++loaded} of ${tracks.length}`;
    }));
    statusEl.textContent = "";
    playBtn.disabled = false;
  } catch (err) {
    statusEl.innerHTML = `<span class="error">${esc(err.message)}</span>`;
  }
  player.duration = Math.max(player.duration, song.duration);

  // Transport
  const seek = view.querySelector("#seek");
  const pos = view.querySelector("#pos");
  let dragging = false;
  const showPlayState = () => {
    playBtn.innerHTML = player.playing ? icons.pause : icons.play;
    playBtn.setAttribute("aria-label", player.playing ? "Pause" : "Play");
  };
  playBtn.onclick = async () => {
    if (player.playing) player.pause();
    else await player.play();
    showPlayState();
  };
  player.onEnded = showPlayState;
  view.querySelector("#back").onclick = () => player.seek(player.position() - 10);
  view.querySelector("#fwd").onclick = () => player.seek(player.position() + 10);
  seek.addEventListener("input", () => { dragging = true; pos.textContent = fmt(seek.value); });
  seek.addEventListener("change", () => { dragging = false; player.seek(Number(seek.value)); });

  const tick = () => {
    if (!dragging) {
      const p = player.position();
      seek.value = p;
      pos.textContent = fmt(p);
    }
    raf = requestAnimationFrame(tick);
  };
  let raf = requestAnimationFrame(tick);

  const onKey = (e) => {
    if (e.code === "Space" && e.target === document.body) {
      e.preventDefault();
      playBtn.click();
    }
  };
  document.addEventListener("keydown", onKey);

  // Loop
  const loopToggle = view.querySelector("#loop-toggle");
  const loopRange = view.querySelector("#loop-range");
  let loopStart = 0;
  let loopEnd = song.duration;
  const showLoop = () => {
    loopToggle.textContent = player.loop.on ? "Loop on" : "Loop off";
    loopToggle.classList.toggle("on", player.loop.on);
    loopRange.textContent = loopStart > 0 || loopEnd < song.duration ? `${fmt(loopStart)} – ${fmt(loopEnd)}` : "";
  };
  view.querySelector("#loop-a").onclick = () => {
    loopStart = player.position();
    if (loopEnd <= loopStart + 1) loopEnd = song.duration;
    player.setLoop(true, loopStart, loopEnd);
    showLoop();
  };
  view.querySelector("#loop-b").onclick = () => {
    const p = player.position();
    if (p <= loopStart + 1) return;
    loopEnd = p;
    player.setLoop(true, loopStart, loopEnd);
    showLoop();
  };
  loopToggle.onclick = () => {
    player.setLoop(!player.loop.on, loopStart, loopEnd);
    showLoop();
  };

  // Track controls
  const refreshRows = () => {
    for (const row of view.querySelectorAll(".track[data-id]")) {
      const id = row.dataset.id;
      const c = player.channels.get(id);
      row.classList.toggle("silent", id !== "mix" && !player.audible(id));
      const mute = row.querySelector('[data-action="mute"]');
      const solo = row.querySelector('[data-action="solo"]');
      if (mute) mute.classList.toggle("on", c.muted);
      if (solo) solo.classList.toggle("on", c.solo);
      row.querySelectorAll("[data-source]").forEach((b) => b.classList.toggle("on", b.dataset.source === state[id]?.source));
      const ref = row.querySelector('[data-action="reference"]');
      if (ref) { ref.textContent = c.muted ? "Off" : "On"; ref.classList.toggle("on", !c.muted); }
    }
  };
  refreshRows();

  view.querySelector(".tracks").addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const id = btn.closest(".track").dataset.id;
    const c = player.channels.get(id);
    const action = btn.dataset.action;
    if (action === "mute") player.set(id, { muted: !c.muted });
    if (action === "solo") player.set(id, { solo: !c.solo });
    if (action === "reference") {
      if (c.muted && !c.buffer) {
        btn.textContent = "Loading…";
        player.setBuffer("mix", await bufferFor(urlFor("mix")));
      }
      player.set("mix", { muted: !c.muted });
    }
    if (btn.dataset.source && btn.dataset.source !== state[id].source) {
      state[id].source = btn.dataset.source;
      refreshRows();
      btn.textContent = "Loading…";
      try {
        player.setBuffer(id, await bufferFor(urlFor(id)));
      } finally {
        btn.textContent = btn.dataset.source === "ai" ? "AI" : "Original";
      }
    }
    refreshRows();
    persist();
  });
  view.querySelector(".tracks").addEventListener("input", (e) => {
    if (e.target.dataset.action !== "volume") return;
    player.set(e.target.closest(".track").dataset.id, { volume: Number(e.target.value) });
    persist();
  });
  view.querySelector("#master").addEventListener("input", (e) => {
    player.setMasterVolume(Number(e.target.value));
    persist();
  });

  view.querySelector("#rename").onclick = async () => {
    const title = prompt("Song title", song.title);
    if (!title || !title.trim()) return;
    song = await api(`/songs/${song.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    view.querySelector("h1").textContent = song.title;
  };
  view.querySelector("#delete").onclick = () => deleteSong(song);

  cleanup = () => {
    cancelAnimationFrame(raf);
    document.removeEventListener("keydown", onKey);
    player.close();
  };
}

function trackRow(t, saved) {
  const source = saved.source === "original" ? "original" : "ai";
  return `
    <li class="track" data-id="${t.id}">
      <div>
        <div class="name">${esc(t.label)}</div>
        <div class="segmented" role="group" aria-label="${esc(t.label)} sound">
          <button data-source="ai" class="${source === "ai" ? "on" : ""}">AI</button>
          <button data-source="original" class="${source === "original" ? "on" : ""}">Original</button>
        </div>
      </div>
      <div class="controls">
        <button class="toggle mute" data-action="mute" aria-label="Mute ${esc(t.label)}">M</button>
        <button class="toggle solo" data-action="solo" aria-label="Solo ${esc(t.label)}">S</button>
        <input class="grow" type="range" min="0" max="1.5" step="0.01" value="${saved.volume ?? 1}" data-action="volume" aria-label="${esc(t.label)} volume">
      </div>
    </li>`;
}
