"""A stand-in for the ACE-Step 1.5 API server, for tests.

It answers the same endpoints with the same shapes as the real server (acestep/api/http
in ACE-Step-1.5): /health, /release_task (multipart), /query_result and /v1/audio. A
"cover" comes back as the source audio played `delay` seconds late and slightly
duller, so tests can check the app lines the AI band up with the original.
"""

import json
import socket
import tempfile
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

ANALYSIS = {
    "status_message": "Full Hardware Analysis Success",
    "bpm": 75,
    "keyscale": "C major",
    "timesignature": "6",
    "duration": 30,
    "genre": "worship",
    "prompt": "A heartfelt modern worship song with warm male vocals, acoustic guitar strumming, "
              "ambient electric guitar, piano, organ pads and live drums",
    "lyrics": "[Intro]\n\n[Verse 1]\nla la\n\n[Chorus]\nla la\n\n[Outro]",
    "language": "en",
}


def _wrap(data):
    return {"data": data, "code": 200, "error": None, "timestamp": int(time.time() * 1000), "extra": None}


class FakeAceStep:
    def __init__(self, delay: float = 0.25, fail: bool = False):
        self.delay = delay
        self.fail = fail
        self.requests: list[dict] = []
        self.tasks: dict[str, dict] = {}
        self.dir = Path(tempfile.mkdtemp(prefix="fake-acestep-"))
        self.app = self._build()
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/health")
        def health():
            return _wrap({"status": "ok"})

        @app.post("/release_task")
        async def release_task(request: Request):
            form = await request.form()
            fields = {k: v for k, v in form.items() if isinstance(v, str)}
            upload = form.get("src_audio")
            src = self.dir / f"src-{uuid.uuid4().hex}.wav"
            src.write_bytes(await upload.read())
            self.requests.append({**fields, "src_audio": src})
            task_id = uuid.uuid4().hex
            self.tasks[task_id] = {"fields": fields, "src": src, "polls": 0}
            return _wrap({"task_id": task_id, "status": "queued", "queue_position": 1})

        @app.post("/query_result")
        async def query_result(request: Request):
            body = await request.json()
            out = []
            for task_id in body["task_id_list"]:
                task = self.tasks[task_id]
                task["polls"] += 1
                if task["polls"] < 2:  # still running on the first poll
                    out.append({"task_id": task_id, "status": 0, "result": "[]"})
                elif self.fail:
                    out.append({"task_id": task_id, "status": 2, "result": json.dumps([{"error": "out of memory"}])})
                elif task["fields"].get("full_analysis_only") == "true":
                    out.append({"task_id": task_id, "status": 1, "result": json.dumps([ANALYSIS])})
                else:
                    path = self._cover(task)
                    item = {"file": f"/v1/audio?path={urllib.parse.quote(str(path))}", "wave": "", "status": 1,
                            "create_time": int(time.time()), "env": "development",
                            "prompt": task["fields"].get("prompt", ""), "lyrics": task["fields"].get("lyrics", ""),
                            "metas": {"bpm": task["fields"].get("bpm"), "duration": task["fields"].get("audio_duration"),
                                      "genres": "", "keyscale": task["fields"].get("key_scale", ""),
                                      "timesignature": task["fields"].get("time_signature", "")}}
                    out.append({"task_id": task_id, "status": 1, "result": json.dumps([item])})
            return _wrap(out)

        @app.get("/v1/audio")
        def get_audio(path: str):
            if not Path(path).is_relative_to(self.dir):
                return JSONResponse({"detail": "not found"}, status_code=404)
            return FileResponse(path)

        return app

    def _cover(self, task) -> Path:
        data, sr = sf.read(str(task["src"]), always_2d=True)
        # ACE-Step works at 48 kHz.
        n = int(len(data) * 48000 / sr)
        data = np.stack([np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data[:, c])
                         for c in range(data.shape[1])], axis=1)
        delayed = np.concatenate([np.zeros((int(self.delay * 48000), data.shape[1])), data])[:n]
        duller = np.convolve(delayed[:, 0], np.ones(4) / 4, mode="same")[:, None].repeat(2, axis=1)
        out = self.dir / f"cover-{uuid.uuid4().hex}.wav"
        sf.write(str(out), duller * 0.9, 48000)
        return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
