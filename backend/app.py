"""ReelForge backend — FastAPI.

Flow:
  POST /api/script        source text -> timestamped scene script (via NVIDIA NIM)
  POST /api/rewrite-scene one scene rewrite (NIM)
  POST /api/jobs          launch the TTS -> align -> reflow -> music -> render pipeline
  GET  /api/jobs/{id}     status + per-stage timings (polled by the UI)
  GET  /api/jobs/{id}/video, /srt, /scenes.json  outputs

The NIM API key is passed by the *client* on every LLM request and never stored.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import nim
import script as S
import jobs as J
import render as R
import projects as P
import webinfo as W

BACKEND = Path(__file__).resolve().parent
FRONTEND = BACKEND.parent / "frontend"

app = FastAPI(title="ReelForge")

# Rescan project folders so history survives restarts.
P.rebuild_index()


# Always return JSON for errors (Starlette's default 500 is plain text, which the
# browser can't parse as JSON — that produced "Unexpected token 'I'").
@app.exception_handler(Exception)
async def unhandled_exc(_request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


@app.exception_handler(RequestValidationError)
async def validation_exc(_request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "invalid request body"})


class ScriptRequest(BaseModel):
    prompt: str | None = None          # natural-language request, e.g. "explain YouTube in ~300s"
    source_text: str | None = None     # optional reference material to enrich from
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    target_seconds: int = 120
    tone: str = "clear, engaging, neutral-explain"
    voice_lang: str | None = None


class RewriteRequest(BaseModel):
    scene: dict
    context: list[dict] = []
    instruction: str
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None


class EstimateRequest(BaseModel):
    scenes: list[dict]


class JobRequest(BaseModel):
    script: dict
    options: dict = Field(default_factory=dict)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "renderer": R.renderer_ready(),
        "providers": nim.PROVIDERS,
        "default_provider": "in2peta-1",
        "projects": len(P.list_projects()),
        "models_version": "kokoro + whisper base.en",
        "frontend": FRONTEND.is_dir(),
    }


# --- history / saved projects -------------------------------------------------

@app.get("/api/projects")
def project_list():
    return P.list_projects()


@app.get("/api/projects/{pid}")
def project_get(pid: str):
    m = P.get_project(pid)
    if not m:
        raise HTTPException(404, "project not found")
    return m


@app.get("/api/projects/{pid}/video")
def project_video(pid: str):
    f = P.PROJECTS_ROOT / pid / "out.mp4"
    if not f.is_file():
        raise HTTPException(404, "video not ready")
    return FileResponse(f, media_type="video/mp4", filename=f"{pid}.mp4")


@app.get("/api/projects/{pid}/srt")
def project_srt(pid: str):
    f = P.PROJECTS_ROOT / pid / "subs.srt"
    if not f.is_file():
        raise HTTPException(404, "no srt")
    return FileResponse(f, media_type="text/plain", filename=f"{pid}.srt")


@app.get("/api/projects/{pid}/scenes.json")
def project_scenes(pid: str):
    f = P.PROJECTS_ROOT / pid / "scenes.json"
    if not f.is_file():
        raise HTTPException(404, "no scenes")
    return FileResponse(f, media_type="application/json")


def _detect_target(text: str | None, fallback: int) -> int:
    """Pull a duration out of natural language like '300 sec', '5 min', '90 seconds'."""
    if not text:
        return fallback
    m = re.search(r"(\d+)\s*(secs?|seconds?|mins?|minutes?)", text, re.I)
    if not m:
        return fallback
    n = int(m.group(1))
    unit = m.group(2).lower()[0]  # 's' or 'm'
    secs = n * 60 if unit == "m" else n
    return min(1800, max(10, secs))


class ResearchRequest(BaseModel):
    idea: str
    style: str = "technical"          # technical | architectural | educational | marketing | storytelling
    target_seconds: int = 120
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None


@app.post("/api/research")
def research(req: ResearchRequest):
    """Idea -> (web research) -> detailed markdown source text the user can validate."""
    idea = (req.idea or "").strip()
    if not idea:
        raise HTTPException(400, "Provide an idea.")
    target = _detect_target(idea, req.target_seconds)
    model, base_url = nim.resolve_provider(req.provider, req.model, req.base_url)
    # gather optional live web info (network failures are tolerated)
    try:
        sources = W.research(idea, 8)
    except Exception:
        sources = []
    t0 = time.time()
    try:
        text, words = nim.generate_writeup(
            idea, style=req.style, research=sources,
            target_seconds=target, model=model, base_url=base_url,
        )
    except nim.NimError as e:
        raise HTTPException(502, f"LLM /api/research ({model}): {e}") from e
    return {
        "source_text": text,
        "words": words,
        "sources": [label for label, _ in sources],
        "source_count": len(sources),
        "style": req.style,
        "target_seconds": target,
        "llm_ms": round((time.time() - t0) * 1000),
    }


@app.post("/api/script")
def make_script(req: ScriptRequest):
    prompt = (req.prompt or "").strip()
    source = (req.source_text or "").strip()
    if not prompt and not source:
        raise HTTPException(400, "Provide a prompt (or source text).")
    target = _detect_target(prompt, req.target_seconds)
    model, base_url = nim.resolve_provider(req.provider, req.model, req.base_url)
    t0 = time.time()
    try:
        obj = nim.generate_script(
            source_text=source, prompt=prompt or None,
            model=model, base_url=base_url,
            target_seconds=target, tone=req.tone, voice_lang=req.voice_lang,
        )
    except nim.NimError as e:
        raise HTTPException(502, f"LLM /api/script ({model}): {e}") from e
    script_dict = S.clean_script(obj)
    S.estimate_timings(script_dict["scenes"])
    from_prompt = bool(prompt and re.search(r"(\d+)\s*(secs?|seconds?|mins?|minutes?)", prompt, re.I))
    return {
        "script": script_dict,
        "llm_ms": round((time.time() - t0) * 1000),
        "model": model,
        "target_seconds": target,
        "duration_from_prompt": from_prompt,
        "estimated_total": round(script_dict["scenes"][-1]["start"] + script_dict["scenes"][-1]["duration"], 2),
    }


@app.post("/api/rewrite-scene")
def rewrite(req: RewriteRequest):
    model, base_url = nim.resolve_provider(req.provider, req.model, req.base_url)
    context_lines = "\n".join(
        f"[{i + 1}] {c.get('heading', '')}" for i, c in enumerate(req.context)
    )
    try:
        out = nim.rewrite_scene(
            req.scene, req.instruction, context_lines, model=model, base_url=base_url,
        )
    except nim.NimError as e:
        raise HTTPException(502, f"LLM /api/rewrite-scene ({model}): {e}") from e
    clean = S.clean_scene({**req.scene, **out}, 0)
    return {"scene": clean}


@app.post("/api/estimate")
def estimate(req: EstimateRequest):
    scenes = S.estimate_timings([dict(s) for s in req.scenes])
    total = scenes[-1]["start"] + scenes[-1]["duration"] if scenes else 0.0
    return {"scenes": scenes, "total": round(total, 2)}


@app.post("/api/jobs")
def create_job(req: JobRequest):
    job = J.create_job(req.script, req.options)
    threading.Thread(target=J.run_pipeline, args=(job, req.options), daemon=True).start()
    return {"id": job.id, "status": job.status}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = J.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: str):
    job = J.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    mp4 = job.dir / "out.mp4"
    if not mp4.is_file():
        raise HTTPException(404, "video not ready")
    return FileResponse(mp4, media_type="video/mp4", filename=f"{job.title or job.id}.mp4")


@app.get("/api/jobs/{job_id}/srt")
def job_srt(job_id: str):
    job = J.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    srt = job.dir / "subs.srt"
    if not srt.is_file():
        raise HTTPException(404, "no srt")
    return FileResponse(srt, media_type="text/plain", filename=f"{job.title or job.id}.srt")


@app.get("/api/jobs/{job_id}/scenes.json")
def job_scenes(job_id: str):
    job = J.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    f = job.dir / "scenes.json"
    if not f.is_file():
        raise HTTPException(404, "not staged yet")
    return FileResponse(f, media_type="application/json")


if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787)