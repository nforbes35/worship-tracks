"""Talking to ACE-Step 1.5, the open-source music generator, running as its own local
server (see scripts/setup.sh). It analyses songs and plays new "cover" recordings of them.

API reference: https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/API.md
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8001"


class AceStepError(RuntimeError):
    pass


class AceStep:
    def __init__(self, url: str | None = None, timeout: float = 30 * 60):
        self.url = (url or os.environ.get("WT_ACESTEP_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout  # longest a job may take, in seconds

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=3) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def analyse(self, audio_path: Path) -> dict:
        """What ACE-Step hears in a song: caption (style and instruments), bpm, keyscale,
        timesignature, genre, and lyrics with section tags. Needs its language model."""
        task = self._submit({"task_type": "cover", "full_analysis_only": "true"}, audio_path)
        result = self._wait(task)
        items = result if isinstance(result, list) else [result]
        return items[0] if items else {}

    def cover(self, src: Path, dest: Path, *, caption: str, bpm: float | None, key: str | None,
              time_signature: str | None, duration: float, strength: float = 1.0, seed: int | None = None,
              on_wait: Callable[[float], None] | None = None) -> dict:
        """A new recording of `src` in the style of `caption`, keeping its structure,
        chords and timing. Saves it to `dest` (WAV) and returns its details."""
        fields = {
            "task_type": "cover",
            "prompt": caption,
            "lyrics": "[Instrumental]",
            "audio_duration": f"{duration:.2f}",
            "audio_cover_strength": f"{strength:.2f}",
            "batch_size": "1",
            "audio_format": "wav",
            "thinking": "false",
            "use_cot_caption": "false",
            "use_cot_language": "false",
            "use_format": "false",
            "use_random_seed": "false" if seed is not None else "true",
            "seed": str(seed if seed is not None else -1),
        }
        if bpm:
            fields["bpm"] = str(int(round(bpm)))
        if key:
            fields["key_scale"] = key
        if time_signature:
            fields["time_signature"] = time_signature
        task = self._submit(fields, src)
        items = self._wait(task, on_wait)
        files = [item.get("file") for item in items if isinstance(item, dict) and item.get("file")]
        if not files:
            raise AceStepError("ACE-Step finished but returned no audio")
        self._download(files[0], dest)
        return items[0]

    # --- HTTP ---

    def _submit(self, fields: dict[str, str], audio_path: Path) -> str:
        body, content_type = _multipart(fields, {"src_audio": audio_path})
        response = self._request("/release_task", body, content_type)
        data = response.get("data") or {}
        task_id = data.get("task_id")
        if not task_id:
            raise AceStepError(f"ACE-Step didn't accept the job: {response}")
        return task_id

    def _wait(self, task_id: str, on_wait: Callable[[float], None] | None = None):
        started = time.time()
        while True:
            response = self._request("/query_result", json.dumps({"task_id_list": [task_id]}).encode(),
                                     "application/json")
            items = response.get("data") or []
            item = items[0] if items else {}
            status = item.get("status", 0)
            if status == 1:
                result = item.get("result") or "[]"
                return json.loads(result) if isinstance(result, str) else result
            if status == 2:
                raise AceStepError(f"ACE-Step couldn't make it: {str(item.get('result'))[:300]}")
            elapsed = time.time() - started
            if elapsed > self.timeout:
                raise AceStepError("ACE-Step took too long")
            if on_wait:
                on_wait(elapsed)
            time.sleep(2)

    def _download(self, file_url: str, dest: Path) -> None:
        url = file_url if file_url.startswith("http") else f"{self.url}{file_url}"
        with urllib.request.urlopen(url, timeout=120) as response, open(dest, "wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)

    def _request(self, path: str, body: bytes, content_type: str) -> dict:
        request = urllib.request.Request(f"{self.url}{path}", data=body, method="POST",
                                         headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise AceStepError(f"ACE-Step said {exc.code}: {exc.read()[:300]!r}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AceStepError(f"Couldn't reach ACE-Step at {self.url}: {exc}") from exc


def _multipart(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, path in files.items():
        header = (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                  f'filename="{urllib.parse.quote(path.name)}"\r\nContent-Type: application/octet-stream\r\n\r\n')
        parts.append(header.encode() + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
