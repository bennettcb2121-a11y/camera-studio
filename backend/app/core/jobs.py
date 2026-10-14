"""Tiny background job queue (thread pool) with progress, used for exports, video
renders, mockup ZIPs and AI best-of-N."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    cancel_requested: bool = False
    live: dict | None = None  # latest intermediate state (e.g. settings the AI just applied)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "progress": round(self.progress, 4),
                "message": self.message, "result": self.result, "live": self.live, "error": self.error, "created": self.created,
                "finished": self.finished}


class JobQueue:
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ccs-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[["JobContext"], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn):
        job.status = "running"
        ctx = JobContext(job)
        try:
            job.result = fn(ctx)
            job.status = "cancelled" if job.cancel_requested else "done"
            job.progress = 1.0
        except Exception as e:  # report, never crash the worker
            job.status = "error"
            job.error = f"{e}\n{traceback.format_exc()[-1500:]}"
        finally:
            job.finished = time.time()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        j = self._jobs.get(job_id)
        if not j:
            return False
        j.cancel_requested = True
        return True

    def list(self, limit: int = 50) -> list[dict]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)[:limit]
        return [j.to_dict() for j in jobs]

    def wait(self, job_id: str, timeout: float = 600) -> Job:
        """Block until a job finishes (used by tests)."""
        t0 = time.time()
        j = self._jobs[job_id]
        while j.status in ("queued", "running") and time.time() - t0 < timeout:
            time.sleep(0.05)
        return j


class JobContext:
    def __init__(self, job: Job):
        self.job = job

    def progress(self, frac: float, message: str = "") -> None:
        self.job.progress = max(0.0, min(1.0, float(frac)))
        if message:
            self.job.message = message

    @property
    def cancelled(self) -> bool:
        return self.job.cancel_requested


queue = JobQueue()
