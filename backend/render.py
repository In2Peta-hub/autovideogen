"""Stage a job directory and drive the prebuilt `reel-renderer` binary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

RENDERER_BIN = Path(__file__).resolve().parent.parent / "renderer" / "target" / "release" / "reel-renderer"
FONTS_DIR = Path(__file__).resolve().parent.parent / "renderer" / "fonts"


def renderer_ready() -> bool:
    return RENDERER_BIN.is_file()


def write_scenes_json(job_dir: Path, script: dict, subtitles: dict, total: float,
                      theme: dict, music: bool) -> Path:
    doc = {
        "title": script["title"],
        "total": total,
        "music": "music.wav" if music else None,
        "theme": theme,
        "scenes": script["scenes"],
        "subtitles": subtitles,
    }
    path = job_dir / "scenes.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    return path


def stage_fonts(job_dir: Path):
    if FONTS_DIR.is_dir():
        for f in FONTS_DIR.iterdir():
            if f.suffix.lower() in (".ttf", ".otf"):
                shutil.copy(f, job_dir / f.name)


def render(job_dir: Path, out_name: str = "out.mp4", on_line=None) -> Path:
    """Run the renderer; return the mp4 path."""
    if not renderer_ready():
        raise RuntimeError(
            "renderer binary missing — build it first:\n"
            "  cd renderer && cargo build --release"
        )
    env = dict(os.environ)
    cmd = [str(RENDERER_BIN), "--job", str(job_dir), "render"]
    proc = subprocess.Popen(cmd, cwd=str(job_dir), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        line = line.rstrip("\n")
        if on_line:
            on_line(line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"renderer exited with code {proc.returncode}")
    return job_dir / out_name