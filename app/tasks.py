"""Tiny background job runner so long work (searching, crawling, sending) doesn't block the page."""
from __future__ import annotations

import itertools
import threading
import traceback
from datetime import datetime
from typing import Callable, Dict, List, Optional


class Job:
    def __init__(self, job_id: int, kind: str, title: str):
        self.id = job_id
        self.kind = kind
        self.title = title
        self.state = "running"
        self.lines: List[str] = []
        self.done = 0
        self.total = 0
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.started = datetime.now()
        self.finished: Optional[datetime] = None

    def log(self, line: str) -> None:
        self.lines.append(f"{datetime.now():%H:%M:%S}  {line}")
        del self.lines[:-500]

    def progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total

    @property
    def running(self) -> bool:
        return self.state == "running"


_jobs: Dict[int, Job] = {}
_ids = itertools.count(1)
_lock = threading.Lock()


def running(kind: Optional[str] = None) -> Optional[Job]:
    with _lock:
        for job in _jobs.values():
            if job.running and (kind is None or job.kind == kind):
                return job
    return None


def get(job_id: int) -> Optional[Job]:
    return _jobs.get(job_id)


def recent(limit: int = 5) -> List[Job]:
    return sorted(_jobs.values(), key=lambda j: j.id, reverse=True)[:limit]


def start(kind: str, title: str, fn: Callable[[Job], Optional[dict]]) -> Job:
    """Run fn(job) in a thread. Only one job per kind at a time: returns the running one instead."""
    with _lock:
        for job in _jobs.values():
            if job.running and job.kind == kind:
                return job
        job = Job(next(_ids), kind, title)
        _jobs[job.id] = job

    def run() -> None:
        try:
            job.result = fn(job) or {}
            job.state = "done"
        except Exception as exc:
            job.error = str(exc)
            job.log("❌ " + str(exc))
            job.log(traceback.format_exc().splitlines()[-1])
            job.state = "failed"
        finally:
            job.finished = datetime.now()

    threading.Thread(target=run, name=f"job-{job.id}", daemon=True).start()
    return job
