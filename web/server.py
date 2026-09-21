"""A web surface for this library, so lit-storm can frame it in a tab.

Upstream is a library and a CLI: there is nothing to open in a browser. This
adds the smallest thing that can be — start a research, watch it work, read
the report — and nothing else. Providers, keys and search stay where the
library already reads them from, the environment.

Kept in `web/` rather than inside `deep_researcher/` so it is obvious which
code is ours and which is upstream's, and so a merge from upstream cannot
collide with it.

The three query parameters on `/` are the contract lit-storm's other framed
sibling already honours: `embed=1` hides this page's own language and theme
controls, which would otherwise sit under the host app's and announce a
second application; `lang` and `theme` make the frame match the page around
it.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

STATIC = Path(__file__).parent / "static"

# Runs live in memory. A restart loses them, which is the right trade for a
# sibling whose reports are read once and copied out: persistence would mean
# a volume, a schema and a retention question, none of which this needs yet.
# The cap stops a forgotten tab filling the container's memory.
MAX_RUNS_KEPT = 50

app = FastAPI(title="Deep Research", docs_url=None, redoc_url=None)


@dataclass
class Run:
    """One research, and everything a client needs to follow it."""

    id: str
    query: str
    mode: str
    status: Literal["running", "done", "failed"] = "running"
    progress: list[str] = field(default_factory=list)
    report: str = ""
    error: str = ""
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None

    @property
    def seconds(self) -> float:
        return (self.finished or time.monotonic()) - self.started

    def as_json(self) -> dict:
        return {
            "id": self.id,
            "query": self.query,
            "mode": self.mode,
            "status": self.status,
            "progress": self.progress,
            "report": self.report,
            "error": self.error,
            "seconds": round(self.seconds, 1),
        }


RUNS: dict[str, Run] = {}


def _researcher_classes():
    """Imported late: llm_config reads the environment at import time.

    Importing at module load would freeze whatever the environment held when
    uvicorn started, before this module had a chance to be told otherwise.
    """
    from deep_researcher import DeepResearcher, IterativeResearcher

    return DeepResearcher, IterativeResearcher


def _with_progress(cls, run: Run):
    """A researcher that reports to `run` instead of to stdout.

    `_log_message` is the one place both researchers announce what they are
    doing, and it is a method, so overriding it is enough — no change to
    upstream, and nothing to re-apply when upstream moves.
    """

    class Reporting(cls):  # type: ignore[misc, valid-type]
        def _log_message(self, message: str) -> None:
            for line in str(message).splitlines():
                line = line.strip()
                if line:
                    run.progress.append(line)
            # Keep the tail bounded: the deep mode is chatty and a client
            # polls the whole list.
            if len(run.progress) > 400:
                del run.progress[:-400]

    return Reporting


async def _execute(run: Run, iterations: int, minutes: int) -> None:
    deep_cls, iterative_cls = _researcher_classes()
    base = deep_cls if run.mode == "deep" else iterative_cls
    researcher = _with_progress(base, run)(
        max_iterations=iterations, max_time_minutes=minutes, verbose=True
    )
    try:
        run.report = await researcher.run(run.query)
        run.status = "done"
    except Exception as error:  # noqa: BLE001 - any failure ends the run
        run.status = "failed"
        # The class name matters: the library's own errors read as plain
        # sentences and would otherwise be indistinguishable from a message
        # this server wrote.
        run.error = f"{type(error).__name__}: {error}"
    finally:
        run.finished = time.monotonic()


class StartRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    mode: Literal["simple", "deep"] = "simple"
    iterations: int = Field(default=2, ge=1, le=10)
    minutes: int = Field(default=5, ge=1, le=30)


@app.post("/api/runs")
async def start(request: StartRequest) -> JSONResponse:
    if not os.getenv("OPENROUTER_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        # Said here rather than 500 pages later: without a key the first
        # agent fails with a provider error that reads like a bug.
        raise HTTPException(503, "No model key is configured for this service.")

    run = Run(id=uuid.uuid4().hex[:12], query=request.query.strip(), mode=request.mode)
    RUNS[run.id] = run
    for old in sorted(RUNS.values(), key=lambda r: r.started)[:-MAX_RUNS_KEPT]:
        RUNS.pop(old.id, None)

    asyncio.create_task(_execute(run, request.iterations, request.minutes))
    return JSONResponse({"id": run.id}, status_code=202)


@app.get("/api/runs/{run_id}")
async def poll(run_id: str) -> JSONResponse:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "No such run.")
    return JSONResponse(run.as_json())


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness, and what the host app asks before it frames this.

    It reports whether a key is present as well as whether the process is
    up: a sibling that is running but cannot call a model is not ready, and
    the tab that frames it would rather say so than show a working page that
    fails on the first button.
    """
    return JSONResponse(
        {
            "status": "ok",
            "model_key": bool(
                os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
            ),
            "search": os.getenv("SEARCH_PROVIDER", "serper"),
            "running": sum(1 for r in RUNS.values() if r.status == "running"),
        }
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
