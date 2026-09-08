"""CLI entry point.

Usage:
    python main.py "https://www.youtube.com/watch?v=..." \
        --num-clips 3 --aspect-ratio 9:16
"""
import argparse
import json
import os
import sys

# Windows uses 'charmap' by default, which can't encode Unicode characters
# like →. Reconfigure stdout/stderr to UTF-8 so output works on all platforms.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from shorts_generator import generate_shorts


def main() -> int:
    parser = argparse.ArgumentParser(description="AI YouTube Shorts Generator")
    parser.add_argument("url", help="YouTube URL, file:// URL, or local file path")
    parser.add_argument(
        "--mode",
        choices=["api", "local"],
        default="api",
        help="api (default, MuAPI) or local (remote URL, file://, or local path + faster-whisper + LLM provider + ffmpeg).",
    )
    parser.add_argument("--num-clips", type=int, default=3, help="How many shorts to render (default: 3)")
    parser.add_argument("--aspect-ratio", default="9:16", help="Output aspect ratio (default: 9:16)")
    parser.add_argument("--format", default="720", help="Source download resolution: 360 / 480 / 720 / 1080 (default: 720)")
    parser.add_argument("--language", default=None, help="Force Whisper language code, e.g. 'en' (default: auto-detect)")
    parser.add_argument("--output-json", default=None, help="Write the full result JSON to this path")
    parser.add_argument("--no-subtitles", action="store_true", help="Don't burn subtitles onto shorts (local mode)")
    parser.add_argument("--track-face", action="store_true", help="Face-tracking vertical crop (local mode, default off)")
    parser.add_argument("--upload", action="store_true", help="Upload finished shorts to Zernio (local mode)")
    parser.add_argument("--resume", action="store_true", help="Reanudar desde la última etapa cacheada (local mode)")
    args = parser.parse_args()

    # Si LOCAL_PROGRESS_FILE está seteado, escribimos el progreso por etapa ahí
    # (JSON). La GUI detached lo lee para pintar la barra.
    def _progress(stage: str, done: int, total: int, msg: str = "") -> None:
        print(f"[progress] {stage} {done}/{total} {msg}", flush=True)
        if os.environ.get("LOCAL_PROGRESS_FILE"):
            try:
                with open(os.environ["LOCAL_PROGRESS_FILE"], "w", encoding="utf-8") as f:
                    json.dump({"stage": stage, "done": done, "total": total, "message": msg}, f)
            except OSError:
                pass

    try:
        result = generate_shorts(
            youtube_url=args.url,
            num_clips=args.num_clips,
            aspect_ratio=args.aspect_ratio,
            download_format=args.format,
            language=args.language,
            mode=args.mode,
            subtitles=not args.no_subtitles,
            face_tracking=args.track_face,
            upload=args.upload,
            resume=args.resume,
            on_progress=_progress,
        )
    except Exception as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1

    print("\n" + "=" * 72)
    print(f"Mode:          {result.get('mode', args.mode)}")
    print(f"Source video:  {result['source_video_url']}")
    print(f"Highlights:    {len(result['highlights'])} candidates → kept top {len(result['shorts'])}")
    print("=" * 72)
    for i, s in enumerate(result["shorts"], 1):
        print(f"\n#{i}  score={s.get('score')}  {s.get('start_time'):.1f}s → {s.get('end_time'):.1f}s")
        print(f"     title:  {s.get('title')}")
        print(f"     hook:   {s.get('hook_sentence')}")
        if s.get("clip_url"):
            print(f"     clip:   {s['clip_url']}")
        else:
            print(f"     clip:   FAILED ({s.get('error')})")

    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nFull JSON written to {args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
