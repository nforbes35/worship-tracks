"""The web server: the team's song library and the play-along player, in one page."""

import hashlib
import hmac
import logging
import os
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .chords import Chart, parse_chart_text, read_chart
from .library import Library

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_CHART_BYTES = 10 * 1024 * 1024
CHART_TYPES = {".pdf", ".txt", ".cho", ".chopro", ".chordpro", ".pro"}
AUDIO_TYPES = {".m4a": "audio/mp4", ".mid": "audio/midi"}
WEB_DIR = Path(__file__).parent / "web"
COOKIE = "wt_session"


def _session_token(data_dir: Path, passcode: str) -> str:
    """Derived from the passcode and a secret kept on this Mac, so changing the passcode
    signs everyone out."""
    secret_file = data_dir / ".secret"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(32))
        secret_file.chmod(0o600)
    return hmac.new(secret_file.read_text().encode(), passcode.encode(), hashlib.sha256).hexdigest()


def create_app(
    data_dir: Path | None = None,
    soundfont: Path | None = None,
    passcode: str | None = None,
    start_worker: bool = True,
) -> FastAPI:
    """Settings default to the WT_* environment variables (see scripts/config.example)."""
    if data_dir is None:
        data_dir = Path(os.environ.get("WT_DATA_DIR", Path.home() / "WorshipTracks")).expanduser()
    if soundfont is None:
        soundfont = Path(os.environ.get("WT_SOUNDFONT", data_dir / "soundfonts" / "GeneralUser-GS.sf2")).expanduser()
    if passcode is None:
        # Optional: everyone enters this team passcode once per device.
        passcode = os.environ.get("WT_PASSCODE", "")
    passcode = passcode.strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    library = Library(data_dir, soundfont)
    token = _session_token(data_dir, passcode) if passcode else ""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_worker:
            if not soundfont.exists():
                log.warning("SoundFont not found at %s; run scripts/setup.sh", soundfont)
            library.start()
        yield

    app = FastAPI(title="Worship Tracks", lifespan=lifespan)

    @app.middleware("http")
    async def require_passcode(request: Request, call_next):
        path = request.url.path
        protected = passcode and path.startswith("/api/") and path not in ("/api/login", "/api/session")
        if protected and not hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
            return JSONResponse({"detail": "Enter the team passcode"}, status_code=401)
        return await call_next(request)

    class Login(BaseModel):
        passcode: str

    @app.get("/api/session")
    def session(request: Request):
        signed_in = not passcode or hmac.compare_digest(request.cookies.get(COOKIE, ""), token)
        return {"passcodeRequired": bool(passcode), "signedIn": signed_in}

    @app.post("/api/login")
    def login(body: Login):
        if not passcode or not hmac.compare_digest(body.passcode.strip(), passcode):
            raise HTTPException(401, "That passcode isn't right")
        response = JSONResponse({"signedIn": True})
        response.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
        return response

    @app.get("/api/songs")
    def list_songs():
        return library.list()

    async def _save_upload(file: UploadFile, limit: int, suffix: str = ".upload") -> Path:
        size = 0
        with tempfile.NamedTemporaryFile(delete=False, dir=library.songs_dir, suffix=suffix) as out:
            tmp = Path(out.name)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    out.close()
                    tmp.unlink(missing_ok=True)
                    raise HTTPException(413, f"That file is too big ({limit // (1024 * 1024)} MB max)")
                out.write(chunk)
        return tmp

    async def _read_chart(file: UploadFile | None, text: str) -> Chart | None:
        """A chart from an uploaded file (PDF, ChordPro, text) or typed-in text."""
        try:
            if file is not None and file.filename:
                suffix = Path(file.filename).suffix.lower()
                if suffix not in CHART_TYPES:
                    raise ValueError("Chord charts can be PDF, ChordPro or text files")
                path = await _save_upload(file, MAX_CHART_BYTES, suffix)
                try:
                    return read_chart(path)
                finally:
                    path.unlink(missing_ok=True)
            if text.strip():
                chart = parse_chart_text(text)
                if not chart.chords:
                    raise ValueError("Couldn't find any chords in that. Write them like: G  D  Em  C")
                return chart
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return None

    @app.post("/api/songs", status_code=201)
    async def upload(
        file: UploadFile = File(...),
        title: str = Form(""),
        chart: UploadFile | None = File(None),
        chart_text: str = Form(""),
    ):
        if not file.filename:
            raise HTTPException(400, "Choose a song file")
        parsed = await _read_chart(chart, chart_text)
        tmp = await _save_upload(file, MAX_UPLOAD_BYTES)
        return library.add(title, file.filename, tmp, parsed)

    def _song_or_404(song_id: str) -> dict:
        meta = library.get(song_id)
        if meta is None:
            raise HTTPException(404, "Song not found")
        return meta

    @app.get("/api/songs/{song_id}")
    def get_song(song_id: str):
        return _song_or_404(song_id)

    class Rename(BaseModel):
        title: str

    @app.patch("/api/songs/{song_id}")
    def rename(song_id: str, body: Rename):
        _song_or_404(song_id)
        return library.rename(song_id, body.title)

    @app.post("/api/songs/{song_id}/chart")
    async def set_chart(song_id: str, file: UploadFile | None = File(None), text: str = Form("")):
        _song_or_404(song_id)
        chart = await _read_chart(file, text)
        if chart is None:
            raise HTTPException(400, "Choose a chord chart or type the chords")
        return library.set_chart(song_id, chart)

    @app.delete("/api/songs/{song_id}/chart")
    def remove_chart(song_id: str):
        if _song_or_404(song_id).get("chart") is None:
            raise HTTPException(404, "This song has no chord chart")
        return library.set_chart(song_id, None)

    @app.post("/api/songs/{song_id}/retry")
    def retry(song_id: str):
        if _song_or_404(song_id)["status"] != "failed":
            raise HTTPException(409, "Only a song that failed can be retried")
        return library.retry(song_id)

    @app.delete("/api/songs/{song_id}", status_code=204)
    def delete(song_id: str):
        if not library.delete(song_id):
            raise HTTPException(404, "Song not found")

    @app.get("/api/songs/{song_id}/files/{relative:path}")
    def song_file(song_id: str, relative: str):
        path = library.file(song_id, relative)
        if path is None or path.suffix not in AUDIO_TYPES:
            raise HTTPException(404, "File not found")
        return FileResponse(path, media_type=AUDIO_TYPES[path.suffix],
                            headers={"Cache-Control": "private, max-age=3600"})

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
