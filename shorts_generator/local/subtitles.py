r"""Burn word-timed animated subtitles onto each short (stage 5 del pipeline local).

Puerto del enfoque de ai-video-captions (MIT, autoshorts):
  - word-level timestamps reales (words[] de faster-whisper)
  - ASS generado con pysubs2: agrupa palabras por caracteres, un evento por
    palabra con la línea completa y tags de animación en la palabra activa
    (highlight \c, karaoke \kf, scale \fscx, bounce \t) — libass hace el
    layout natural (sin \pos manual → sin scatter)
  - 6 estilos: hormozi / mrbeast / karaoke / minimal / bounce / classic
  - quema con ffmpeg subtitles filter
"""

from typing import Dict, List

from .caption_styles import get_style

MAX_CHARS_PER_LINE = 18
POSITION_PCT = 19  # % desde abajo — punto medio entre el borde inferior y la posición anterior (38%)


def _escape_ass_text(text: str) -> str:
    return text.replace("{", "\\{").replace("}", "\\}")


def _wrap_words(words: List[Dict]) -> List[tuple]:
    """Agrupa palabras (con su timing relativo al clip) en subtítulos de ≤N chars."""
    subtitles = []
    current: List[tuple] = []
    current_chars = 0
    cur_start = 0.0
    cur_end = 0.0
    for w in words:
        start = float(w["start"])
        end = float(w["end"])
        text = _escape_ass_text(w["word"].upper())
        if not current:
            current = [(text, start, end)]
            current_chars = len(w["word"])
            cur_start = start
            cur_end = end
            continue
        if current_chars + 1 + len(w["word"]) <= MAX_CHARS_PER_LINE:
            current.append((text, start, end))
            current_chars += 1 + len(w["word"])
            cur_end = end
        else:
            subtitles.append((cur_start, cur_end, current))
            current = [(text, start, end)]
            current_chars = len(w["word"])
            cur_start = start
            cur_end = end
    if current:
        subtitles.append((cur_start, cur_end, current))
    return subtitles


def _active_word_tag(word: str, start: float, end: float, style: dict) -> str:
    """Aplica el tag de la palabra activa según la animación del estilo.

    Todos los estilos crecen (\fscx) y funden el highlight con \t (transición
    suave de color) — la palabra no "salta", crece y se ilumina progresivamente.
    """
    hl = style["highlight"]
    anim = style.get("animation", "highlight")
    if anim == "karaoke":
        dur_cs = max(10, int((end - start) * 100)) if end > start else 30
        return f"{{\\kf{dur_cs}\\c{hl}}}{word}{{\\r}}"
    if anim == "bounce":
        return (
            f"{{\\t(0,50,\\fscx120\\fscy120)\\t(50,100,\\fscx100\\fscy100)"
            f"\\c{hl}}}{word}{{\\r}}"
        )
    # scale y highlight (default): relleno SOLIDO del color highlight (letras de
    # color fijo) + crecimiento animado. El glow interior va en capa aparte.
    return f"{{\\fscx115\\fscy115\\1c{hl}\\t(0,120,\\fscx100\\fscy100)}}{word}{{\\r}}"


def _build_event_text(words: List[tuple], active_idx: int, style: dict) -> str:
    """Línea completa; la palabra activa con tag de animación, el resto blancas."""
    parts = []
    for i, (word, start, end) in enumerate(words):
        if i == active_idx:
            parts.append(_active_word_tag(word, start, end, style))
        else:
            parts.append(word)
    return " ".join(parts)


def _build_glow_event_text(words: List[tuple], active_idx: int, style: dict,
                           background: bool = True) -> str:
    """Copia de la línea SOLO para el halo interior: la palabra activa se dibuja
    rellena del highlight con blur ajustado al glifo (la luz emana de dentro).
    El resto completamente transparente (conserva el ancho para centrado).

    Con background=False (sin outline negro) la silueta se reduce mucho para
    no quemar las letras vecinas.
    """
    hl = style["highlight"]
    if background:
        bord_blur = "\\bord3\\blur3.5"
    else:
        bord_blur = "\\bord0.8\\blur1.2"
    parts = []
    for i, (word, _start, _end) in enumerate(words):
        if i == active_idx:
            parts.append(f"{{\\1c{hl}\\3c{hl}{bord_blur}\\shad0}}{word}")
        else:
            parts.append(f"{{\\1a&HFF&\\bord0\\shad0}}{word}")
    return " ".join(parts)


def generate_ass(
    transcript: Dict,
    clip_start: float,
    clip_end: float,
    out_path: str,
    width: int,
    height: int,
    style_id: str = "hormozi",
    background: bool = True,
) -> bool:
    """Genera el .ass animado para un clip. False si no hay palabras en el rango."""
    import pysubs2

    style = get_style(style_id)

    # palabras del transcript dentro del rango del clip (relativas al clip)
    words = []
    for seg in transcript.get("segments", []):
        for wi in seg.get("words", []):
            ws = float(wi.get("start"))
            we = float(wi.get("end"))
            if we > clip_start and ws < clip_end:
                words.append({
                    "word": wi.get("word", "").strip(),
                    "start": max(0.0, ws - clip_start),
                    "end": max(0.0, we - clip_start),
                })
    words = [w for w in words if w["word"]]
    if not words:
        return False

    groups = _wrap_words(words)

    subs = pysubs2.SSAFile()
    subs.info["WrapStyle"] = 3
    subs.info["PlayResX"] = width
    subs.info["PlayResY"] = height

    s = pysubs2.SSAStyle()
    s.fontname = style["font"]
    s.fontsize = max(24, round(height * style.get("font_size", 0.055)))
    s.primarycolor = pysubs2.Color(255, 255, 255, 0)  # blanco opaco (primary en ASS-BGR)
    s.bold = bool(style.get("bold", True))
    s.italic = bool(style.get("italic", False))
    s.outline = style.get("outline_size", 4.0) if background else 0.0
    s.outlinecolor = pysubs2.Color(0, 0, 0, 0)
    s.shadow = style.get("shadow_depth", 3.0) if background else 0.0
    s.shadowcolor = pysubs2.Color(0, 0, 0, int(style.get("shadow_alpha", 128)))
    s.alignment = pysubs2.Alignment.BOTTOM_CENTER
    s.marginv = int(height * POSITION_PCT / 100)
    subs.styles["Default"] = s

    for start, end, group in groups:
        for i in range(len(group)):
            event_start = group[i][1]
            event_end = group[i + 1][1] if i + 1 < len(group) else end
            # capa glow (detrás): solo con background activo (sin bg = sin glow)
            if background and style.get("animation", "highlight") not in ("karaoke", "bounce"):
                subs.events.append(pysubs2.SSAEvent(
                    layer=0,
                    start=pysubs2.make_time(s=event_start),
                    end=pysubs2.make_time(s=event_end),
                    text=_build_glow_event_text(group, i, style, background),
                    style="Default",
                ))
            # texto principal (delante)
            subs.events.append(pysubs2.SSAEvent(
                layer=1,
                start=pysubs2.make_time(s=event_start),
                end=pysubs2.make_time(s=event_end),
                text=_build_event_text(group, i, style),
                style="Default",
            ))

    subs.save(out_path)
    return True


def _burn_ass(in_path: str, ass_path: str, out_path: str) -> None:
    import subprocess
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
    transcript: Dict,
    start_time: float,
    end_time: float,
    words_per_block: int = None,
    style: str = "hormozi",
    background: bool = True,
) -> str:
    """Quema subtítulos en un clip, reemplaza el archivo; devuelve el path."""
    import os
    import cv2  # type: ignore

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {clip_path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    ass_path = clip_path + ".ass"
    ok = generate_ass(transcript, start_time, end_time, ass_path, width, height,
                      style, background=background)
    if not ok:
        return clip_path  # sin palabras en rango → sin subtítulos

    burned = clip_path + ".sub.mp4"
    _burn_ass(clip_path, ass_path, burned)
    os.replace(burned, clip_path)
    return clip_path


def burn_subtitles_for_shorts(
    shorts: List[Dict],
    transcript: Dict,
    words_per_block: int = None,
    resume: bool = False,
    style: str = "hormozi",
    background: bool = True,
) -> List[Dict]:
    """Quema subtítulos en cada short con clip_url; devuelve shorts actualizados."""
    import os
    from . import resume as _resume

    out = []
    for s in shorts:
        clip = s.get("clip_url")
        if not clip or not os.path.exists(clip):
            out.append(s)
            continue
        clip = os.path.abspath(clip)
        out_dir = os.path.dirname(clip)
        name = os.path.basename(clip)
        if resume and _resume.subtitled(out_dir, name):
            print(f"[subtitles] resume: reusing subtitles for {clip}", flush=True)
            out.append(s)
            continue
        print(f"[subtitles] {s.get('title', '(untitled)')}", flush=True)
        try:
            burn_subtitles_for_short(
                clip, transcript,
                float(s["start_time"]), float(s["end_time"]),
                words_per_block=words_per_block,
                style=style,
                background=background,
            )
            _resume.mark_subtitled(out_dir, name)
            out.append(s)
        except Exception as e:
            print(f"[subtitles] failed for {clip}: {e}", flush=True)
            out.append({**s, "subtitles_error": str(e)})
    return out