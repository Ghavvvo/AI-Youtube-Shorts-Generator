"""Upload de shorts a Zernio (stage 6 del pipeline local).

Cliente Zernio autocontenido: presign → PUT → create_post. Usa los knobs
ZERNIO_* de config.py (key, base URL, platform, account ids, publish_now).
"""
import os
from typing import Any, Dict, List

import requests

from ..config import (
    ZERNIO_ACCOUNT_INSTAGRAM,
    ZERNIO_ACCOUNT_YOUTUBE,
    ZERNIO_API_KEY,
    ZERNIO_BASE_URL,
    ZERNIO_PLATFORM,
    ZERNIO_PUBLISH_NOW,
)


class ZernioError(RuntimeError):
    pass


def _api_key() -> str:
    if not ZERNIO_API_KEY:
        raise ZernioError("ZERNIO_API_KEY is not set. Add it to your .env or export it.")
    return ZERNIO_API_KEY


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}


def _presign(filename: str, content_type: str, size: int) -> tuple:
    r = requests.post(
        f"{ZERNIO_BASE_URL}/media/presign",
        headers=_headers(),
        json={"filename": filename, "contentType": content_type, "size": size},
        timeout=60,
    )
    if r.status_code != 200:
        raise ZernioError(f"presign failed [{r.status_code}]: {r.text[:200]}")
    data = r.json()
    return data["uploadUrl"], data["publicUrl"]


def _upload_file(path: str, upload_url: str) -> None:
    with open(path, "rb") as f:
        r = requests.put(upload_url, data=f, headers={"Content-Type": "video/mp4"}, timeout=600)
    if r.status_code != 200:
        raise ZernioError(f"PUT media failed [{r.status_code}]: {r.text[:200]}")


def _create_post(content: str, media_url: str, platform: str, account_id: str,
                 title: str, publish_now: bool) -> Dict[str, Any]:
    platforms = [{"platform": platform, "accountId": account_id}]
    if platform == "youtube":
        platforms[0]["platformSpecificData"] = {"title": title}
    payload = {
        "content": content,
        "mediaItems": [{"url": media_url, "type": "video"}],
        "platforms": platforms,
        "publishNow": publish_now,
        "isDraft": not publish_now,
    }
    r = requests.post(f"{ZERNIO_BASE_URL}/posts", headers=_headers(), json=payload, timeout=120)
    if r.status_code not in (200, 201):
        raise ZernioError(f"create post failed [{r.status_code}]: {r.text[:300]}")
    return r.json()


def _account_for(platform: str) -> str:
    return ZERNIO_ACCOUNT_YOUTUBE if platform == "youtube" else ZERNIO_ACCOUNT_INSTAGRAM


def upload_short(clip_path: str, title: str, hook: str, platform: str = None,
                 publish_now: bool = None) -> Dict[str, Any]:
    """Sube y crea UN post en Zernio; devuelve la respuesta del post."""
    platform = platform or ZERNIO_PLATFORM
    publish_now = ZERNIO_PUBLISH_NOW if publish_now is None else publish_now
    account_id = _account_for(platform)
    if not account_id:
        raise ZernioError(f"No ZERNIO_ACCOUNT_{platform.upper()} set. Add it to your .env.")
    filename = os.path.basename(clip_path)
    up_url, pub_url = _presign(filename, "video/mp4", os.path.getsize(clip_path))
    _upload_file(clip_path, up_url)
    return _create_post(hook, pub_url, platform, account_id, title, publish_now)


def upload_shorts(shorts: List[Dict], platform: str = None, publish_now: bool = None,
                  resume: bool = False, out_dir: str = None) -> List[Dict]:
    """Sube cada short con clip_url; adjunta post_id o upload_error al dict.

    Con resume=True, no re-subir shorts ya subidos (post_id registrado en uploads.json).
    """
    from . import resume as _resume

    already = _resume.load_uploads(out_dir) if (resume and out_dir) else {}
    out = []
    for i, s in enumerate(shorts, 1):
        clip = s.get("clip_url")
        if not clip or not os.path.exists(clip):
            out.append(s)
            continue
        name = os.path.basename(clip)
        prev = already.get(name)
        if prev:
            print(f"[upload] resume: {name} ya subido (post {prev})", flush=True)
            out.append({**s, "post_id": prev})
            continue
        print(f"[upload] {i}/{len(shorts)}: {s.get('title', '(untitled)')}", flush=True)
        try:
            resp = upload_short(clip, s.get("title", ""), s.get("hook_sentence", ""),
                                platform=platform, publish_now=publish_now)
            post_id = resp.get("id", resp.get("postId", ""))
            if out_dir:
                _resume.save_upload(out_dir, name, str(post_id))
            out.append({**s, "post_id": post_id})
        except Exception as e:
            print(f"[upload] {i} failed: {e}", flush=True)
            out.append({**s, "upload_error": str(e)})
    return out
