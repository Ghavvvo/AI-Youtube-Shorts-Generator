"""End-to-end orchestrator.

Two modes:
  * mode="api"   (default) — MuAPI does download / transcribe / LLM / autocrop.
                              Fast, no local deps, pay-per-call.
  * mode="local"            — yt-dlp + faster-whisper + OpenAI or Gemini + ffmpeg/opencv.
                              Self-hosted, LLM_PROVIDER selects OpenAI or Gemini.
"""
import os
from typing import Callable, Dict, List, Optional

from .clipper import crop_highlights
from .downloader import download_youtube
from .highlights import call_muapi_llm, get_highlights
from .transcriber import transcribe

STAGE_NAMES = ("download", "transcribe", "rank", "crop", "subtitles", "upload")


def _run_local(
    youtube_url: str,
    num_clips: int,
    aspect_ratio: str,
    download_format: str,
    language: Optional[str],
    subtitles: bool = True,
    face_tracking: bool = False,
    upload: bool = False,
    resume: bool = False,
    on_progress: Optional[Callable[[str, int, int, str], None]] = None,
    out_dir: Optional[str] = None,
) -> Dict:
    from .local.clipper import crop_highlights_local
    from .local.downloader import download_youtube_local
    from .local.llm import call_local_llm
    from .local.transcriber import transcribe_local

    out_dir = out_dir or os.getenv("LOCAL_OUTPUT_DIR", "output")
    os.environ["LOCAL_OUTPUT_DIR"] = out_dir  # stages cachean en el mismo sitio

    total = len(STAGE_NAMES)

    def emit(stage: str, done: int, msg: str = "") -> None:
        if on_progress:
            on_progress(stage, done, total, msg)

    # 1. download (cacheado por yt-dlp)
    emit("download", 1, "Descargando video...")
    source_path = download_youtube_local(youtube_url, fmt=download_format)

    # 2. transcribe (cacheado a .srt)
    emit("transcribe", 2, "Transcribiendo...")
    transcript = transcribe_local(source_path, language=language)
    if not transcript["segments"]:
        raise RuntimeError(
            "Whisper produced no segments. The video may have no detectable speech."
        )

    # 3. rank (resume reusa highlights.json)
    emit("rank", 3, "Ranking highlights...")
    from .local import resume

    all_highlights: List[Dict] = resume.load_highlights(out_dir) if resume else []
    if not all_highlights:
        highlights_result = get_highlights(transcript, num_clips=num_clips, llm_fn=call_local_llm)
        all_highlights = highlights_result.get("highlights", [])
        if not all_highlights:
            raise RuntimeError("Highlight generator returned zero clips.")
        if resume:
            resume.save_highlights(out_dir, all_highlights)

    top = sorted(all_highlights, key=lambda h: int(h.get("score", 0)), reverse=True)[:num_clips]
    print(f"[pipeline/local] cropping {len(top)} of {len(all_highlights)} candidates", flush=True)

    # 4. crop
    emit("crop", 4, "Crop vertical...")
    shorts = crop_highlights_local(source_path, top, aspect_ratio=aspect_ratio,
                                   track=face_tracking, resume=resume)

    # 5. subtitles
    if subtitles:
        emit("subtitles", 5, "Quemando subtítulos...")
        from .local.subtitles import burn_subtitles_for_shorts
        shorts = burn_subtitles_for_shorts(shorts, transcript, resume=resume)

    # 6. upload
    if upload:
        emit("upload", 6, "Subiendo a Zernio...")
        from .local.uploader import upload_shorts
        shorts = upload_shorts(shorts, resume=resume, out_dir=out_dir)

    emit("done", total, "Terminado")
    return {
        "mode": "local",
        "source_video_url": source_path,
        "transcript": transcript,
        "highlights": all_highlights,
        "shorts": shorts,
    }


def _run_api(
    youtube_url: str,
    num_clips: int,
    aspect_ratio: str,
    download_format: str,
    language: Optional[str],
) -> Dict:
    source_url = download_youtube(youtube_url, fmt=download_format)

    transcript = transcribe(source_url, language=language)
    if not transcript["segments"]:
        raise RuntimeError(
            "Whisper produced no segments. The video may have no detectable speech."
        )

    highlights_result = get_highlights(transcript, num_clips=num_clips, llm_fn=call_muapi_llm)
    all_highlights: List[Dict] = highlights_result.get("highlights", [])
    if not all_highlights:
        raise RuntimeError("Highlight generator returned zero clips.")

    top = sorted(all_highlights, key=lambda h: int(h.get("score", 0)), reverse=True)[:num_clips]
    print(f"[pipeline] cropping {len(top)} of {len(all_highlights)} candidates", flush=True)

    shorts = crop_highlights(source_url, top, aspect_ratio=aspect_ratio)

    return {
        "mode": "api",
        "source_video_url": source_url,
        "transcript": transcript,
        "highlights": all_highlights,
        "shorts": shorts,
    }


def generate_shorts(
    youtube_url: str,
    num_clips: int = 3,
    aspect_ratio: str = "9:16",
    download_format: str = "720",
    language: Optional[str] = None,
    mode: str = "api",
    subtitles: bool = True,
    face_tracking: bool = False,
    upload: bool = False,
    resume: bool = False,
    on_progress: Optional[Callable[[str, int, int, str], None]] = None,
    out_dir: Optional[str] = None,
) -> Dict:
    """Run the full pipeline and return a structured result.

    Args:
        youtube_url: source URL.
        num_clips: how many shorts to render.
        aspect_ratio: e.g. "9:16", "1:1".
        download_format: source resolution ("360" / "480" / "720" / "1080").
        language: ISO-639-1 to force Whisper language detection.
        mode: "api" (default, MuAPI) or "local" (yt-dlp + faster-whisper +
            OpenAI or Gemini + ffmpeg).
        subtitles: (local) burn word-chunked subtitles onto shorts.
        face_tracking: (local) track faces when reframing vertical.
        upload: (local) upload finished shorts to Zernio.
        resume: (local) reanudar desde la última etapa cacheada.
        on_progress: (local) callback(stage, done, total, message) por etapa.
        out_dir: (local) directorio de artefactos (default LOCAL_OUTPUT_DIR).

    Returns:
        {
          "mode": "api" | "local",
          "source_video_url": str,   # hosted URL (api) or local path (local)
          "transcript": {...},
          "highlights": [...],       # all candidates ranked
          "shorts": [...],           # top `num_clips` with clip_url / local path
        }
    """
    mode = (mode or "api").lower()
    if mode == "local":
        return _run_local(
            youtube_url, num_clips, aspect_ratio, download_format, language,
            subtitles=subtitles, face_tracking=face_tracking, upload=upload,
            resume=resume, on_progress=on_progress, out_dir=out_dir,
        )
    if mode == "api":
        return _run_api(youtube_url, num_clips, aspect_ratio, download_format, language)
    raise ValueError(f"Unknown mode: {mode!r}. Use 'api' or 'local'.")
