"""Background job runner for register / mint / import."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

REG_DIR = Path(__file__).resolve().parents[1]
VENV_PY = REG_DIR / ".venv" / "bin" / "python"
PYTHON = str(VENV_PY if VENV_PY.exists() else sys.executable)


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued|running|done|error|cancelled
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    command: List[str] = field(default_factory=list)
    cwd: str = str(REG_DIR)
    returncode: Optional[int] = None
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    logs: Deque[str] = field(default_factory=lambda: deque(maxlen=2000))
    _proc: Any = field(default=None, repr=False)

    def to_dict(self, with_logs: bool = False, log_tail: int = 200) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "command": self.command,
            "cwd": self.cwd,
            "returncode": self.returncode,
            "error": self.error,
            "meta": self.meta,
            "log_lines": len(self.logs),
        }
        if with_logs:
            lines = list(self.logs)
            d["logs"] = lines[-log_tail:]
        return d


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, Job] = {}

    def list_jobs(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.to_dict(with_logs=False) for j in jobs[:limit]]

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _register(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.id] = job
        return job

    def _append(self, job: Job, line: str) -> None:
        job.logs.append(line.rstrip("\n"))

    def _run_subprocess(self, job: Job) -> None:
        job.status = "running"
        job.started_at = time.time()
        self._append(job, f"$ {' '.join(job.command)}")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        extra_env = (job.meta or {}).get("_env") or {}
        if isinstance(extra_env, dict):
            env.update({str(k): str(v) for k, v in extra_env.items()})
            hide = extra_env.get("GROK_BROWSER_HIDE_MODE")
            if hide:
                self._append(job, f"[env] GROK_BROWSER_HIDE_MODE={hide}")
        try:
            proc = subprocess.Popen(
                job.command,
                cwd=job.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            job._proc = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                self._append(job, line)
            proc.wait()
            job.returncode = proc.returncode
            if job.status == "cancelled":
                self._append(job, "[job cancelled]")
            elif proc.returncode == 0:
                job.status = "done"
                self._append(job, f"[done exit={proc.returncode}]")
            else:
                job.status = "error"
                job.error = f"exit {proc.returncode}"
                self._append(job, f"[error exit={proc.returncode}]")
        except Exception as exc:
            job.status = "error"
            job.error = str(exc)
            self._append(job, f"[exception] {exc}")
        finally:
            job.finished_at = time.time()
            job._proc = None

    def start_command(
        self,
        kind: str,
        command: List[str],
        meta: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Job:
        meta = dict(meta or {})
        if env:
            meta["_env"] = dict(env)
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            command=command,
            meta=meta,
        )
        self._register(job)
        t = threading.Thread(target=self._run_subprocess, args=(job,), daemon=True)
        t.start()
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        if job.status not in ("queued", "running"):
            return False
        job.status = "cancelled"
        if job._proc:
            try:
                job._proc.terminate()
            except Exception:
                pass
        return True

    # ---- high-level actions ----

    def start_register(self, count: int = 1, browser_hide_mode: Optional[str] = None) -> Job:
        count = max(1, min(int(count), 100))
        cmd = [PYTHON, str(REG_DIR / "grok_register_ttk.py"), "cli", str(count)]
        env = None
        mode = None
        if browser_hide_mode:
            mode = str(browser_hide_mode).strip().lower()
            if mode in ("min", "minimized", "minimise"):
                mode = "minimize"
            if mode in ("virtual", "virtual-display", "xvfb-run"):
                mode = "xvfb"
            if mode in ("normal", "minimize", "xvfb"):
                env = {"GROK_BROWSER_HIDE_MODE": mode}
            else:
                mode = None
        return self.start_command(
            "register",
            cmd,
            meta={"count": count, "browser_hide_mode": mode},
            env=env,
        )

    def start_mint(
        self,
        limit: int = 5,
        sleep: float = 15.0,
        save_cpa: bool = False,
        accounts_file: Optional[str] = None,
    ) -> Job:
        limit = max(1, min(int(limit), 100))
        sleep = max(0.0, float(sleep))
        cmd = [
            PYTHON,
            str(REG_DIR / "auto_import_grok_cli.py"),
            "--limit",
            str(limit),
            "--sleep",
            str(sleep),
        ]
        if save_cpa:
            cmd.append("--save-cpa")
        meta = {"limit": limit, "sleep": sleep, "save_cpa": save_cpa}
        if accounts_file:
            meta["accounts_file"] = accounts_file
        return self.start_command("mint", cmd, meta=meta)

    def start_import_cpa(self) -> Job:
        cmd = [PYTHON, str(REG_DIR / "import_to_vps_db.py")]
        return self.start_command("import_cpa", cmd, meta={})

    def start_probe(
        self,
        provider: str = "grok-cli",
        mode: str = "auto",
        live: bool = True,
        sleep_sec: float = 1.5,
        limit: Optional[int] = None,
        ids: Optional[List[str]] = None,
    ) -> Job:
        """Background probe that updates 9Router testStatus (unknown→active/quota/etc)."""
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind="probe",
            command=["internal:probe"],
            meta={
                "provider": provider,
                "mode": mode,
                "live": live,
                "sleep": sleep_sec,
                "limit": limit,
                "ids": ids or [],
            },
        )
        self._register(job)

        def runner() -> None:
            from dashboard.probe import probe_batch

            job.status = "running"
            job.started_at = time.time()
            self._append(
                job,
                f"$ probe provider={provider} mode={mode} live={live} sleep={sleep_sec} limit={limit}",
            )
            try:
                summary = probe_batch(
                    provider=provider,
                    mode=mode,
                    ids=ids,
                    live=live,
                    sleep_sec=sleep_sec,
                    limit=limit,
                    log=lambda m: self._append(job, m),
                    should_cancel=lambda: job.status == "cancelled",
                )
                job.meta["summary"] = {
                    k: summary.get(k)
                    for k in ("targeted", "scanned", "counts", "provider", "mode", "live")
                }
                if job.status == "cancelled":
                    self._append(job, "[probe cancelled]")
                else:
                    job.status = "done"
                    c = summary.get("counts") or {}
                    self._append(
                        job,
                        f"[done] scanned={summary.get('scanned')} active={c.get('active',0)} "
                        f"quota={c.get('quota_exhausted',0)} revoked={c.get('token_invalid_revoked',0)} "
                        f"error={c.get('error',0)}",
                    )
            except Exception as exc:
                job.status = "error"
                job.error = str(exc)
                self._append(job, f"[exception] {exc}")
            finally:
                job.finished_at = time.time()

        threading.Thread(target=runner, daemon=True).start()
        return job


jobs = JobManager()
