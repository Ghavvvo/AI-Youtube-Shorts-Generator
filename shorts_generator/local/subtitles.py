"""Burn word-chunked yellow/bold subtitles onto each short (stage 5 del pipeline local).

Toma los segments de la transcripción, los ventanea al clip [start,end], los
trocea en bloques de N palabras en MAYÚSCULAS y los quema vía ASS + ffmpeg.
"""
import os
import subprocess
from typing import Dict, List, Tuple

from ..config import LOCAL_SUBTITLE_WORDS


def _format_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cc = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cc:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("{", "\\{").replace("}", "\\}")


def _probe_frame_size(path: str) -> Tuple[int, int]:
    import cv2  # type: ignore
    cap = cv2.VideoCapture(path)
    try:
        return (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    finally:
        cap.release()


def _window_segments(segments: List[Dict], start_time: float, end_time: float) -> List[Dict]:
    return [
        {
            "start": max(0.0, seg["start"] - start_time),
            "end": min(end_time, seg["end"]) - start_time,
            "text": seg["text"],
        }
        for seg in segments
        if seg["start"] < end_time and seg["end"] > start_time
    ]


def _chunk_words(segments: List[Dict], words_per_block: int) -> List[Dict]:
    blocks = []
    for seg in segments:
        words = seg["text"].split()
        chunks = [
            " ".join(words[k:k + words_per_block]).upper()
            for k in range(0, len(words), words_per_block)
        ]
        total = sum(len(c) for c in chunks)
        span = seg["end"] - seg["start"]
        cum = 0
        for chunk in chunks:
            start_s = seg["start"] + (span * cum / total if total else 0.0)
            cum += len(chunk)
            end_s = seg["start"] + (span * cum / total if total else 0.0)
            blocks.append({"start": start_s, "end": end_s, "text": chunk})
    return blocks


def _write_ass(blocks: List[Dict], ass_path: str, width: int, height: int) -> str:
    """Escribe bloques → ASS amarillo/bold/outlined; devuelve ass_path."""
    size = max(16, round(height * 0.062))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,DejaVu Sans,{size},&H0000FFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,2,20,20,40,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for b in blocks:
        lines.append(
            f"Dialogue: 0,{_ass_time(b['start'])},{_ass_time(b['end'])},"
            f"Default,,0,0,0,,{_escape_ass_text(b['text'])}"
        )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return ass_path


def _burn_ass(in_path: str, ass_path: str, out_path: str) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", in_path,
        "-vf", f"subtitles={ass_path}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "copy",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def burn_subtitles_for_short(
    clip_path: str,
    segments: List[Dict],
    start_time: float,
    end_time: float,
    words_per_block: int = None,
) -> str:
    """Quema subtítulos en un clip, reemplazando el archivo; devuelve el path."""
    words_per_block = words_per_block or LOCAL_SUBTITLE_WORDS
    windowed = _window_segments(segments, start_time, end_time)
    if not windowed:
        return clip_path
    blocks = _chunk_words(windowed, words_per_block)
    if not blocks:
        return clip_path

    width, height = _probe_frame_size(clip_path)
    ass_path = clip_path + ".ass"
    _write_ass(blocks, ass_path, width, height)
    burned = clip_path + ".sub.mp4"
    _burn_ass(clip_path, ass_path, burned)
    os.replace(burned, clip_path)
    return clip_path


def burn_subtitles_for_shorts(
    shorts: List[Dict],
    transcript: Dict,
    words_per_block: int = None,
    resume: bool = False,
) -> List[Dict]:
    """Quema subtítulos en cada short con clip_url; devuelve shorts actualizados.

    Con resume=True, salta los shorts ya subtitulados (marcador .sub).
    """
    from . import resume as _resume

    segments = transcript.get("segments", [])
    out = []
    for s in shorts:
        clip = s.get("clip_url")
        if not clip or not os.path.exists(clip):
            out.append(s)
            continue
        clip = os.path.abspath(clip)  # rutas relativas → absolutas para marcador/ffmpeg
        out_dir = os.path.dirname(clip)
        name = os.path.basename(clip)
        if resume and _resume.subtitled(out_dir, name):
            print(f"[subtitles] resume: reusing subtitles for {clip}", flush=True)
            out.append(s)
            continue
        print(f"[subtitles] {s.get('title', '(untitled)')}", flush=True)
        try:
            burn_subtitles_for_short(
                clip, segments,
                float(s["start_time"]), float(s["end_time"]),
                words_per_block=words_per_block,
            )
            _resume.mark_subtitled(out_dir, name)
            out.append(s)
        except Exception as e:
            print(f"[subtitles] failed for {clip}: {e}", flush=True)
            out.append({**s, "subtitles_error": str(e)})
    return out
