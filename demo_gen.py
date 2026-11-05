"""Generate a self-contained demo job (numpy-only audio, no TTS) so the renderer
can be smoke-tested independently of the backend models.

Usage:  python3 demo_gen.py <out_dir>
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "demo-job"
OUT.mkdir(parents=True, exist_ok=True)

# ---- scenes.json (the timestamped contract) ---------------------------------
FONT_SRC = Path(__file__).parent / "renderer" / "fonts" / "DMSans-Medium.ttf"
shutil.copy(FONT_SRC, OUT / "DMSans-Medium.ttf")

theme = {
    "bg": "#060d17", "panel": "#0b1726", "panel2": "#0f2032", "line": "#1d3a52",
    "text": "#eef6fb", "dim": "#8aa7bd", "faint": "#527189", "accent": "#4fd8ff",
    "palette": ["#4fd8ff", "#ffbe5c", "#ff6b81", "#6fe6a8", "#a78bfa"],
}

scenes = [
    dict(id="s01", kind="title", label="INTRODUCTION", heading="The ReelForge Pipeline",
         items=["From text to a narrated mp4 in under a minute."], narration="",
         start=0.0, duration=3.6, accent="#4fd8ff"),
    dict(id="s02", kind="steps", label="STAGES",
         heading="Five vectorized stages",
         items=["Script", "TTS", "Align", "Score", "Render"],
         narration="", start=4.1, duration=5.8, accent="#ffbe5c"),
    dict(id="s03", kind="flow", label="SYNC",
         heading="Timestamps drive sync",
         items=["Voice", "Scenes", "Captions", "Music"],
         narration="", start=10.3, duration=5.0, accent="#6fe6a8"),
    dict(id="s04", kind="stat", label="SPEED",
         heading="18s", items=["Render time for two minutes of 1080p30", "GPU", "Compiled once"],
         narration="", start=15.7, duration=4.6, accent="#a78bfa"),
    dict(id="s05", kind="compare", label="OLD vs NEW",
         heading="Vectorized beats looped",
         items=["Before", "After", "Python-filter loops", "SciPy lfilter", "29 seconds", "under 1 second"],
         narration="", start=20.7, duration=5.2, accent="#4fd8ff"),
    dict(id="s06", kind="outro", label="OUTRO", heading="Try it yourself",
         items=["Paste your source", "Let NIM script it", "Review the timeline", "Render"],
         narration="", start=26.3, duration=4.4, accent="#ffbe5c"),
]

# manual subtitle words so the caption bar is exercised (video-relative seconds)
subtitles = {"words": []}
_lines = [
    [(0.2, "The ReelForge", "pipeline")],
    [(4.3, "Five", "vectorized", "stages")],
    [(10.5, "Timestamps", "drive", "sync")],
    [(15.9, "Eighteen", "seconds")],
    [(20.9, "Vectorized", "beats", "looped")],
    [(26.5, "Try", "it", "yourself")],
]
t = 0.0
for n, line in enumerate(_lines):
    for (w0, *ws) in line:
        start = w0
        for i, w in enumerate(ws):
            subtitles["words"].append({"w": w, "s": start + i * 0.35, "e": start + (i + 1) * 0.35})
        t = start + len(ws) * 0.35

doc = {"title": "ReelForge Demo", "total": 31.5, "music": "music.wav",
       "theme": theme, "scenes": scenes, "subtitles": subtitles}
(OUT / "scenes.json").write_text(json.dumps(doc, indent=2))

# ---- audio (numpy-only) ------------------------------------------------------
sr = 48000

def write_sine(name, hz, dur, amp=0.35):
    t = np.arange(int(dur * sr)) / sr
    x = amp * np.sin(2 * np.pi * hz * t) * np.exp(-t * 1.5)
    sf.write(OUT / name, np.stack([x, x], axis=1), sr)

# a simple held-chord "voice-like" track so fframes has something to mix
dur = 31.5
t = np.arange(int(dur * sr)) / sr
voice = 0.3 * np.sin(2 * np.pi * 140 * t) * np.minimum(1, t / 0.1) * np.exp(-np.maximum(0, t - 28) * 0.8)
voice = np.minimum(voice, 0.4)
sf.write(OUT / "voice.wav", voice.astype(np.float32), sr)
music = np.sin(2 * np.pi * 55 * t) * 0.2 + np.sin(2 * np.pi * 110 * t) * 0.1
music *= np.concatenate([np.linspace(0, 1, int(0.05 * sr)), np.ones(max(0, len(t) - 2 * int(0.05 * sr))), np.linspace(1, 0, int(0.05 * sr))])
sf.write(OUT / "music.wav", np.stack([music, music], axis=1).astype(np.float32), sr)

for i, name in enumerate(["whoosh", "whoosh_short", "click", "tick", "ping", "hit", "riser", "chime", "data"]):
    hz = 300 + i * 160
    write_sine(f"sfx_{name}.wav", hz, 0.5, 0.4)

print("wrote demo job ->", OUT)