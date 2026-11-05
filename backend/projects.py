"""Persistent project/history store.

Every finished generation is archived into `backend/projects/<id>-<slug>/` holding the
video (`out.mp4`), the exact timestamped `scenes.json`, the `.srt` and a `project.json`
metadata record. A top-level `index.json` keeps the list across server restarts, so
history survives reboots (the in-memory job store does not).

Folders are the source of truth; `rebuild_index()` rescans them at startup in case the
index was lost or projects were added by hand.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

PROJECTS_ROOT = Path(__file__).resolve().parent / "projects"
PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
INDEX_PATH = PROJECTS_ROOT / "index.json"


def _load_index() -> dict:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_index(idx: dict):
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=2))


def rebuild_index() -> dict:
    """Rescan project folders so the list matches disk (survives restarts / manual adds)."""
    idx = _load_index()
    changed = False
    for d in PROJECTS_ROOT.iterdir():
        if d.is_dir():
            mf = d / "project.json"
            if mf.exists():
                try:
                    meta = json.loads(mf.read_text())
                    meta.setdefault("dir", d.name)
                    if idx.get(d.name) != meta:
                        idx[d.name] = meta
                        changed = True
                except Exception:
                    pass
    if changed:
        _save_index(idx)
    return idx


def _row(meta: dict) -> dict:
    pid = meta.get("id") or meta.get("dir") or ""
    return {
        "id": pid,
        "title": meta.get("title", "Untitled"),
        "created_at": meta.get("created_at", ""),
        "total": meta.get("total"),
        "scene_count": meta.get("scene_count", len(meta.get("scenes") or [])),
        "options": meta.get("options", {}),
        "times": meta.get("times", {}),
        "scenes": meta.get("scenes", []),
        "video_url": f"/api/projects/{pid}/video",
        "srt": meta.get("srt") is not None,
        "srt_url": f"/api/projects/{pid}/srt" if meta.get("srt") else None,
        "scenes_url": f"/api/projects/{pid}/scenes.json",
    }


def list_projects() -> list:
    items = [_row(meta) for meta in _load_index().values()]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


def get_project(pid: str):
    meta = _load_index().get(pid)
    return _row(meta) if meta else None


def archive(job) -> str | None:
    """Move a finished job directory into projects/ and index it. Returns the project id."""
    if not job.dir.exists():
        return None
    slug = re.sub(r"[^A-Za-z0-9]+", "-", job.title).strip("-")[:40] or "video"
    pid = f"{job.id}-{slug}"
    dest = PROJECTS_ROOT / pid
    if not dest.exists():
        job.dir.rename(dest)
        job.dir = dest
    else:
        dest = job.dir
        pid = job.dir.name

    meta = {
        "id": pid,
        "dir": pid,
        "title": job.title,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(job.ended_at or time.time())),
        "total": job.plan.get("total"),
        "options": job.plan.get("options", {}),
        "times": job.times,
        "scenes": job.plan.get("scenes", []),
        "scene_count": len(job.plan.get("scenes", [])),
        "srt": (dest / "subs.srt").is_file(),
    }
    (dest / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    shutil.rmtree(dest / "tmp", ignore_errors=True)

    idx = _load_index()
    idx[pid] = meta
    _save_index(idx)
    return pid