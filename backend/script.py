"""Timestamped script model + timing engine.

The whole system is driven by one JSON document (``scenes.json``) in which every
scene carries an absolute ``start`` time. Backend stages consume that document:
TTS renders each ``narration``, whisper aligns it word-by-word, and timings are
then reflowed so visual scenes, voice, captions and music all agree.

This is exactly the user's key insight — *input carries timestamps* — so audio
and video never "detect" sync; they both obey the same timeline.
"""

from __future__ import annotations

import json
import math

KINDS = ["title", "bullets", "steps", "stat", "compare", "quote", "flow", "outro"]

# Words-per-second target used to *estimate* scene lengths before TTS runs.
WPS = 2.55
LEAD = 0.35     # silence before the first narration word
SCENE_GAP = 0.22  # pause between scenes
TAIL = 1.05     # padding after the last word


def words(s: str) -> int:
    return len(s.split())


def estimate_duration(narration: str, wps: float = WPS) -> float:
    return LEAD + max(1, words(narration)) / wps


def estimate_timings(scenes: list[dict], gap: float = SCENE_GAP) -> list[dict]:
    """Fill ``start``/``duration`` for each scene from narration word count."""
    t = 0.0
    for s in scenes:
        dur = estimate_duration(s.get("narration", ""))
        s["duration"] = round(dur, 3)
        s["start"] = round(t, 3)
        t += dur + gap
    return scenes


# ---------------------------------------------------------------------------
# Cleaning / validation
# ---------------------------------------------------------------------------

def _norm(t: str) -> str:
    return "".join(c for c in t.lower() if c.isalnum())


def _tokens(narration: str) -> list[str]:
    return [_norm(t) for t in narration.split()]


def clean_scene(sc: dict, index: int) -> dict:
    sc = dict(sc)
    sc["id"] = str(sc.get("id") or f"s{index + 1:02d}")
    sc["kind"] = sc.get("kind") if sc.get("kind") in KINDS else "bullets"
    heading = str(sc.get("heading") or "").strip()
    sc["heading"] = heading[:110]
    items = [str(x).strip()[:80] for x in (sc.get("items") or [])]
    items = [x for x in items if x][:8]
    sc["items"] = items
    sc["narration"] = str(sc.get("narration") or "").strip()
    sc.setdefault("label", "")
    sc.setdefault("kicker", "")
    sc.setdefault("accent", None)
    sc.setdefault("sfx", [])
    sc.setdefault("start", 0.0)
    sc.setdefault("duration", 4.0)
    return sc


def clean_script(obj) -> dict:
    """Normalize an LLM (or user) script dict into a validated script."""
    if isinstance(obj, dict) and "scenes" in obj:
        data = obj
    elif isinstance(obj, list):
        data = {"scenes": obj}
    else:
        raise ValueError("script must be an object with a 'scenes' array")
    title = str(data.get("title") or "Untitled").strip() or "Untitled"
    scenes = [clean_scene(s, i) for i, s in enumerate(data.get("scenes") or [])]
    if not scenes:
        raise ValueError("script has no scenes")
    return {"title": title, "scenes": scenes}


# ---------------------------------------------------------------------------
# Reflow: fit the timeline to the real audio (word-accurate sync)
# ---------------------------------------------------------------------------

def _match_scene_first_words(scenes: list[dict], stream: list[tuple[str, float, float]]) -> list[int | None]:
    """Greedy ordered subsequence match of each narration onto the word stream.

    ``stream`` = [(normalized_word, start, end)] from whisper on the whole voice
    track. Returns, per scene, the stream index of its first word (or None).
    """
    pos = 0
    out: list[int | None] = []
    for sc in scenes:
        toks = _tokens(sc.get("narration") or "")
        if not toks:
            out.append(None)
            continue
        start = pos
        found: int | None = None
        while start <= len(stream):
            j = start
            matched = 0
            for tok in toks:
                while j < len(stream) and stream[j][0] != tok:
                    j += 1
                if j >= len(stream):
                    break
                matched += 1
                j += 1
            if matched >= max(1, int(len(toks) * 0.6)):
                found = start
                pos = j
                break
            start += 1
        out.append(found)
    return out


def reflow_fit(scenes: list[dict], raw_words: list[dict], gap: float = SCENE_GAP) -> tuple[list[dict], float]:
    """Fit mode (default): boundaries come from the real voice audio.

    Returns (scenes with start/duration updated, total_video_duration).
    """
    stream = [(_norm(w.get("w", "")), float(w["s"]), float(w["e"])) for w in raw_words if w.get("w", "").strip()]
    first = _match_scene_first_words(scenes, stream)
    scenes = [dict(s) for s in scenes]
    areal = [stream[i] if i is not None else None for i in first]

    boundaries = [0.0]
    for i in range(1, len(scenes)):
        # next scene begins just before its first (real) word
        if areal[i] is not None:
            b = areal[i][1] - 0.12
        else:
            b = scenes[i - 1]["start"] + scenes[i - 1]["duration"] + gap
        boundaries.append(max(b, boundaries[-1] + 0.4))
    total = (stream[-1][2] + TAIL) if stream else sum(s["duration"] for s in scenes)
    if total < boundaries[-1] + 0.5:
        total = boundaries[-1] + 1.0

    for i, s in enumerate(scenes):
        s["start"] = round(boundaries[i], 3)
        end = boundaries[i + 1] if i + 1 < len(scenes) else total
        s["duration"] = round(max(end - boundaries[i], 0.5), 3)
    return scenes, round(total, 3)


def reflow_strict(scenes: list[dict], raw_words: list[dict]) -> tuple[list[dict], float]:
    """Strict mode: keep the *planned* timestamps; pad/trim silence around narration
    so the voice lands inside its slot. Longer narration is flagged, not broken."""
    scenes = [dict(s) for s in scenes]
    flags = []
    for i, s in enumerate(scenes):
        nw = words(s.get("narration") or "")
        need = estimate_duration(s.get("narration") or "")
        if need > s["duration"]:
            flags.append({"scene": s["id"], "over": round(need - s["duration"], 2)})
    total = scenes[-1]["start"] + scenes[-1]["duration"] if scenes else 0.0
    return scenes, round(total, 3)


# ---------------------------------------------------------------------------
# Subtitles / SRT
# ---------------------------------------------------------------------------

def ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_from_words(scenes: list[dict], raw_words: list[dict], max_words: int = 6) -> str:
    """Group whisper words into subtitle cues; prefix each cue with its scene number."""
    if not raw_words:
        return ""
    stream = [(_norm(w.get("w", "")), float(w["s"]), float(w["e"])) for w in raw_words if w.get("w", "").strip()]
    first = _match_scene_first_words(scenes, stream)
    scene_of = [None] * len(stream)
    for i, fi in enumerate(first):
        if fi is not None:
            scene_of[fi] = i
    cur = 0
    out = []
    n = 1
    i = 0
    while i < len(stream):
        take = min(max_words, len(stream) - i)
        chunk = stream[i:i + take]
        s0 = chunk[0][1]
        e0 = chunk[-1][2]
        si = scene_of[i] if scene_of[i] is not None else cur
        cur = max(cur, si or 0)
        text = " ".join(w for w, _, _ in chunk)
        out.append(f"{n}\n{ts(s0)} --> {ts(e0 + 0.1)}\n[{si + 1:02d}] {text}\n")
        n += 1
        i += take
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Theme presets
# ---------------------------------------------------------------------------

THEMES = {
    "ocean": {
        "bg": "#060d17", "panel": "#0b1726", "panel2": "#0f2032", "line": "#1d3a52",
        "text": "#eef6fb", "dim": "#8aa7bd", "faint": "#527189", "accent": "#4fd8ff",
        "palette": ["#4fd8ff", "#ffbe5c", "#ff6b81", "#6fe6a8", "#a78bfa"],
    },
    "ember": {
        "bg": "#110a07", "panel": "#1a0f09", "panel2": "#241208", "line": "#3a2415",
        "text": "#fdf1e7", "dim": "#cda78d", "faint": "#7a5a40", "accent": "#ff9d4d",
        "palette": ["#ff9d4d", "#ffbe5c", "#ff6b81", "#6fe6a8", "#4fd8ff"],
    },
    "forest": {
        "bg": "#07130d", "panel": "#0b1c13", "panel2": "#0f2719", "line": "#1d3a2a",
        "text": "#ecfaf0", "dim": "#8fc3a2", "faint": "#4f7a60", "accent": "#6fe6a8",
        "palette": ["#6fe6a8", "#4fd8ff", "#ffbe5c", "#a78bfa", "#ff6b81"],
    },
    "violet": {
        "bg": "#0d0916", "panel": "#150f22", "panel2": "#1d1430", "line": "#2c2148",
        "text": "#f2edfc", "dim": "#b3a3d6", "faint": "#6f5c94", "accent": "#a78bfa",
        "palette": ["#a78bfa", "#ff6b81", "#4fd8ff", "#6fe6a8", "#ffbe5c"],
    },
}


def make_theme(style: str = "ocean", accent: str | None = None) -> dict:
    th = dict(THEMES.get(style, THEMES["ocean"]))
    th = json.loads(json.dumps(th))
    if accent:
        th["accent"] = accent
        # put the accent first in the palette so scene accents rotate from it
        pal = th["palette"]
        th["palette"] = [accent] + [c for c in pal if c != accent]
    return th