"""Caché por etapa para resume del pipeline local.

Resume determinista por ARTEFACTO: cada etapa guarda/lee ficheros en out_dir.
Si el artefacto existe, la etapa se considera hecha y se omite en un reintento.
Así el resume sobrevive a crashes intermedios sin confiar en run.progress.
"""
import json
import os

_HIGHLIGHTS = "highlights.json"
_UPLOADS = "uploads.json"
_SUB_MARKER = ".sub"


# ---------- rank (highlights) ----------
def highlights_path(out_dir: str) -> str:
    return os.path.join(out_dir, _HIGHLIGHTS)


def load_highlights(out_dir: str) -> list:
    p = highlights_path(out_dir)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f).get("highlights", [])
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_highlights(out_dir: str, highlights: list) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(highlights_path(out_dir), "w", encoding="utf-8") as f:
        json.dump({"highlights": highlights}, f, ensure_ascii=False)


# ---------- crop ----------
def short_clip_path(out_dir: str, idx: int) -> str:
    return os.path.join(out_dir, f"short_{idx:02d}.mp4")


def short_exists(out_dir: str, idx: int) -> bool:
    return os.path.exists(short_clip_path(out_dir, idx))


# ---------- subtitles ----------
def subtitled(out_dir: str, basename: str) -> bool:
    return os.path.exists(os.path.join(out_dir, basename + _SUB_MARKER))


def mark_subtitled(out_dir: str, basename: str) -> None:
    open(os.path.join(out_dir, basename + _SUB_MARKER), "a").close()


# ---------- upload (idempotente) ----------
def load_uploads(out_dir: str) -> dict:
    p = os.path.join(out_dir, _UPLOADS)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_upload(out_dir: str, basename: str, post_id: str) -> None:
    uploads = load_uploads(out_dir)
    uploads[basename] = post_id
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, _UPLOADS), "w", encoding="utf-8") as f:
        json.dump(uploads, f, ensure_ascii=False)


# ---------- reinicio desde cero ----------
def clear_build(out_dir: str) -> None:
    """Borra artefactos de build; conserva source/ y .srt (caros de rehacer)."""
    if not os.path.isdir(out_dir):
        return
    for name in os.listdir(out_dir):
        full = os.path.join(out_dir, name)
        if name.startswith("short_") or name.endswith(_SUB_MARKER) or name.endswith(".ass"):
            if os.path.isfile(full):
                os.remove(full)
        elif name in (_HIGHLIGHTS, _UPLOADS):
            if os.path.isfile(full):
                os.remove(full)
