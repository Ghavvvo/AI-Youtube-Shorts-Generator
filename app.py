"""Shorts Studio — GUI Streamlit minimalista sobre el pipeline local.

Detached por diseño: cada generación corre en un subproceso que sobrevive a
reruns/refreses. El estado vive en disco (output/<proyecto>/{run.log,run.pid,
run.rc,run.progress}), no en session_state, así que recargar no rompe nada.
"""
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

REPO = Path(__file__).resolve().parent
OUTPUT_BASE = REPO / "output"

STAGES = ["download", "transcribe", "rank", "crop", "subtitles", "upload"]


# ---------- estado de un run (disco) ----------
def project_dir(name: str) -> Path:
    return OUTPUT_BASE / name


def proc_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except (FileNotFoundError, IndexError):
        return False


def read_progress(name: str) -> dict:
    p = project_dir(name) / "run.progress"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def run_is_active(name: str) -> bool:
    """True si hay un proceso detached vivo para el proyecto."""
    pid_file = project_dir(name) / "run.pid"
    if not pid_file.exists():
        return False
    try:
        return proc_alive(int(pid_file.read_text().strip()))
    except (ValueError, OSError):
        return False


def launch(name: str, source: str, params: dict, resume: bool) -> None:
    """Lanza main.py detached; borra estado previo si no es resume."""
    out_dir = project_dir(name)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run.rc").write_text("")  # vacío = corriendo
    (out_dir / "run.pid").write_text("")

    cmd = [
        sys.executable, str(REPO / "main.py"), source, "--mode", "local",
        "--num-clips", str(params.get("num_clips", 3)),
        "--aspect-ratio", params.get("ratio", "9:16"),
        "--format", str(params.get("quality", "720")),
    ]
    if params.get("language") and params["language"] not in ("auto", ""):
        cmd += ["--language", params["language"]]
    if not params.get("subtitles", True):
        cmd += ["--no-subtitles"]
    if params.get("track"):
        cmd += ["--track-face"]
    if params.get("upload"):
        cmd += ["--upload"]
    if resume:
        cmd += ["--resume"]

    env = os.environ.copy()
    env["LOCAL_OUTPUT_DIR"] = str(out_dir)
    env["LOCAL_PROGRESS_FILE"] = str(out_dir / "run.progress")
    extra = (REPO / ".env").read_text(encoding="utf-8") if (REPO / ".env").exists() else ""
    if extra:
        for line in extra.splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())

    logf = open(out_dir / "run.log", "w")
    proc = subprocess.Popen(
        ["bash", "-c", " ".join(shlex.quote(a) for a in cmd)],
        cwd=REPO, env=env, stdout=logf, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (out_dir / "run.pid").write_text(str(proc.pid))
    logf.close()


def recover_orphans() -> None:
    """Marca como terminadas las generaciones cuyo proceso ya murió (refresh/crash)."""
    for d in OUTPUT_BASE.iterdir() if OUTPUT_BASE.exists() else []:
        if not d.is_dir():
            continue
        pid_file = d / "run.pid"
        rc_file = d / "run.rc"
        if not pid_file.exists() or not pid_file.read_text().strip():
            continue
        try:
            alive = proc_alive(int(pid_file.read_text().strip()))
        except ValueError:
            alive = False
        if not alive and rc_file.exists() and not rc_file.read_text().strip():
            # murió sin escribir rc → crash
            rc_file.write_text("1")
            pid_file.unlink(missing_ok=True)


# ---------- UI ----------
def page():
    st.set_page_config(page_title="Shorts Studio", page_icon=":material/smart_display:", layout="wide")
    recover_orphans()

    st.title("Shorts Studio")
    st.caption("Pipeline local: descargar → transcribir → rank → crop → subtítulos → upload")

    default_name = st.text_input("Nombre del proyecto", value="proyecto",
                                 help="Se usa como carpeta en output/<nombre>/")
    src_url = st.text_input("URL de YouTube o ruta local", placeholder="https://www.youtube.com/... o /ruta/video.mp4")

    c1, c2, c3, c4 = st.columns(4)
    num_clips = c1.slider("Shorts", 1, 10, 3)
    quality = c2.selectbox("Calidad", ["360", "480", "720", "1080"], index=2)
    ratio = c3.selectbox("Aspecto", ["9:16", "1:1", "16:9"], index=0)
    language = c4.text_input("Idioma", value="auto", help="auto, es, en, ...")

    c5, c6, c7 = st.columns(3)
    subtitles = c5.checkbox("Subtítulos", value=True)
    track = c6.checkbox("Face-tracking", value=False)
    upload = c7.checkbox("Subir a Zernio", value=False)

    sub, gen, clr = st.columns([2, 2, 2])
    with sub:
        resume = st.checkbox("Reintentar desde donde quedó", value=False,
                             help="Reusa cachés (highlight/crop/subtitles) y solo sigue lo pendiente.")
    active = run_is_active(default_name) if default_name else False
    generate = gen.button("Generar", type="primary", disabled=active or not default_name or not src_url)
    clear = clr.button("Limpiar build (de cero)", disabled=active,
                       help="Borra highlights.json/shorts/subtitles del proyecto, conserva source y transcripción.")

    if clear and default_name:
        from shorts_generator.local import resume as _r
        _r.clear_build(str(project_dir(default_name)))
        st.toast("Build limpiado", icon=":material/delete:")
        st.rerun()

    params = {
        "num_clips": num_clips, "quality": quality, "ratio": ratio, "language": language,
        "subtitles": subtitles, "track": track, "upload": upload,
    }

    if generate and default_name and src_url:
        if not run_is_active(default_name):
            launch(default_name, src_url, params, resume)
            st.rerun()

    # ----- panel de progreso / estado -----
    if default_name:
        _render_status(default_name, params)


@st.fragment(run_every=2.0)
def _render_status(name: str, params: dict):
    """Pinta barra de pasos, log y resultado; auto-refresca mientras corre."""
    out_dir = project_dir(name)
    prog = read_progress(name)
    status = prog.get("status", "running") if prog else "running"
    stage = prog.get("stage")
    done = int(prog.get("done", 0))
    total = int(prog.get("total", 6))
    rc_file = out_dir / "run.rc"
    active = run_is_active(name)

    with st.container(border=True):
        st.subheader(f"Proyecto: {name}")

        # steps
        steps = st.status("Generando...", expanded=True) if active else st.status("Estado", expanded=True)
        with steps:
            for i, s in enumerate(STAGES, 1):
                if done > i:
                    st.write(f"✅ {s}")
                elif done == i:
                    st.write(f"⏳ {s} — {prog.get('message', '')}")
                else:
                    st.write(f"○ {s}")
        if not active:
            steps.update(label="Terminado", state="complete" if (rc_file.exists() and rc_file.read_text().strip() == "0") else "error")

        st.progress(min(done / max(total, 1), 1.0))

        # resultado / fallo
        if not active:
            if rc_file.exists() and rc_file.read_text().strip() == "0":
                _show_results(name)
            else:
                _show_error(name)

        if active:
            st.caption("Refrescando cada 2s · puedes cerrar esta pestaña; el proceso sigue.")
            st.rerun()


def _show_results(name: str):
    out_dir = project_dir(name)
    result_json = out_dir / "result.json"
    st.success("Generación terminada")
    if result_json.exists():
        try:
            data = json.loads(result_json.read_text())
            for i, s in enumerate(data.get("shorts", []), 1):
                clip = out_dir / "shorts" / (Path(s.get("clip_url", "x")).name)
                col_a, col_b = st.columns([2, 1])
                with col_a:
                    st.markdown(f"**#{i}** — {s.get('title', '(sin título)')}")
                with col_b:
                    st.write(f"score {s.get('score')}")
                if s.get("post_id"):
                    st.caption(f"Subido a Zernio · post {s['post_id']}")
                if s.get("upload_error"):
                    st.error(f"Upload falló: {s['upload_error']}")
                if clip.exists():
                    st.video(str(clip))
        except (json.JSONDecodeError, KeyError):
            st.warning("No se pudo leer result.json")
    # listar shorts sueltos si no hay result.json
    else:
        shorts_dir = out_dir / "shorts"
        if shorts_dir.exists():
            for p in sorted(shorts_dir.glob("short_*.mp4")):
                st.video(str(p))


def _show_error(name: str):
    out_dir = project_dir(name)
    st.error("La generación falló.")
    log_file = out_dir / "run.log"
    if log_file.exists():
        tail = "\n".join(log_file.read_text(errors="replace").splitlines()[-25:])
        st.code(tail, language="text")
    st.info("Pulsa **Generar** con 'Reintentar desde donde quedó' para retomar desde la última etapa.")


page()
