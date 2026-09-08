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


def _clamp_highlight(h: Dict, words: List[Dict], max_secs: int) -> Dict:
    """Recorta un highlight a ≤ max_secs cortando en límite de palabra coherente.

    Prioriza el fin de frase (word terminada en .?!…) dentro de la ventana; si no
    existe, usa el último límite de palabra ≤ start+max. Así el clip inicia/cierra
    en un único tema con texto coherente, no a cuchillo.
    """
    start = float(h["start_time"])
    end = float(h["end_time"])
    if end - start <= max_secs:
        return h
    hard_end = start + max_secs
    in_win = [w for w in words if w["start"] >= start and w["end"] <= hard_end + 0.05]
    cut = hard_end
    if in_win:
        # preferir corte en fin de frase; si no, el último límite de palabra
        sentence_end = [
            w["end"] for w in in_win
            if (w.get("word") or "").rstrip()[-1:] in ".!?…"
        ]
        if sentence_end:
            cut = min(sentence_end[-1], hard_end)
        else:
            cut = min(in_win[-1]["end"], hard_end)
    return {**h, "start_time": start, "end_time": cut}


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
    subtitles_style: str = "hormozi",
    max_clip_secs: int = 60,
    subtitles_bg: bool = True,
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

    # recortar a duración máxima (max_clip_secs) cortando en límite de frase/palabra
    words = [
        {"start": float(wi["start"]), "end": float(wi["end"]), "word": wi.get("word", "")}
        for seg in transcript.get("segments", []) for wi in seg.get("words", [])
    ]
    if max_clip_secs and words:
        top = [_clamp_highlight(h, words, max_clip_secs) for h in top]
        print(f"[pipeline/local] duración máx {max_clip_secs}s aplicada", flush=True)

    # 4. crop
    emit("crop", 4, "Crop vertical...")
    shorts = crop_highlights_local(source_path, top, aspect_ratio=aspect_ratio,
                                   track=face_tracking, resume=resume)

    # 5. subtitles
    if subtitles:
        emit("subtitles", 5, "Quemando subtítulos...")
        from .local.subtitles import burn_subtitles_for_shorts
        shorts = burn_subtitles_for_shorts(shorts, transcript, resume=resume,
                                           style=subtitles_style, background=subtitles_bg)

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
    subtitles_style: str = "hormozi",
    max_clip_secs: int = 60,
    subtitles_bg: bool = True,
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
            subtitles_style=subtitles_style, max_clip_secs=max_clip_secs,
            subtitles_bg=subtitles_bg,
        )
    if mode == "api":
        return _run_api(youtube_url, num_clips, aspect_ratio, download_format, language)
    raise ValueError(f"Unknown mode: {mode!r}. Use 'api' or 'local'.")
