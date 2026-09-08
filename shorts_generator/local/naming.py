"""Nombrado automático de proyectos a partir del source.

Deriva un slug estable desde la entrada:
  - YouTube URL → video_id (`output/<video_id>`)
  - Archivo local / file:// → nombre del archivo sin extensión
Así la GUI y el CLI generan el mismo nombre para el mismo video.
"""
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Optional

from .downloader import _extract_youtube_video_id


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text)
    return text.strip("-").lower()


def project_name(source: str) -> str:
    """Devuelve el nombre de proyecto (slug) para el source dado."""
    video_id = _extract_youtube_video_id(source)
    if video_id:
        return video_id

    parsed = urlparse(source)
    if parsed.scheme == "file":
        raw = unquote(parsed.path)
        stem = Path(raw).stem
        return _slugify(stem) or "proyecto"

    # ruta local o nombre
    if parsed.scheme in ("http", "https"):
        # otro dominio que no es youtube → slug del host+path
        base = (parsed.netloc + parsed.path).strip("/")
        return _slugify(base) or "proyecto"

    candidate = Path(source)
    return _slugify(candidate.stem) or "proyecto"
