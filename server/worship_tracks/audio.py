"""Reading, writing and encoding audio with ffmpeg and FluidSynth."""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 44100


def require_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "fluidsynth") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(
            f"Missing {', '.join(missing)}. Run scripts/setup.sh (it installs them with Homebrew)."
        )


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {result.stderr.strip()[-800:]}")


def decode(src: Path, dest_wav: Path) -> float:
    """Convert any audio file ffmpeg understands to 44.1 kHz stereo WAV. Returns its length in seconds."""
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-vn",
          "-ac", "2", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dest_wav)])
    return sf.info(str(dest_wav)).duration


def read(path: Path) -> np.ndarray:
    """Audio as float32, shaped (channels, samples)."""
    data, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return data.T


def write(path: Path, audio: np.ndarray) -> None:
    sf.write(str(path), np.atleast_2d(audio).T, SAMPLE_RATE, subtype="PCM_16")


def encode_m4a(src_wav: Path, dest: Path, duration: float) -> None:
    """Mono AAC, padded or trimmed to the song's exact length so every track lines up."""
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src_wav), "-ac", "1",
          "-af", "apad", "-t", f"{duration:.3f}", "-c:a", "aac", "-b:a", "128k",
          "-movflags", "+faststart", str(dest)])


def render_midi(midi_path: Path, soundfont: Path, dest_wav: Path) -> None:
    _run(["fluidsynth", "-ni", "-q", "-g", "0.8", "-r", str(SAMPLE_RATE),
          "-F", str(dest_wav), str(soundfont), str(midi_path)])


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if audio.size else 0.0


def match_loudness(audio: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Scale a recreated track to sit at the level its instrument had in the original mix."""
    current = rms(audio)
    if current < 1e-9:
        return audio
    gain = np.clip(rms(reference) / current, 0.05, 20.0)
    out = audio * gain
    peak = np.abs(out).max()
    return out / peak * 0.98 if peak > 0.98 else out
