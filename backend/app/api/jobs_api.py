from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..core import jobs

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs():
    return {"jobs": jobs.queue.list()}


@router.get("/{job_id}")
def get_job(job_id: str):
    j = jobs.queue.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j.to_dict()


@router.post("/{job_id}/cancel")
def cancel(job_id: str):
    if not jobs.queue.cancel(job_id):
        raise HTTPException(404, "job not found")
    return {"ok": True}
