// Multitrack playback with the Web Audio API. Every track is decoded into memory and
// started on the same audio clock tick, so they stay sample-accurately in sync.

export class Player {
  constructor() {
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    // Without this, iPhones stay silent when the ring/silent switch is on silent.
    if (navigator.audioSession) navigator.audioSession.type = "playback";
    this.master = this.ctx.createGain();
    this.master.connect(this.ctx.destination);
    this.channels = new Map(); // id -> { gain, pan, volume, muted, solo, buffer }
    this.sources = [];
    this.playing = false;
    this.offset = 0; // song position (s) when playback last started
    this.startedAt = 0; // ctx time playback last started
    this.duration = 0;
    this.loop = { on: false, start: 0, end: 0 };
    this.onEnded = null;
  }

  addChannel(id, { pan = 0, volume = 1, muted = false, solo = false } = {}) {
    const gain = this.ctx.createGain();
    const panner = this.ctx.createStereoPanner ? this.ctx.createStereoPanner() : null;
    if (panner) {
      panner.pan.value = pan;
      gain.connect(panner).connect(this.master);
    } else {
      gain.connect(this.master);
    }
    this.channels.set(id, { gain, volume, muted, solo, buffer: null });
    this.updateGains();
  }

  async load(url) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`Couldn't load a track (${response.status})`);
    const data = await response.arrayBuffer();
    return await new Promise((resolve, reject) => this.ctx.decodeAudioData(data, resolve, reject));
  }

  // Swap the audio a channel plays (e.g. recreated <-> original) without losing its place.
  setBuffer(id, buffer) {
    this.channels.get(id).buffer = buffer;
    this.duration = Math.max(this.duration, buffer ? buffer.duration : 0);
    if (this.playing) this.seek(this.position());
  }

  set(id, changes) {
    Object.assign(this.channels.get(id), changes);
    this.updateGains();
  }

  updateGains() {
    const anySolo = [...this.channels.values()].some((c) => c.solo);
    const now = this.ctx.currentTime;
    for (const c of this.channels.values()) {
      const audible = anySolo ? c.solo : !c.muted;
      c.gain.gain.setTargetAtTime(audible ? c.volume : 0, now, 0.015);
    }
  }

  audible(id) {
    const anySolo = [...this.channels.values()].some((c) => c.solo);
    const c = this.channels.get(id);
    return anySolo ? c.solo : !c.muted;
  }

  setMasterVolume(v) {
    this.master.gain.setTargetAtTime(v, this.ctx.currentTime, 0.015);
  }

  position() {
    if (!this.playing) return this.offset;
    let pos = this.offset + (this.ctx.currentTime - this.startedAt);
    const { on, start, end } = this.loop;
    if (on && end > start && pos >= end) pos = start + ((pos - start) % (end - start));
    return Math.min(pos, this.duration);
  }

  async play() {
    if (this.playing) return;
    await this.ctx.resume();
    if (this.offset >= this.duration - 0.05) this.offset = this.loop.on ? this.loop.start : 0;
    const when = this.ctx.currentTime + 0.06;
    for (const c of this.channels.values()) {
      if (!c.buffer) continue;
      const src = this.ctx.createBufferSource();
      src.buffer = c.buffer;
      if (this.loop.on && this.loop.end > this.loop.start) {
        src.loop = true;
        src.loopStart = this.loop.start;
        src.loopEnd = this.loop.end;
      }
      src.connect(c.gain);
      src.start(when, Math.min(this.offset, c.buffer.duration));
      this.sources.push(src);
    }
    this.startedAt = when;
    this.playing = true;
    this._watchEnd();
  }

  pause() {
    if (!this.playing) return;
    this.offset = this.position();
    this._stopSources();
    this.playing = false;
  }

  seek(pos) {
    const wasPlaying = this.playing;
    if (wasPlaying) {
      this._stopSources();
      this.playing = false;
    }
    this.offset = Math.max(0, Math.min(pos, this.duration));
    if (wasPlaying) this.play();
  }

  setLoop(on, start = this.loop.start, end = this.loop.end) {
    const pos = this.position();
    this.loop = { on, start, end };
    // Playback has to begin inside the loop for the browser to loop it.
    const target = on && (pos < start || pos >= end) ? start : pos;
    if (this.playing) this.seek(target);
    else this.offset = target;
  }

  _stopSources() {
    for (const s of this.sources) {
      try { s.stop(); } catch { /* already stopped */ }
      s.disconnect();
    }
    this.sources = [];
  }

  _watchEnd() {
    clearInterval(this._endTimer);
    this._endTimer = setInterval(() => {
      if (!this.playing) return clearInterval(this._endTimer);
      if (!this.loop.on && this.position() >= this.duration - 0.02) {
        this.pause();
        this.offset = 0;
        clearInterval(this._endTimer);
        if (this.onEnded) this.onEnded();
      }
    }, 200);
  }

  close() {
    this._stopSources();
    clearInterval(this._endTimer);
    this.ctx.close();
  }
}
