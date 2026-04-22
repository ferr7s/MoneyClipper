import json
import re
import subprocess
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

MODEL: Any | None = None
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "com", "da", "das", "de", "do", "dos",
    "e", "em", "for", "how", "in", "is", "isso", "it", "mais", "na", "no", "nos", "o", "of",
    "on", "or", "os", "para", "por", "que", "se", "sem", "the", "to", "um", "uma", "você",
}


def get_model():
    global MODEL
    if MODEL is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed correctly. Check the project dependencies."
            ) from exc
        MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    return MODEL


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise RuntimeError(message or "Command failed")


def ffprobe_duration(video_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def download_video(url: str, target_dir: Path) -> tuple[Path, str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(target_dir / "source.%(ext)s")
    options = {
        "format": "mp4/bestvideo+bestaudio/best",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = Path(ydl.prepare_filename(info))
        final_path = downloaded.with_suffix(".mp4") if downloaded.with_suffix(".mp4").exists() else downloaded
        return final_path, info.get("title") or downloaded.stem


def transcribe_to_sentences(video_path: Path, subtitle_path: Path) -> tuple[list[dict], str]:
    model = get_model()
    segments, _ = model.transcribe(
        str(video_path),
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    sentence_segments = []

    def flush_segment(text: str, start: float, end: float) -> None:
        clean_text = text.strip()
        if clean_text:
            sentence_segments.append(
                {"start": round(start, 2), "end": round(end, 2), "text": clean_text}
            )

    for segment in segments:
        current_text = ""
        current_start = None
        current_end = None
        words = list(segment.words or [])
        if not words:
            flush_segment(segment.text.strip(), segment.start, segment.end)
            continue

        for index, word in enumerate(words):
            if current_start is None:
                current_start = word.start
            current_end = word.end
            current_text += word.word
            if re.search(r"[.!?…]$", word.word.strip()):
                flush_segment(current_text, current_start, current_end)
                current_text = ""
                current_start = None
                current_end = None
            elif index == len(words) - 1:
                flush_segment(current_text, current_start, current_end)

    write_srt(sentence_segments, subtitle_path, 0.0)
    transcript = " ".join(item["text"] for item in sentence_segments).strip()
    return sentence_segments, transcript


def format_timestamp(seconds: float) -> str:
    total_ms = int(max(seconds, 0) * 1000)
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_srt(segments: list[dict], output_path: Path, shift_seconds: float) -> None:
    lines = []
    for index, segment in enumerate(segments, start=1):
        start = max(segment["start"] - shift_seconds, 0)
        end = max(segment["end"] - shift_seconds, 0)
        lines.append(
            "\n".join(
                [
                    str(index),
                    f"{format_timestamp(start)} --> {format_timestamp(end)}",
                    segment["text"].strip(),
                ]
            )
        )
    output_path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def select_highlights(segments: list[dict], video_duration: float, max_clips: int = 3) -> list[dict]:
    if not segments:
        end = min(video_duration or 30.0, 30.0)
        return [{"start": 0.0, "end": max(end, 10.0), "score": 1.0, "excerpt": ""}]

    if video_duration and video_duration <= 35:
        excerpt = " ".join(item["text"] for item in segments[:5]).strip()
        return [{"start": 0.0, "end": video_duration, "score": 1.0, "excerpt": excerpt}]

    candidates = []
    for index, segment in enumerate(segments):
        start = max(segment["start"] - 0.5, 0.0)
        end = start
        text_parts = []
        words = []
        bonus = 0.0
        for inner in segments[index:]:
            if inner["end"] - start > 32:
                break
            end = inner["end"] + 0.5
            text_parts.append(inner["text"])
            segment_words = re.findall(r"[A-Za-zÀ-ÿ0-9]+", inner["text"].lower())
            words.extend(segment_words)
            if "?" in inner["text"] or "!" in inner["text"]:
                bonus += 2.5
            if re.search(r"\d", inner["text"]):
                bonus += 1.0

        duration = end - start
        if duration < 15:
            continue
        unique_words = {word for word in words if len(word) > 3}
        score = len(words) + (len(unique_words) * 0.7) + bonus
        candidates.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "score": round(score, 2),
                "excerpt": " ".join(text_parts[:4]).strip(),
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    selected = []
    for candidate in candidates:
        overlaps = False
        for existing in selected:
            if candidate["start"] < existing["end"] and candidate["end"] > existing["start"]:
                overlaps = True
                break
        if overlaps:
            continue
        selected.append(candidate)
        if len(selected) >= max_clips:
            break

    if not selected:
        fallback_end = min(video_duration or 30.0, 30.0)
        return [{"start": 0.0, "end": max(fallback_end, 10.0), "score": 1.0, "excerpt": segments[0]["text"]}]

    return sorted(selected, key=lambda item: item["start"])


def filter_segments_for_clip(segments: list[dict], start: float, end: float) -> list[dict]:
    clip_segments = []
    for segment in segments:
        if segment["end"] <= start or segment["start"] >= end:
            continue
        clip_segments.append(
            {
                "start": max(segment["start"], start),
                "end": min(segment["end"], end),
                "text": segment["text"],
            }
        )
    return clip_segments


def extract_keywords(text: str, limit: int = 5) -> list[str]:
    counts = {}
    for token in re.findall(r"[A-Za-zÀ-ÿ0-9]+", text.lower()):
        if len(token) < 4 or token in STOPWORDS:
            continue
        counts[token] = counts.get(token, 0) + 1
    keywords = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in keywords[:limit]]


def sentence_chunks(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def build_copy(source_title: str, excerpt: str, full_transcript: str) -> dict:
    keywords = extract_keywords(f"{source_title} {excerpt} {full_transcript}")
    base_title = source_title.strip() or (sentence_chunks(excerpt)[:1] or ["Clip"])[0]
    base_title = re.sub(r"\s+", " ", base_title).strip(" -")
    if len(base_title) > 70:
        base_title = base_title[:67].rstrip() + "..."
    if keywords:
        title = f"{base_title} | {keywords[0].title()}"
    else:
        title = base_title or "Clip curto"

    caption_sentences = sentence_chunks(excerpt or full_transcript)[:2]
    caption = " ".join(caption_sentences).strip()
    if len(caption) > 180:
        caption = caption[:177].rstrip() + "..."

    hashtags = [f"#{word.replace('-', '')}" for word in keywords[:4]]
    if not hashtags:
        hashtags = ["#shorts", "#clip", "#video"]

    return {
        "title": title,
        "caption": caption or title,
        "hashtags": " ".join(hashtags),
    }


def render_clip(video_path: Path, subtitle_path: Path, output_path: Path, start: float, end: float) -> None:
    duration = max(end - start, 1.0)
    subtitle_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"subtitles={str(subtitle_path)}:"
        "force_style='FontName=DejaVu Sans,FontSize=18,PrimaryColour=&HFFFFFF&,"
        "OutlineColour=&H000000&,BorderStyle=3,Outline=1,Shadow=0,MarginV=80,Alignment=2'"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(video_path),
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
