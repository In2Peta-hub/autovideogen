"""Audio: parallel TTS, word alignment, and *vectorized* music/SFX generation.

The previous procedural music ran ~29s because its biquad filters were Python
loops over millions of samples. Here every filter is `scipy.signal.lfilter`
(compiled), so the same bed is built in well under a second. TTS is parallelized
across scenes with a thread pool (ONNX Runtime releases the GIL during `Run`).
"""

from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import soundfile as sf
from scipy import signal

SR_MUSIC = 48000
SR_VOICE = 24000
SR_WHISPER = 16000
LEAD = 0.35
GAP = 0.22


# ---------------------------------------------------------------------------
# Vectorized biquad (this is the whole reason music is fast now)
# ---------------------------------------------------------------------------

def biquad(x: np.ndarray, kind: str, f0: float, q: float = 0.707, sr: int = SR_MUSIC) -> np.ndarray:
    w0 = 2 * math.pi * f0 / sr
    c, s = math.cos(w0), math.sin(w0)
    a = s / (2 * q)
    if kind == "lp":
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]; a0 = [1 + a, -2 * c, 1 - a]
    elif kind == "hp":
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]; a0 = [1 + a, -2 * c, 1 - a]
    elif kind == "bp":
        b = [a, 0, -a]; a0 = [1 + a, -2 * c, 1 - a]
    a_den = [1.0, a0[1] / a0[0], a0[2] / a0[0]]
    b_num = [b[0] / a0[0], b[1] / a0[0], b[2] / a0[0]]
    return signal.lfilter(b_num, a_den, x.astype(np.float32)).astype(np.float32)


def env_adsr(n: int, a: float, d: float, sustain: float, r: float, sr: int = SR_MUSIC) -> np.ndarray:
    e = np.ones(n, dtype=np.float32)
    a_n = max(1, min(int(a * sr), n))
    e[:a_n] = np.linspace(0, 1, a_n) ** 0.6
    d_n = max(1, min(int(d * sr), n - a_n))
    if d_n:
        e[a_n:a_n + d_n] = 1 - (1 - sustain) * (np.linspace(0, 1, d_n) ** 1.5)
    if a_n + d_n < n:
        e[a_n + d_n:] = sustain
    r_n = min(int(r * sr), n // 2)
    if r_n:
        e[-r_n:] *= np.linspace(1, 0, r_n) ** 1.2
    return e.astype(np.float32)


def note_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _stereo(x: np.ndarray, pan: float = 0.0) -> np.ndarray:
    l = np.sqrt((1 - pan) / 2)
    r = np.sqrt((1 + pan) / 2)
    return np.stack([x * l, x * r], axis=1)


# ---------------------------------------------------------------------------
# Music bed
# ---------------------------------------------------------------------------

def _pad_note(m: float, dur: float, amp: float, pan: float, rng: np.random.Generator) -> np.ndarray:
    n = int(dur * SR_MUSIC)
    t = np.arange(n, dtype=np.float32) / SR_MUSIC
    f = note_hz(m)
    sig = np.zeros(n, dtype=np.float32)
    for det, g in [(-0.006, 0.55), (0.0, 1.0), (0.007, 0.55)]:
        ph = 2 * np.pi * f * (1 + det) * t + rng.uniform(0, 6.28)
        sig = sig + g * (np.sin(ph) + 0.18 * np.sin(2 * ph))
    sig = sig / 1.9
    sig = biquad(sig, "lp", min(2600.0, 900 + f * 4), 0.6)
    sig *= env_adsr(n, 1.1, 0.8, 0.72, 1.4)
    return _stereo(sig * amp, pan)


def _pluck(m: float, dur: float, amp: float, pan: float) -> np.ndarray:
    n = int(dur * SR_MUSIC)
    t = np.arange(n, dtype=np.float32) / SR_MUSIC
    f = note_hz(m)
    sig = (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t))
    sig *= np.exp(-t * 7.0) * np.minimum(1, t / 0.004)
    sig = biquad(sig.astype(np.float32), "lp", 3400, 0.8) * amp
    return _stereo(sig, pan)


def _kick(amp: float) -> np.ndarray:
    n = int(0.45 * SR_MUSIC)
    t = np.arange(n, dtype=np.float32) / SR_MUSIC
    f = 52 * np.exp(-t * 8) + 38
    sig = np.sin(2 * np.pi * np.cumsum(f) / SR_MUSIC) * np.exp(-t * 8) * amp
    return _stereo(sig)


def _hat(amp: float, rng: np.random.Generator) -> np.ndarray:
    n = int(0.06 * SR_MUSIC)
    x = rng.normal(0, 1, n).astype(np.float32)
    x = biquad(x, "hp", 6500, 0.8)
    x *= np.exp(-np.arange(n) / SR_MUSIC * 85) * amp
    return _stereo(x)


def _add(buf: np.ndarray, sig: np.ndarray, at: float) -> np.ndarray:
    i = int(at * SR_MUSIC)
    j = min(len(buf), i + len(sig))
    if i < len(buf):
        buf[i:j] += sig[:j - i]
    return buf


def reverb(x: np.ndarray, delays=(0.071, 0.113, 0.157, 0.211), gains=(0.5, 0.35, 0.28, 0.2)) -> np.ndarray:
    y = x.copy()
    for d, g in zip(delays, gains):
        n = int(d * SR_MUSIC)
        if n < len(x):
            y[n:] += x[:-n] * g
    return y


def music_bed(total: float, bpm: int = 96, seed: int = 7) -> np.ndarray:
    """Ambient beat bed matched to `total` seconds. Vectorized -> < 1s."""
    rng = np.random.default_rng(seed)
    n = int(total * SR_MUSIC)
    buf = np.zeros((n, 2), dtype=np.float32)
    beat = 60 / bpm
    bar = 4 * beat
    prog = [(48, 0), (41, 0.25), (43, 0.5), (46, 0.75)]  # root/relative-bass positions
    nbars = max(4, int(math.ceil(total / bar)))
    for b in range(nbars):
        root_m = prog[b % len(prog)][0]
        t0 = b * bar
        if t0 * SR_MUSIC < n:
            _add(buf, _pad_note(root_m, bar * 0.98, 0.16, -0.2, rng), t0)
        # arp plucks
        for k, mrel in enumerate([24, 31, 28, 24]):
            for sub, off in [(0, 0), (2, beat * 2)]:
                _add(buf, _pluck(root_m + mrel, beat * 1.5, 0.05, 0.35), t0 + k * beat + off)
        # drums
        for k in range(4):
            btm = t0 + k * beat
            _add(buf, _kick(0.42), btm)
            _add(buf, _hat(0.16, rng), btm + beat / 2)
            _add(buf, _hat(0.08, rng), btm)
    buf = reverb(buf)
    # gentle master shaping + fades
    buf *= 0.9
    fi = int(min(2.0, total / 3) * SR_MUSIC)
    buf[:fi] *= np.linspace(0, 1, fi)[:, None].astype(np.float32)
    fo = int(min(3.0, total / 3) * SR_MUSIC)
    if fo:
        buf[-fo:] *= np.linspace(1, 0, fo)[:, None].astype(np.float32)
    peak = np.abs(buf).max()
    if peak > 0.9:
        buf *= 0.9 / peak
    return buf


# ---------------------------------------------------------------------------
# SFX
# ---------------------------------------------------------------------------

def _noise(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.normal(0, 1, n).astype(np.float32)


def sfx_whoosh(dur: float = 1.2, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(dur * SR_MUSIC)
    t = np.arange(n, dtype=np.float32) / SR_MUSIC
    x = _noise(n, rng)
    x = biquad(x, "bp", 1800, 0.8)
    x *= env_adsr(n, dur * 0.35, dur * 0.4, 0.8, dur * 0.25) * 0.5
    return _stereo(x, 0.1)


def sfx_whoosh_short() -> np.ndarray:
    return sfx_whoosh(0.55, 2)


def sfx_click() -> np.ndarray:
    rng = np.random.default_rng(3)
    n = int(0.06 * SR_MUSIC)
    x = _noise(n, rng)
    x = biquad(x, "hp", 3000, 0.8)
    x *= np.exp(-np.arange(n) / SR_MUSIC * 40) * 0.7
    return _stereo(x)


def sfx_tick() -> np.ndarray:
    n = int(0.04 * SR_MUSIC)
    t = np.arange(n, dtype=np.float32) / SR_MUSIC
    x = np.sin(2 * np.pi * 1400 * t) * np.exp(-t * 90) * 0.5
    return _stereo(x)


def sfx_ping() -> np.ndarray:
    n = int(0.5 * SR_MUSIC)
    t = np.arange(n, dtype=np.float32) / SR_MUSIC
    f = 880 * (1 + 0.02 * np.sin(2 * np.pi * 6 * t))
    x = (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(4 * np.pi * f * t))
    x *= np.exp(-t * 4.5) * 0.4
    return _stereo(x, 0.4)


def sfx_hit() -> np.ndarray:
    rng = np.random.default_rng(4)
    n = int(0.5 * SR_MUSIC)
    t = np.arange(n, dtype=np.float32) / SR_MUSIC
    th = np.sin(2 * np.pi * np.cumsum(70 * np.exp(-t * 12) + 40) / SR_MUSIC) * np.exp(-t * 9)
    ns = biquad(_noise(n, rng), "hp", 1500, 0.7) * 0.15
    x = th + ns
    x *= np.minimum(1, t / 0.002)
    return _stereo(x * 0.8)


def sfx_chime() -> np.ndarray:
    n = int(1.2 * SR_MUSIC)
    t = np.arange(n, dtype=np.float32) / SR_MUSIC
    x = sum(np.sin(2 * np.pi * f * t) * g for f, g in [(880, 1.0), (1320, 0.5), (1760, 0.3)])
    x *= np.exp(-t * 2.2) * 0.35
    return _stereo(x)


def sfx_riser() -> np.ndarray:
    rng = np.random.default_rng(5)
    dur = 1.6
    n = int(dur * SR_MUSIC)
    t = np.arange(n, dtype=np.float32) / SR_MUSIC
    x = _noise(n, rng)
    x = biquad(x, "bp", 1800, 1.2)
    x *= (t / dur) ** 2 * 0.5
    return _stereo(x)


def sfx_data() -> np.ndarray:
    rng = np.random.default_rng(6)
    n = int(1.0 * SR_MUSIC)
    x = np.zeros(n, dtype=np.float32)
    for k in range(9):
        at = int((0.05 + k * 0.11) * SR_MUSIC)
        tk = np.arange(int(0.028 * SR_MUSIC), dtype=np.float32)
        s = np.sin(2 * np.pi * 1200 * tk / SR_MUSIC) * np.exp(-tk / SR_MUSIC * 90) * 0.4
        j = min(n, at + len(s))
        x[at:j] += s[:j - at]
    return _stereo(x)


SFX = {
    "whoosh": sfx_whoosh, "whoosh_short": sfx_whoosh_short, "click": sfx_click,
    "tick": sfx_tick, "ping": sfx_ping, "hit": sfx_hit, "chime": sfx_chime,
    "riser": sfx_riser, "data": sfx_data,
}


def gen_sfx(names: list[str], path: str):
    os.makedirs(path, exist_ok=True)
    for name in names:
        fn = SFX.get(name)
        if fn:
            sf.write(f"{path}/sfx_{name}.wav", fn(), SR_MUSIC)


# ---------------------------------------------------------------------------
# TTS (parallel) + whisper alignment
# ---------------------------------------------------------------------------

def _synth(kokoro, text: str, voice: str, speed: float, lang: str) -> np.ndarray:
    a, sr = kokoro.create(text, voice=voice, speed=speed, lang=lang)
    a = a.astype(np.float32)
    loud = np.where(np.abs(a) > 0.008)[0]
    if len(loud) == 0:
        return a
    pad = int(0.025 * sr)
    return a[max(0, loud[0] - pad):min(len(a), loud[-1] + pad)]


def assemble_voice(
    scenes: list[dict],
    kokoro,
    voice: str = "bm_daniel",
    speed: float = 0.96,
    lang: str = "en-gb",
    workers: int = 4,
) -> tuple[np.ndarray, list[float], list[float]]:
    """Synthesize each scene's narration (in parallel) and splice into one track.

    Returns (voice_24k, scene_audio_starts_in_voice, durations_in_seconds).
    """
    texts = [sc.get("narration", "").strip() for sc in scenes]
    results: list[np.ndarray | None] = [None] * len(texts)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        fut = {ex.submit(_synth, kokoro, txt, voice, speed, lang): i for i, txt in enumerate(texts)}
        for f in as_completed(fut):
            idx = fut[f]
            results[idx] = f.result()

    parts: list[np.ndarray] = []
    starts: list[float] = []
    t = LEAD
    for i, a in enumerate(results):
        if a is None or len(a) == 0:
            a = np.zeros(int(0.5 * SR_VOICE), dtype=np.float32)
        starts.append(round(t, 4))
        parts.append(a)
        t += len(a) / SR_VOICE + GAP
    dur = t - GAP
    head = np.zeros(int(LEAD * SR_VOICE), dtype=np.float32)
    chunks = [head]
    for a in parts:
        chunks.append(a)
        chunks.append(np.zeros(int(GAP * SR_VOICE), dtype=np.float32))
    voice = np.concatenate(chunks).astype(np.float32)
    if int(dur * SR_VOICE) < len(voice):
        voice = voice[: int(dur * SR_VOICE)]
    return voice, starts, [len(a) / SR_VOICE for a in parts]


def align_words(voice: np.ndarray, whisper) -> list[dict]:
    """whisper ensures word timings; returns [{w, s, e}] (contract keys) on the voice track."""
    n = int(round(len(voice) * SR_WHISPER / SR_VOICE))
    a16 = np.interp(np.linspace(0, len(voice) - 1, n), np.arange(len(voice)), voice).astype(np.float32)
    segs, _ = whisper.transcribe(a16, word_timestamps=True, beam_size=5, language="en")
    words = []
    for s in segs:
        for w in s.words:
            words.append({"w": w.word.strip(), "s": round(w.start, 4), "e": round(w.end, 4)})
    return words


def write_voice(voice: np.ndarray, path: str):
    sf.write(path, voice, SR_VOICE)


def normalize_voice(voice: np.ndarray, target_db: float = -18.0) -> np.ndarray:
    rms = float(np.sqrt(np.mean(voice ** 2)))
    if rms < 1e-6:
        return voice
    gain = 10 ** (target_db / 20) / rms
    gain = min(max(gain, 0.5), 8.0)
    return np.clip(voice * gain, -1.0, 1.0)