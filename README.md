# Worship Tracks

Upload a song, and an AI running on your own Mac listens to it and recreates each instrument as its own play-along track: **drums, bass, guitar, keys, pads, and the vocal melody**. Your team opens it in a web browser on any phone, tablet or computer, mutes their own part, and plays along.

- **Free.** It uses only open-source AI models and runs on your Mac. No subscriptions or cloud fees.
- **Private.** Songs never leave your Mac.
- **Works on any device.** iPhone, iPad, Android, Windows or Mac, in the browser, with nothing to install.

## How it works

1. **Listen.** [Demucs](https://github.com/adefossez/demucs) (from Meta) picks out each instrument in the recording.
2. **Write it down.** [Basic Pitch](https://github.com/spotify/basic-pitch) (from Spotify) transcribes the notes of each instrument. Drums are detected by their hits (kick, snare, hi-hat, crash).
3. **Play it back.** Each part is played on virtual instruments ([GeneralUser GS](https://github.com/mrbumpy409/GeneralUser-GS)) at the same volume it had in the original mix.

In the player, every track also has an **Original** switch. It plays the real instrument picked out of the recording instead of the recreation, which helps when the recreation of a part sounds rough.

**What to expect:** drums, bass, keys and melody come out close to the original. Guitar is recreated as clean chords, but strumming patterns and effects are simplified. Pads catch the main chords. Recreated tracks sound like a good MIDI arrangement of the song, not the original band.

## Setting up the Mac mini (one time)

You need an Apple Silicon Mac (M1 or newer) and about 5 GB of free space.

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
| Run it just in this Terminal window | `scripts/start.sh` |
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
- `server/worship_tracks/instruments.py`: the tracks, their sounds and transcription settings.
- `server/worship_tracks/library.py`: song storage and the one-at-a-time processing queue.
- `server/worship_tracks/app.py`: the FastAPI web server.
- `server/worship_tracks/web/`: the web app (plain HTML/JS; `player.js` is the Web Audio multitrack player).
- `tests/synthetic.py` builds a test song from known parts, so transcription can be scored without real recordings.
