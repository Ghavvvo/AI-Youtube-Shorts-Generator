# Arquitectura — AI YouTube Shorts Generator

Documentación del repo original (estado `origin/main`), verificada contra el
código real. Explica flujo, capas, patrones, reglas y cómo añadir una feature.

---

## Vista del árbol

```
raíz
├── main.py                       CLI entry point (única entrada de usuario)
├── requirements.txt              deps del modo api (MuAPI)
├── requirements-local.txt        deps opcionales del modo local
├── .env.example                  plantilla de variables de entorno
├── assets/                       imágenes/videos para el README
└── shorts_generator/             paquete: toda la lógica de pipeline
    ├── __init__.py               exporta generate_shorts
    ├── config.py                 todas las settings desde env vars
    ├── pipeline.py               orquestador + dispatcher api ↔ local
    ├── muapi.py                  cliente MuAPI (submit / poll / run)
    ├── downloader.py             (api)  descarga vía MuAPI
    ├── transcriber.py            (api)  transcripción vía MuAPI /openai-whisper
    ├── highlights.py             ranking de virality (LLM pluggable) + prompts
    ├── clipper.py                (api)  autocrop vía MuAPI
    └── local/                    backends offline (--mode local)
        ├── downloader.py         yt-dlp (o ruta local)
        ├── transcriber.py        faster-whisper + caché .srt
        ├── llm.py                selector OpenAI / Gemini
        └── clipper.py            ffmpeg corte + OpenCV crop vertical
```

## Principio rector

**CLI + librería, sin interfaz.** El repo es (a) un CLI (`main.py`) y (b) una
librería reutilizable (`shorts_generator.generate_shorts`). No hay GUI. Todo
se configura por env vars (`.env`). El patrón es "capa de pipe por comando".

## El flujo de datos (cómo se conecta)

### Modo API (default) — todo vía MuAPI en la nube

```
main.py --mode api
  → generate_shorts(mode="api")
      pipeline._run_api()
        1. download_youtube()   → muapi.run("youtube-download") → hosted URL
        2. transcribe()         → muapi.run("openai-whisper")  → {duration, segments}
        3. get_highlights(llm_fn=call_muapi_llm)
                                 → muapi.run("gpt-5-mini")      → highlights rankeados
        4. crop_highlights()     → muapi.run("autocrop")        → URLs de shorts
  → result {mode, source_video_url, transcript, highlights, shorts}
```

### Modo local (--mode local) — en tu máquina

```
main.py --mode local
  → generate_shorts(mode="local")
      pipeline._run_local()
        1. download_youtube_local()  → yt-dlp (o usa ruta local)  → path mp4
        2. transcribe_local()        → faster-whisper + caché .srt → {duration, segments}
        3. get_highlights(llm_fn=call_local_llm)
                                       → OpenAI o Gemini          → highlights rankeados
        4. crop_highlights_local()     → ffmpeg corte + OpenCV crop → path local
  → result idéntico en forma (shorts usan rutas locales, no URLs)
```

### La conexión clave: el LLM de ranking es pluggable

`highlights.py` recibe `llm_fn: Callable[[str], str]`:

- `call_muapi_llm(prompt)` → usada por `_run_api`.
- `call_local_llm(prompt)` → usada por `_run_local`; despacha a OpenAI o Gemini
  según `LLM_PROVIDER`.

`get_highlights()` por defecto usa `call_muapi_llm`. El pipeline SIEMPRE pasa el
`llm_fn` correcto por argumento — nunca toca cuál usar directamente.

### Forma de datos contractada (los pipes encajan solos)

Todos los módulos producen/consumen la misma forma, así que api y local son
intercambiables:

- **Transcript**: `{"duration": float, "segments": [{"start", "end", "text"}]}`
- **Highlight**: `{"title", "start_time", "end_time", "score",
  "hook_sentence", "virality_reason"}`
- **Short** (resultado): highlight + `"clip_url"` (URL api o path local).
- **Result** completo devuelto por `generate_shorts`:
  `{"mode", "source_video_url", "transcript", "highlights", "shorts"}`

Ese contrato es el que hace que clippear (api) o clippear (local) produzcan el
mismo downstream.

## Patrones y reglas

### 1. Dispatcher de modo en un solo sitio
`pipeline.generate_shorts(mode=...)` decide api vs local. `_run_api` y
`_run_local` son paralelos (misma estructura, distinto backend). Regla:
**no dupliques el dispatcher; la elección de modo vive solo aquí**.

### 2. Módulo `local/` es gemelo espejo de la raíz
`downloader/transcriber/clipper` existen dos veces: raíz (api) y `local/`
(local). Mismo nombre de función (`download_youtube` vs `download_youtube_local`,
`transcribe` vs `transcribe_local`, `crop_highlights` vs `crop_highlights_local`).
Regla: **una feature de pipeline tiene su par local/ simétrico**.

### 3. Imports diferidos dentro del paquete local
`pipeline._run_local` importa los módulos `local/` DENTRO de la función, no en
top-level. Así `--mode api` no importa (ni exige instalar) las deps locales.
Regla: **no arrastres deps de un modo al otro al importar**.

### 4. Config central en `config.py`, lectura perezosa de `require_*`
- Todas las settings salen de env vars en un solo archivo (`config.py`).
- Las claves se validan con `require_api_key()`, `require_openai_key()`,
  `require_gemini_key()` en el momento de uso (no en import).
- No hay rutas/URLs/keys hardcodeadas en los módulos.

### 5. Cada backend checa sus deps y da error claro
`local/downloader/transcriber/llm/clipper` importan sus librerías pesadas
(yt-dlp, faster-whisper, openai, cv2) dentro de un `try/except ImportError` y
lanzan `RuntimeError` con la instrucción de instalación
(`pip install -r requirements-local.txt`). Regla: **error accionable, no
ImportError en bruto**.

### 6. Cachés para no repetir trabajo caro
- `local/transcriber` cachea a `.srt` (reusa si el caché es más nuevo que el
  video). Caché vacío/inválido se elimina y re-transcribe.
- `local/downloader` reusa `source_<video_id>.mp4` ya descargado.

### 7. MuAPI: submit → poll → run
`muapi.py` expone `submit`, `fetch_result`, `poll`, `run`. Los clientes API
solo llaman `muapi.run(endpoint, payload, label=...)`. El polling bloquea y
lanza `MuAPIError` en fallo/timeout. Regla: **no repitas el bucle de polling
fuera de muapi.py**.

## Cómo se agrega una feature

Ejemplo: añadir mejor crop vertical (nueva lógica de reframing).

### Paso 1 — decide el modo
¿Aplica a los dos modos o solo a uno? El crop es compartido conceptualmente,
así que toca `local/clipper.py` (local) o `clipper.py` (api) según dónde viva.

### Paso 2 — localiza la función espejo
- Local: `crop_clip_local` / `crop_highlights_local` en `local/clipper.py`.
- Api:   `crop_clip` / `crop_highlights` en `clipper.py`.
Edita el par que corresponda manteniendo la MISMA firma externa y el MISMO
contrato de salida (`{**h, "clip_url": ...}`).

### Paso 3 — si añade un parámetro
- Añade un flag en `main.py` (argparse).
- Pásalo por `generate_shorts(...)` → `_run_local/_run_api` → la función espejo.
- Añade la setting a `config.py` con default (lee env var).

### Paso 4 — si añade un knobs de env
1. `config.py`: `MI_NUEVO_SETTING = os.getenv("MI_NUEVO_SETTING", "default")`.
2. Usa `MI_NUEVO_SETTING` en el módulo que corresponda (no `os.getenv` suelto).
3. Documenta en `.env.example`.

### Paso 5 — respeta los contratos
- Transcript / highlight / short NO cambian de forma, o romperás los pipes.
- No rompas la simetría api↔local: si añades a un modo, decide si el otro lo
  necesita (o al menos no lo dejes inconsistente a mitad).

### Paso 6 — verifica
```bash
# api (necesita MUAPI_API_KEY)
python main.py "https://..." --num-clips 3
# local (necesita OPENAI_API_KEY o GEMINI + ffmpeg)
python main.py "/ruta/video.mp4" --mode local --num-clips 3
```
Comprueba que el `result.json` (con `--output-json result.json`) sigue
teniendo la misma forma.

## Reglas de estilo (del propio código)

- Python estándar + type hints (`Dict`, `List`, `Optional`) — usa `from
  typing import ...`.
- `print(..., flush=True)` para logs de progreso (importante porque el CLI
  hace streaming).
- Docstrings con "qué y por qué", no solo "qué".
- Excepciones específicas (`MuAPIError`, `RuntimeError` con instrucción),
  nada de `except Exception: pass`.
- `subprocess.run(cmd, check=True)` para ffmpeg; nada de shell fragile.

## Dependencias y setup

| Archivo | Rol |
|---|---|
| `requirements.txt` | api mode: `requests`, `python-dotenv` |
| `requirements-local.txt` | local mode: yt-dlp, faster-whisper, openai, google-genai, opencv-python |

- `.env` (gitignored) o export de env vars para las keys.
- Sin `pyproject.toml` ni `uv.lock` en este estado (repo original usa pip).

## Errores comunes / troubleshooting

- **`Whisper produced no segments`** → el video no tiene voz detectable o el
  idioma falla; usa `--language <ISO-639-1>`.
- **`opencv is required`** → instalaste solo `requirements.txt`; mete
  `requirements-local.txt` (o viceversa, `opencv` no está en api).
- **`MUAPI_API_KEY is not set`** → modo api sin key; usa `--mode local` con tu
  key de LLM, o define la key.
- **Resultado `shorts[].clip_url = null`** → ese clip falló al cortar; revisa
  el `error` en el mismo elemento.
