"""Job model + the end-to-end pipeline, run in a background thread.

Each job records per-stage wall times so the frontend can show where time goes
(and for you to see the effect of parallelism/vectorization). A global lock
serializes pipelines because rendering owns the GPU and the TTS/ASR models are
heavy singletons.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import script as S
import audio as A
import render as R
import projects as P

from nim import NimError
from script import clean_script

LOCAL_VOICES = {
    "daniel": {"voice": "bm_daniel", "lang": "en-gb"},
    "koala": {"voice": "af_koala", "lang": "en-us"},
    "pixie": {"voice": "af_pixie", "lang": "en-us"},
    "sky": {"voice": "am_sky", "lang": "en-us"},
    "william": {"voice": "bm_william", "lang": "en-gb"},
}

JOBS_ROOT = Path(__file__).resolve().parent / "jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()
_jobs: dict[str, "Job"] = {}


@dataclass
class Job:
    id: str
    title: str
    status: str = "queued"            # queued|running|done|error
    stage: str = ""
    error: str = ""
    log: list[str] = field(default_factory=list)
    times: dict[str, float] = field(default_factory=dict)   # stage -> seconds
    started_at: float = 0.0
    ended_at: float = 0.0
    video_url: str = ""
    srt: str = ""
    plan: dict = field(default_factory=dict)
    dir: Path = field(default_factory=Path)
    palette: list = field(default_factory=list)

    def to_dict(self):
        return {
            "id": self.id, "title": self.title, "status": self.status,
            "stage": self.stage, "error": self.error,
            "log": self.log[-200:], "times": self.times,
            "elapsed": round((self.ended_at or time.time()) - (self.started_at or time.time()), 2),
            "done": self.status in ("done", "error"),
            "video_url": self.video_url, "plan": self.plan,
        }


def _models():
    """Lazily load TTS + ASR once; cached across jobs."""
    global _kokoro, _whisper
    if _kokoro is None:
        import kokoro_onnx
        from faster_whisper import WhisperModel
        # override the default paths as with the youtube-2min workspace
        import os as _os
        _os.environ.setdefault("KOKORO_MODEL", str(Path.home() / "Documents/sun-vanish/models/kokoro-v1.0.onnx"))
        m = _os.environ.get("KOKORO_MODEL")
        v = _os.environ.get("KOKORO_VOICES", str(Path.home() / "Documents/sun-vanish/models/voices-v1.0.bin"))
        _kokoro = kokoro_onnx.Kokoro(m, v)
        _whisper = WhisperModel("base.en", device="cpu", compute_type="int8")
    return _kokoro, _whisper


_kokoro = None
_whisper = None


def create_job(script_dict: dict, options: dict) -> Job:
    script_dict = clean_script(script_dict)
    jid = uuid.uuid4().hex[:10]
    job = Job(
        id=jid,
        title=script_dict["title"],
        dir=JOBS_ROOT / jid,
        plan={"options": options, "scenes": script_dict["scenes"]},
        palette=options.get("palette", []),
    )
    job.dir.mkdir(parents=True, exist_ok=True)
    _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def _log(job: Job, line: str):
    job.log.append(line)


def run_pipeline(job: Job, options: dict):
    def timed(name: str, fn):
        t0 = time.time()
        job.stage = name
        _log(job, f"[{name}] start")
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001
            job.times[name] = round(time.time() - t0, 2)
            raise
        job.times[name] = round(time.time() - t0, 2)
        _log(job, f"[{name}] done in {job.times[name]:.2f}s")
        return r

    with _lock:
        job.status = "running"
        job.started_at = time.time()
        try:
            script_dict = job.plan["scenes"]        # list of scenes
            style = options.get("style", "ocean")
            theme = S.make_theme(style, options.get("accent"))
            voice_cfg = LOCAL_VOICES.get(options.get("voice", "daniel"), LOCAL_VOICES["daniel"])
            speed = float(options.get("speed", 0.96))
            workers = int(options.get("tts_workers", 4) or 4)
            sync = options.get("sync", "fit")
            music_on = bool(options.get("music", True))
            bpm = int(options.get("bpm", 96))

            # 1) estimate a first-pass timeline so the plan is visible up front
            est = S.estimate_timings([dict(s) for s in script_dict])
            job.plan["estimated_total"] = round(est[-1]["start"] + est[-1]["duration"], 2) if est else 0.0

            kokoro, whisper = _models()

            # 2) TTS — parallel across scenes
            def _tts():
                voice, starts, durs = A.assemble_voice(
                    script_dict, kokoro, voice=voice_cfg["voice"], speed=speed,
                    lang=voice_cfg["lang"], workers=workers,
                )
                return voice, starts, durs
            voice, _, _ = timed("tts", _tts)
            voice = timed("normalize", lambda: A.normalize_voice(voice))

            # 3) whisper alignment
            raw_words = timed("align", lambda: A.align_words(voice, whisper))

            # 4) reflow timestamps so scenes/captions match the real audio
            def _reflow():
                if sync == "strict":
                    _scenes, total = S.reflow_strict(script_dict, raw_words)
                else:
                    _scenes, total = S.reflow_fit(script_dict, raw_words)
                return _scenes, total
            scenes, total = timed("reflow", _reflow)
            subtitle_words_scope = [dict(w) for w in raw_words]
            subtitles = {"words": subtitle_words_scope}
            srt_text = timed("srt", lambda: S.srt_from_words(scenes, raw_words))

            # 5) voice file (word-aligned already) + music (vectorized) + sfx
            timed("write_audio", lambda: _write_audio(job, voice, total, music_on, bpm))

            # 6) stage the job dir (scenes.json, fonts)
            timed("stage", lambda: _stage(job, script_dict, scenes, subtitles, total, theme, music_on))
            job.plan["scenes"] = scenes
            job.plan["total"] = total

            # 7) render via prebuilt binary
            def _render():
                studio = {"fit": f"timeline synced to voice ({total:.1f}s)"}
                _log(job, json.dumps(studio))
                return R.render(job.dir, on_line=lambda ln: _log(job, ln))
            out = timed("render", _render)

            # 8) finalize
            job.video_url = f"/api/jobs/{job.id}/video"
            if srt_text:
                (job.dir / "subs.srt").write_text(srt_text)
                job.srt = f"/api/jobs/{job.id}/srt"
            size_mb = round(out.stat().st_size / 1e6, 1)
            _log(job, f"[done] {out.name}: {size_mb} MB, {total:.1f}s")
            total_secs = round(sum(job.times.values()), 2)
            job.times["total"] = total_secs
            job.status = "done"
            # persist this generation into the project/history store
            try:
                pid = P.archive(job)
                job.plan["project_id"] = pid
                job.video_url = f"/api/projects/{pid}/video" if pid else job.video_url
            except Exception as e:  # noqa: BLE001
                _log(job, f"archive warning: {e}")
        except (NimError, RuntimeError, ValueError, OSError) as e:  # noqa: BLE001
            job.status = "error"
            job.error = str(e)
            _log(job, f"ERROR: {e}")
        except Exception as e:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(e).__name__}: {e}"
            _log(job, f"ERROR: {job.error}")
        finally:
            job.ended_at = time.time()


def _write_audio(job: Job, voice, total: float, music_on: bool, bpm: int):
    A.write_voice(voice, job.dir / "voice.wav")
    if music_on:
        bed = A.music_bed(total, bpm=bpm)
        import soundfile as sf
        sf.write(job.dir / "music.wav", bed, A.SR_MUSIC)
    names = sorted(A.SFX.keys())
    A.gen_sfx(names, str(job.dir))


def _stage(job: Job, script_dict, scenes, subtitles, total, theme, music_on):
    _ = script_dict
    full = {"title": job.title, "scenes": scenes}
    R.write_scenes_json(job.dir, full, subtitles, total, theme, music_on)
    R.stage_fonts(job.dir)