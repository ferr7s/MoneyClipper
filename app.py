import json
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from processor import (
    build_copy,
    download_video,
    ffprobe_duration,
    filter_segments_for_clip,
    render_clip,
    select_highlights,
    transcribe_to_sentences,
    write_json,
    write_srt,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
JOBS_DIR = DATA_DIR / "jobs"
TEMPLATES_DIR = BASE_DIR / "templates"
DB_PATH = DATA_DIR / "jobs.db"

for path in [DATA_DIR, UPLOADS_DIR, JOBS_DIR]:
    path.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Short Clips MVP")
app.mount("/assets", StaticFiles(directory=DATA_DIR), name="assets")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_value TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT,
                caption TEXT,
                hashtags TEXT,
                clips_json TEXT,
                error TEXT
            )
            """
        )
        conn.commit()


def serialize_job(row: sqlite3.Row) -> dict:
    job = dict(row)
    job["clips"] = json.loads(job.pop("clips_json") or "[]")
    return job


def list_jobs() -> list[dict]:
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC"
        ).fetchall()
    return [serialize_job(row) for row in rows]


def create_job(source_type: str, source_value: str) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, source_type, source_value, status, created_at, updated_at, clips_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, source_type, source_value, "received", now, now, "[]"),
        )
        conn.commit()
    return job_id


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.utcnow().isoformat()
    columns = ", ".join(f"{key} = ?" for key in fields.keys())
    values = list(fields.values()) + [job_id]
    with db_connection() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", values)
        conn.commit()


def process_job(job_id: str, source_type: str, source_value: str) -> None:
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        update_job(job_id, status="processing", error=None)

        if source_type == "upload":
            source_path = Path(source_value)
            video_path = job_dir / f"source{source_path.suffix.lower() or '.mp4'}"
            shutil.copy2(source_path, video_path)
            source_title = source_path.stem
        else:
            video_path, source_title = download_video(source_value, job_dir)

        subtitles_path = job_dir / "transcript.srt"
        segments, transcript = transcribe_to_sentences(video_path, subtitles_path)
        highlights = select_highlights(segments, ffprobe_duration(video_path))

        clips = []
        primary_copy = build_copy(source_title, transcript[:180], transcript)
        for index, highlight in enumerate(highlights, start=1):
            clip_segments = filter_segments_for_clip(
                segments, highlight["start"], highlight["end"]
            )
            clip_subtitle_path = job_dir / f"clip_{index}.srt"
            clip_output_path = job_dir / f"clip_{index}.mp4"
            write_srt(clip_segments, clip_subtitle_path, highlight["start"])
            render_clip(
                video_path,
                clip_subtitle_path,
                clip_output_path,
                highlight["start"],
                highlight["end"],
            )
            clip_excerpt = " ".join(item["text"] for item in clip_segments[:4]).strip()
            clip_copy = build_copy(source_title, clip_excerpt, transcript)
            clips.append(
                {
                    "index": index,
                    "start": highlight["start"],
                    "end": highlight["end"],
                    "url": f"/media/{job_id}/{clip_output_path.name}",
                    "title": clip_copy["title"],
                    "caption": clip_copy["caption"],
                    "hashtags": clip_copy["hashtags"],
                }
            )

        write_json(
            job_dir / "result.json",
            {
                "job_id": job_id,
                "source_type": source_type,
                "source_value": source_value,
                "transcript": transcript,
                "clips": clips,
            },
        )
        update_job(
            job_id,
            status="ready",
            title=primary_copy["title"],
            caption=primary_copy["caption"],
            hashtags=primary_copy["hashtags"],
            clips_json=json.dumps(clips, ensure_ascii=False),
        )
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc))


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"jobs": list_jobs()},
    )


@app.post("/jobs")
async def create_job_route(
    video_url: str = Form(default=""),
    video_file: UploadFile | None = File(default=None),
):
    if not video_url and not video_file:
        raise HTTPException(status_code=400, detail="Envie um arquivo ou um link.")

    if video_file and video_file.filename:
        upload_path = UPLOADS_DIR / f"{uuid.uuid4()}-{Path(video_file.filename).name}"
        with upload_path.open("wb") as buffer:
            shutil.copyfileobj(video_file.file, buffer)
        job_id = create_job("upload", str(upload_path))
        thread = threading.Thread(
            target=process_job, args=(job_id, "upload", str(upload_path)), daemon=True
        )
    else:
        job_id = create_job("link", video_url)
        thread = threading.Thread(
            target=process_job, args=(job_id, "link", video_url), daemon=True
        )

    thread.start()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/jobs")
def api_jobs():
    return {"jobs": list_jobs()}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/media/{job_id}/{filename}")
def media(job_id: str, filename: str):
    file_path = JOBS_DIR / job_id / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    return FileResponse(file_path)
