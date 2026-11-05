# ReelForge

Turn source text into a **narrated, word-synced 1080p30 video** in under a minute — no
per-video recompiles, no hand-authored animations. It drives the same `fframes` (Skia/Metal)
engine used by `youtube-2min`, but through a **timestamped data contract** instead of generated
Rust code.

```
┌────────────┐   OpenAI-compatible LLM (default: in2peta-1)   ┌─────────────────┐
│ Firstend UI │── source text ───────────────────────────────▶│ /api/script      │
│  (browser)  │   (API key lives on the server)               │   → scenes[]     │
└─────┬───────┘                                               └─────────────────┘
      │ script with timestamps (editable in UI)
      ▼
┌──────────────────────────────── backend  ────────────────────────────────────┐
│ Kokoro TTS (parallel across scenes)  →  whisper word alignment  →  reflow    │
│ music/SFX bed (vectorized, scipy)    →  stage jobs/scene.json    →  render   │
└──────────────────────────────────────────────────────────────────────────────┘
      ▼
   prebuilt  reel-renderer  (compiled once)  →  out.mp4 + .srt
```

## Why timestamps make sync trivial

Every scene is one row with an **absolute `start`** on the master timeline. TTS renders each
`narration`, whisper maps every spoken word back onto that timeline, and the caption bar,
scene cuts, SFX and music all read the *same* numbers — nothing has to hunt for sync.
The job dict is `scenes.json`:

```json
{
  "title": "…",
  "total": 120.0,
  "music": "music.wav",
  "theme": { "bg": "#060d17", "accent": "#4fd8ff", "palette": ["#4fd8ff", "…"] },
  "scenes": [
    { "id": "s01", "kind": "title", "kicker": "01 / 12", "label": "INTRO",
      "start": 0.0, "duration": 7.24, "accent": "#4fd8ff",
      "heading": "How YouTube Works", "items": ["One upload. Millions of viewers."],
      "narration": "Every minute, hundreds of hours of video arrive on YouTube.",
      "sfx": ["whoosh", "hit"] },
    { "kind": "steps" | "bullets" | "stat" | "compare" | "quote" | "flow" | "outro" }
  ],
  "subtitles": { "words": [ { "w": "Every", "s": 0.35, "e": 0.62 } ] }
}
```

## Architecture

| Module | Purpose |
|---|---|
| `frontend/index.html` | Single-file UI: paste text, edit the timestamped timeline, live stage timings, player + MP4/SRT download |
| `backend/app.py` | FastAPI HTTP endpoints |
| `backend/nim.py` | OpenAI-compatible LLM client. **API key lives server-side** (env `REELFORGE_API_KEY`). Default provider `in2peta-1` (`deepseek-v4p1-flash`); `nvidia` stays as an option; any `base_url`/model is supported |
| `backend/script.py` | Timestamp model + estimate/reflow (`fit` vs `strict`), SRT generation, themes |
| `backend/audio.py` | Parallel Kokoro TTS (`ThreadPoolExecutor`), whisper alignment, **vectorized** music/SFX (scipy `lfilter`) |
| `backend/jobs.py` + `render.py` | Job store, background pipeline, staging, binary invocation |
| `renderer/` | `reel-renderer` — a generic `fframes` crate that loads `scenes.json` at runtime. Built once. |

The LLM API key is configured server-side only (via the `REELFORGE_API_KEY` environment variable,
or the default baked into `nim.py`); the frontend never sends or stores a key.

## Run it

```sh
# 1) build the renderer once (takes ~2-4 min first time)
cd renderer && cargo build --release

# 2) install + run the backend
cd ../backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
./.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8787
```

Then open **http://127.0.0.1:8787**, paste an NVIDIA API key + source text,
**Generate timestamped script**, review/edit the timeline, and **Generate video**.

> TTS voices live under `~/Documents/sun-vanish/models/` (Kokoro). If they are elsewhere,
> point the backend at them — see `jobs._models()`.

### API
- `POST /api/script` `{source_text, provider?, model?, base_url?, target_seconds, tone}` → timestamped scene script (LLM key is server-side)
- `POST /api/rewrite-scene` `{scene, context[], instruction, provider?, model?, base_url?}` → one scene re-written
- `POST /api/estimate` `{scenes}` → recompute `start`/`duration` from narration word counts
- `POST /api/jobs` `{script, options}` → starts the pipeline
- `GET /api/jobs/{id}` → status + per-stage seconds
- `GET /api/jobs/{id}/video` · `/srt` · `/scenes.json` → outputs

> Set the server-side LLM key without editing code: `export REELFORGE_API_KEY=sk-…` before starting
> the backend. The default provider is `in2peta-1` (`deepseek-v4p1-flash`), with `nvidia`
> (`minimaxai/minimax-m3.1-flash`) as a secondary preset.

## How fast is it? (measured on an Apple M-series, macOS)

### The original `youtube-2min` pipeline (a 2-min, 18-scene explainer)

| Stage | Wall time | Note |
|---|---|---|
| LLM / agent script writing | minutes | the dominant cost (agent loop + codegen) |
| Kokoro TTS, 31 sentences → 118 s of speech | ~30 s | sequential: ~0.96 s/sentence |
| Whisper base.en word alignment | ~6 s | whole 118 s track |
| Procedural music + 9 SFX (`audio_gen.py`) | **29.3 s** | Python-loop IIR biquads over 5.76M samples |
| cargo cold build | 1 m 42 s | one-time |
| fframes render 3590 frames, 1080p30 | **17.8 s** | Skia/Metal |
| **Full clean run** | **≈ 4–5 min** | dominated by script-writing + codegen rebuilds |

### ReelForge (every render is just data — no rebuild per video)

| Stage | 3-scene / ~17 s test | Scaled to 2 min (est.) |
|---|---|---|
| Kokoro TTS (workers=4) | 2.8 s | ~12–15 s |
| Whisper alignment | 0.8 s | ~6 s |
| Music + SFX (vectorized) | **0.07 s** | 0.2 s |
| Render | 2.6 s | ~18 s |
| **End-to-end** | **6.2 s** | **~35–45 s** |

The 29 s music step became **0.07 s** by replacing the Python sample loop with
`scipy.signal.lfilter` (compiled IIR). Render is unchanged (already GPU-fast).

## Parallelizing & optimizing — what's done and what you can do

Already in the app:
- **TTS across scenes** runs in a thread pool (`tts_workers`). Kokoro is an ONNX session whose
  `run()` releases the GIL, so threads give real multi-core speedup; the option is exposed in the UI.
- **Audio generation is vectorized** — the entire music bed + 9 SFX finish in < 0.1 s
  (was ~29 s of Python loops).
- **Zero recompiles per video** — the scenes-json contract replaced Rust codegen, so the
  1 m 42 s cargo build happens once, not per video.
- **Render uses max-performance Skia/Metal** with `h264_videotoolbox` (hardware H.264).

Tunables / further ideas:
- **Two staged TTS**: pre-synthesize non-timing phrases once, cache the voice for reuse across videos.
- **Bigger defaults**: `tts_workers` up to cores; whisper `base.en` → `small.en` only if accuracy matters (slower).
- **Parallel scenes (later stage)**: feed NIM a batched JSON to script scenes in one call (already one call) —
  the bigger win is *rewriting* many scenes in parallel if the API supports concurrency.
- **GPU render shared with TTS**: if you add a GPU whisper/TTS, run those on the GPU too; the render
  already pins the Metal context.
- **LRU voice cache / bigger Whisper batch**: reuse a temp dir per video length; skip re-filtering music
  when the bed can be looped and ducked instead of freshly regenerated.
- For long videos: chunk into **2-min segments**, render each in its own pipeline worker, then
  concatenate (fframes has `concatenator` + the backend can `ffmpeg -f concat`).

Style safety-net: headings/items are auto-wrap + shrink-to-fit in the renderer, and
`script.py` caps heading length and item counts from the LLM so generated copy can't overflow a scene.

## Example job

`python3 demo_gen.py demo-job` writes a self-contained job (scenes.json + audio + font) that you can
hand-render with `./renderer/target/release/reel-renderer --job demo-job render`.