# Worship Tracks

Upload a song. An AI running on your own Mac works out its layout (sections, tempo, key, instruments) and has an **AI band** play a new backing track with the same layout and timing. The backing track is split into **drums, bass, guitar, keys and pads**, plus the original vocals as a guide. Your team opens it in a web browser on any phone, tablet or computer, mutes their own part, and plays along.

- **Free.** It uses only open-source AI models and runs on your Mac. No subscriptions or cloud fees.
- **Private.** Songs never leave your Mac.
- **Works on any device.** iPhone, iPad, Android, Windows or Mac, in the browser, with nothing to install.

## How it works

1. **Listen.** [Demucs](https://github.com/adefossez/demucs) (from Meta) picks out each instrument in the recording.
2. **Lay out the song.** The app finds the beat, tempo, key and time signature, and splits the song into sections (intro, verses, choruses, bridge...) with their start times and lengths. [ACE-Step](https://github.com/ace-step/ACE-Step-1.5) listens too, and describes the style and instruments.
3. **The AI band plays it.** ACE-Step 1.5, an open-source music generator (MIT licence), plays a brand-new recording of the song without the vocals ("cover" mode). It follows the song's structure, chords and timing, in the sound described under **AI band**. The app checks the new recording lines up with the original and nudges it into place.
4. **Split for the team.** The new recording is split into instruments, so each musician can mute their own part.

On each song's page you'll see the **song layout** (tap a section to jump there, or loop it) and the **AI band** settings. Change the description of the sound, choose how closely to follow the original, and make a **new take**. Every take comes out different. Every track also has an **Original** switch that plays the real instrument from the recording instead.

**What to expect:** the AI band sounds like real players, but it isn't the original arrangement. Parts, fills and voicings are its own. Bigger changes in a song (a breakdown, a key change) are followed more loosely than the basic chords and groove. If a take misses, make another.

If the AI band isn't installed or running, the app makes tracks **note by note** instead: it writes down each part and plays it on virtual instruments. That's quicker but sounds much more like MIDI.

### Add a chord chart

Add the song's **chord chart** (a SongSelect PDF, a ChordPro file, or chords typed in like `G D Em C`) when uploading, or later from the song's page. The chart's key, tempo, time signature and section names are then used for the song's layout and the AI band, and the player shows the current and next chord as the song plays. Scanned PDFs (pictures of a page) have no text to read, so type the chords for those.

## Setting up the Mac mini (one time)

You need an Apple Silicon Mac (M1 or newer) and about 20 GB of free space. The AI band needs at least 16 GB of memory; with less, tracks are made note by note.

1. **Install Homebrew** if you don't have it. Open **Terminal** and paste:

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

   Follow its instructions. At the end it shows two commands to "add Homebrew to your PATH". Run those too.

2. **Download this project** and run setup:

   ```bash
   cd ~
   git clone https://github.com/nforbes35/worship-tracks.git
   cd worship-tracks
   scripts/setup.sh
   ```

   This takes around 10 minutes the first time. It installs the tools, downloads the AI models and instrument sounds, and finishes by testing that everything works.

3. **Start it automatically**, so it's always running:

   ```bash
   scripts/autostart.sh install
   ```

   If macOS asks whether to allow incoming network connections, click **Allow**.

   Then **check the AI band works**. The first time, ACE-Step downloads its models (about 10 GB), so this can take a while:

   ```bash
   uv run python scripts/check_ai_band.py
   ```

   It plays a short test song and saves the result as `ai-band-check.wav`, so you can hear it.

4. **Keep the Mac awake.** In **System Settings → Energy**:
   - turn on **Prevent automatic sleeping when the display is off**
   - turn on **Start up automatically after a power failure**

   The server starts when you log in. For a Mac mini that restarts by itself, turn on automatic login in **System Settings → Users & Groups**. That isn't available if FileVault is on; in that case, log in once after a restart.

5. **Open it.** On the Mac mini, go to http://localhost:8000. From other devices on the same Wi-Fi, use the address that `scripts/start.sh` prints, such as `http://your-mac-mini.local:8000`.

   On iPhone, tap **Share → Add to Home Screen** for an app-like icon.

## Using it away from home

For your team to use it from anywhere, use **[Tailscale](https://tailscale.com)**. It's free for small groups and doesn't require changing anything on your router.

1. Install Tailscale on the Mac mini and sign in.
2. Invite your team (Tailscale admin console → **Users → Invite**). Each person installs Tailscale on their phone or computer.
3. They open `http://<mac-mini-name>:8000`, using the Mac mini's name from the Tailscale app.

Only people in your Tailscale network can reach it.

## Team passcode (optional)

To make people enter a passcode once per device, edit `~/WorshipTracks/config.env`:

```bash
WT_PASSCODE=your passcode here
```

Then restart: `scripts/autostart.sh restart`. Changing the passcode later signs everyone out.

## Everyday commands

| What | Command (run inside the `worship-tracks` folder) |
| --- | --- |
| Restart the server | `scripts/autostart.sh restart` |
| See what it's doing | `scripts/autostart.sh logs` |
| Stop it starting automatically | `scripts/autostart.sh uninstall` |
| See what the AI band is doing | `scripts/autostart.sh logs band` |
| Run it just in this Terminal window | `scripts/start.sh` (and `scripts/acestep.sh run` in another for the AI band) |
| Update to the newest version | `git pull && uv sync && scripts/autostart.sh restart` |

Songs and tracks are stored in `~/WorshipTracks/songs`, one folder per song. Each song's folder also has a `midi` folder with every recreated part as a MIDI file, which you can open in GarageBand or Logic.

## A note on copyright

Uploading a song to make practice tracks for your own team is generally treated like using a recording to learn a part. Sharing the tracks outside your team, or using them in services or streams, is covered by your licensing (e.g. CCLI and its rehearsal/multitrack licenses), not by this app.

## For developers

```bash
uv sync                                  # install (Python 3.11, managed by uv)
uv run pytest                            # tests (need ffmpeg, fluidsynth and a SoundFont)
uv run python tests/eval_transcription.py   # transcription accuracy per instrument
```

- `server/worship_tracks/pipeline.py`: song in, tracks out (separate → transcribe → render).
- `server/worship_tracks/transcribe.py`: Basic Pitch wrapper, note cleanup, and the drum transcriber.
- `server/worship_tracks/chords.py`: chord symbols, and reading chord charts (PDF, ChordPro, text).
- `server/worship_tracks/arrange.py`: with a chart, finds the chord on each beat (Viterbi over beat-synced chroma) and builds the guitar, keys, pad and bass parts from it.
- `server/worship_tracks/structure.py`: the song's layout (sections, key).
- `server/worship_tracks/acestep.py`, `backing.py`: the AI band: ACE-Step's API, generating a take and lining it up. `tests/fake_acestep.py` stands in for ACE-Step in tests.
- `server/worship_tracks/rhythm.py`: the beat grid.
- `server/worship_tracks/instruments.py`: the tracks, their sounds and transcription settings.
- `server/worship_tracks/library.py`: song storage and the one-at-a-time processing queue.
- `server/worship_tracks/app.py`: the FastAPI web server.
- `server/worship_tracks/web/`: the web app (plain HTML/JS; `player.js` is the Web Audio multitrack player).
- `tests/synthetic.py` builds a test song from known parts, so transcription can be scored without real recordings.
