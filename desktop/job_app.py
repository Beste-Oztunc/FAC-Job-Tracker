#!/usr/bin/env python3
"""
Local browser application for FAC - Job Tracker.

Run:
    python job_app.py

Then use the browser window opened at:
    http://127.0.0.1:8765
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


PROJECT_FOLDER = Path(__file__).resolve().parent
FRONTEND_FOLDER = PROJECT_FOLDER / "frontend"
OUTPUT_FOLDER = PROJECT_FOLDER / "output"

CONFIG_FILE = OUTPUT_FOLDER / "app_config.json"
COLLECTOR_FILE = PROJECT_FOLDER / "collect_all_ats_app.py"
AGENT_FILE = PROJECT_FOLDER / "job_agent_app.py"
COMPANIES_FILE = PROJECT_FOLDER / "companies.py"
ENV_FILE = PROJECT_FOLDER / ".env"

JOB_DASHBOARD_FILE = (
    OUTPUT_FOLDER / "all_ats_job_dashboard.html"
)
INTELLIGENCE_FILE = OUTPUT_FOLDER / "job_intelligence.html"

HOST = "127.0.0.1"
PORT = 8765

OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="FAC - Job Tracker",
    docs_url=None,
    redoc_url=None,
)

app.mount(
    "/static",
    StaticFiles(directory=str(FRONTEND_FOLDER)),
    name="static",
)
app.mount(
    "/output",
    StaticFiles(directory=str(OUTPUT_FOLDER)),
    name="output",
)


class ConfigPayload(BaseModel):
    config: dict[str, Any]


class RunRequest(BaseModel):
    config: dict[str, Any]
    run_ai: bool = False
    refresh_ai: bool = False
    force_discovery: bool = False
    top_jobs: int = Field(default=15, ge=1, le=20)


class RunState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.thread: threading.Thread | None = None
        self.reset()

    def reset(self) -> None:
        self.running = False
        self.cancel_requested = False
        self.stage = "idle"
        self.stage_label = "Ready"
        self.progress = 0
        self.logs: list[str] = []
        self.error = ""
        self.started_at = ""
        self.finished_at = ""
        self.run_ai = False
        self.job_dashboard_ready = JOB_DASHBOARD_FILE.exists()
        self.intelligence_ready = INTELLIGENCE_FILE.exists()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "cancel_requested": self.cancel_requested,
                "stage": self.stage,
                "stage_label": self.stage_label,
                "progress": self.progress,
                "logs": self.logs[-250:],
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "run_ai": self.run_ai,
                "job_dashboard_ready": (
                    JOB_DASHBOARD_FILE.exists()
                ),
                "intelligence_ready": (
                    INTELLIGENCE_FILE.exists()
                ),
                "job_dashboard_url": (
                    "/output/all_ats_job_dashboard.html"
                    if JOB_DASHBOARD_FILE.exists()
                    else ""
                ),
                "intelligence_url": (
                    "/output/job_intelligence.html"
                    if INTELLIGENCE_FILE.exists()
                    else ""
                ),
            }


RUN_STATE = RunState()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}

    if not path.exists():
        return values

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for raw_line in lines:
        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key:
            values[key] = value

    return values


def ast_literal_assignments(
    path: Path,
    names: set[str],
) -> dict[str, Any]:
    try:
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
    except (OSError, SyntaxError):
        return {}

    values: dict[str, Any] = {}

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        targets = [
            target.id
            for target in node.targets
            if isinstance(target, ast.Name)
        ]

        matching = names.intersection(targets)

        if not matching:
            continue

        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue

        for name in matching:
            values[name] = value

    return values


def load_company_registry() -> list[dict[str, Any]]:
    namespace: dict[str, Any] = {
        "__name__": "company_registry_loader",
    }

    try:
        exec(
            compile(
                COMPANIES_FILE.read_text(encoding="utf-8"),
                str(COMPANIES_FILE),
                "exec",
            ),
            namespace,
        )
    except Exception as error:
        raise RuntimeError(
            f"Could not load companies.py: {error}"
        ) from error

    registry = namespace.get("COMPANY_REGISTRY", {})

    if not isinstance(registry, dict):
        return []

    return [
        {
            "name": name,
            "categories": metadata.get("categories", []),
            "priority": metadata.get("priority", 3),
            "candidate_token": metadata.get(
                "candidate_token",
                "",
            ),
        }
        for name, metadata in registry.items()
        if isinstance(metadata, dict)
    ]


COLLECTOR_RULE_NAMES = {
    "TARGET_ROLE_PHRASES",
    "TITLE_SPECIALTY_TERMS",
    "DOMAIN_TERMS",
    "CUSTOMER_FACING_TERMS",
    "EXCLUDED_TITLE_TERMS",
    "EXCLUDED_EXPERIENCE_LEVELS",
    "HARD_ENTRY_TITLE_TERMS",
    "LOCAL_AREA_TERMS",
    "REMOTE_TERMS",
    "HYBRID_TERMS",
    "ONSITE_TERMS",
    "MIN_POSSIBLE_SCORE",
    "MIN_STRONG_SCORE",
}


def default_config() -> dict[str, Any]:
    values = ast_literal_assignments(
        COLLECTOR_FILE,
        COLLECTOR_RULE_NAMES,
    )
    companies = load_company_registry()

    enabled = [
        item["name"]
        for item in companies
        if int(item.get("priority", 3)) <= 1
    ]

    def list_value(name: str) -> list[str]:
        value = values.get(name, [])

        if isinstance(value, set):
            value = sorted(value)

        return list(value) if isinstance(value, (list, tuple)) else []

    def dict_value(name: str) -> dict[str, int]:
        value = values.get(name, {})
        return dict(value) if isinstance(value, dict) else {}

    return {
        "version": 1,
        "profile": {
            "resume_text": "",
            "desired_job_text": (
                "Solutions Engineer, Solutions Consultant, "
                "Risk, Fraud, AI, Decisioning"
            ),
            "location_preference": (
                "Remote in the United States; Triangle-area "
                "hybrid roles are also welcome."
            ),
            "additional_notes": "",
        },
        "companies": {
            "enabled": enabled,
            "custom": [],
        },
        "sources": {
            "greenhouse": True,
            "ashby": True,
            "lever": True,
        },
        "rules": {
            "target_role_phrases": dict_value(
                "TARGET_ROLE_PHRASES"
            ),
            "title_specialty_terms": dict_value(
                "TITLE_SPECIALTY_TERMS"
            ),
            "domain_terms": dict_value("DOMAIN_TERMS"),
            "customer_facing_terms": dict_value(
                "CUSTOMER_FACING_TERMS"
            ),
            "excluded_title_terms": sorted(
                set(
                    list_value("EXCLUDED_TITLE_TERMS")
                    + list_value("HARD_ENTRY_TITLE_TERMS")
                ),
                key=str.casefold,
            ),
            "excluded_experience_levels": list_value(
                "EXCLUDED_EXPERIENCE_LEVELS"
            ),
            "hard_entry_title_terms": sorted(
                set(
                    list_value("EXCLUDED_TITLE_TERMS")
                    + list_value("HARD_ENTRY_TITLE_TERMS")
                ),
                key=str.casefold,
            ),
            "local_area_terms": list_value(
                "LOCAL_AREA_TERMS"
            ),
            "remote_terms": list_value("REMOTE_TERMS"),
            "hybrid_terms": list_value("HYBRID_TERMS"),
            "onsite_terms": list_value("ONSITE_TERMS"),
            "min_possible_score": int(
                values.get("MIN_POSSIBLE_SCORE", 50)
            ),
            "min_strong_score": int(
                values.get("MIN_STRONG_SCORE", 65)
            ),
        },
        "agent": {
            "model": "gpt-5-mini",
            "top_jobs": 15,
            "market_min_score": 60,
        },
    }


def merged_config() -> dict[str, Any]:
    defaults = default_config()
    saved = load_json(CONFIG_FILE, {})

    if not isinstance(saved, dict):
        return defaults

    # The frontend always sends complete configuration objects.
    # Preserve defaults only when the saved file is incomplete.
    for section in (
        "profile",
        "companies",
        "sources",
        "rules",
        "agent",
    ):
        if section not in saved:
            saved[section] = defaults[section]

    saved.setdefault("version", 1)
    return saved


def env_status(config: dict[str, Any]) -> dict[str, Any]:
    env_values = {
        **read_env_file(ENV_FILE),
        **{
            key: value
            for key, value in os.environ.items()
            if key.startswith("OPENAI_")
        },
    }
    configured = bool(
        env_values.get("OPENAI_API_KEY", "").strip()
    )
    model = (
        str(
            config.get("agent", {}).get(
                "model",
                "",
            )
        ).strip()
        or env_values.get("OPENAI_MODEL", "").strip()
        or "gpt-5-mini"
    )

    return {
        "configured": configured,
        "model": model,
        "storage": (
            ".env / environment variable"
            if configured
            else "Not configured"
        ),
    }


def add_log(line: str) -> None:
    clean = line.rstrip()

    if not clean:
        return

    with RUN_STATE.lock:
        RUN_STATE.logs.append(clean)

        if len(RUN_STATE.logs) > 1_000:
            RUN_STATE.logs = RUN_STATE.logs[-1_000:]


def set_stage(
    stage: str,
    label: str,
    progress: int,
) -> None:
    with RUN_STATE.lock:
        RUN_STATE.stage = stage
        RUN_STATE.stage_label = label
        RUN_STATE.progress = max(
            0,
            min(100, int(progress)),
        )


def parse_collector_progress(line: str) -> None:
    if "Collecting Greenhouse, Ashby, and Lever" in line:
        set_stage(
            "collecting",
            "Collecting ATS job boards",
            8,
        )
        return

    if "Collection and deduplication finished" in line:
        set_stage(
            "eligibility",
            "Removing ineligible jobs",
            36,
        )
        return

    if "Hard eligibility filtering finished" in line:
        set_stage(
            "scoring",
            "Scoring relevant U.S. jobs",
            42,
        )
        return

    match = re.search(
        r"Processed\s+(\d+)/(\d+)\s+jobs",
        line,
    )

    if match:
        current = int(match.group(1))
        total = max(1, int(match.group(2)))
        progress = 42 + round((current / total) * 24)
        set_stage(
            "scoring",
            f"Scoring jobs: {current:,} / {total:,}",
            progress,
        )
        return

    if "Sorting and writing the unified report" in line:
        set_stage(
            "dashboard",
            "Creating the job dashboard",
            68,
        )


def parse_agent_progress(line: str) -> None:
    if "Generating market intelligence" in line:
        set_stage(
            "market_ai",
            "Generating market intelligence",
            78,
        )
        return

    if "Coaching the top" in line:
        set_stage(
            "job_coach",
            "Coaching the top opportunities",
            88,
        )
        return

    if "Job intelligence ready" in line:
        set_stage(
            "finishing",
            "Finishing the application",
            97,
        )


def run_process(
    command: list[str],
    *,
    parser,
    environment: dict[str, str],
) -> int:
    process = subprocess.Popen(
        command,
        cwd=PROJECT_FOLDER,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=environment,
    )

    with RUN_STATE.lock:
        RUN_STATE.process = process

    assert process.stdout is not None

    for line in process.stdout:
        add_log(line)
        parser(line)

        with RUN_STATE.lock:
            if RUN_STATE.cancel_requested:
                process.terminate()
                break

    return_code = process.wait()

    with RUN_STATE.lock:
        RUN_STATE.process = None

    return return_code


def execute_run(request: RunRequest) -> None:
    environment = os.environ.copy()
    environment.update(read_env_file(ENV_FILE))

    model = str(
        request.config.get("agent", {}).get(
            "model",
            "gpt-5-mini",
        )
    ).strip()

    if model:
        environment["OPENAI_MODEL"] = model

    try:
        set_stage(
            "starting",
            "Preparing your search",
            2,
        )

        collector_command = [
            sys.executable,
            str(COLLECTOR_FILE),
            "--app-progress",
            "--no-open",
        ]

        if request.force_discovery:
            collector_command.append("--discover")

        collector_code = run_process(
            collector_command,
            parser=parse_collector_progress,
            environment=environment,
        )

        if collector_code != 0:
            raise RuntimeError(
                f"Collector exited with status {collector_code}."
            )

        with RUN_STATE.lock:
            if RUN_STATE.cancel_requested:
                raise RuntimeError("Run cancelled.")

        set_stage(
            "local_intelligence",
            "Calculating market statistics",
            74,
        )

        agent_command = [
            sys.executable,
            str(AGENT_FILE),
            "--top",
            str(request.top_jobs),
        ]

        if request.run_ai:
            if request.refresh_ai:
                agent_command.append("--refresh-ai")
        else:
            agent_command.append("--no-ai")

        agent_code = run_process(
            agent_command,
            parser=parse_agent_progress,
            environment=environment,
        )

        if agent_code != 0:
            raise RuntimeError(
                f"Agent exited with status {agent_code}."
            )

        set_stage("complete", "Complete", 100)

        with RUN_STATE.lock:
            RUN_STATE.running = False
            RUN_STATE.finished_at = utc_now()
            RUN_STATE.job_dashboard_ready = (
                JOB_DASHBOARD_FILE.exists()
            )
            RUN_STATE.intelligence_ready = (
                INTELLIGENCE_FILE.exists()
            )

    except Exception as error:
        with RUN_STATE.lock:
            cancelled = RUN_STATE.cancel_requested
            RUN_STATE.running = False
            RUN_STATE.finished_at = utc_now()
            RUN_STATE.stage = (
                "cancelled" if cancelled else "error"
            )
            RUN_STATE.stage_label = (
                "Cancelled" if cancelled else "Run failed"
            )
            RUN_STATE.error = (
                "The run was cancelled."
                if cancelled
                else str(error)
            )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_FOLDER / "index.html")


@app.get("/license")
def license_file() -> FileResponse:
    return FileResponse(
        PROJECT_FOLDER / "LICENSE.txt",
        media_type="text/plain",
        filename="FAC-Job-Seeker-Community-License-1.0.txt",
    )


@app.get("/third-party-notices")
def third_party_notices_file() -> FileResponse:
    return FileResponse(
        PROJECT_FOLDER / "THIRD_PARTY_NOTICES.txt",
        media_type="text/plain",
        filename="FAC-Third-Party-Notices.txt",
    )


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    config = merged_config()
    companies = load_company_registry()

    categories = sorted(
        {
            category
            for company in companies
            for category in company.get("categories", [])
        }
    )

    return {
        "config": config,
        "companies": companies,
        "categories": categories,
        "api_key": env_status(config),
        "run": RUN_STATE.snapshot(),
        "files": {
            "collector": COLLECTOR_FILE.exists(),
            "agent": AGENT_FILE.exists(),
            "companies": COMPANIES_FILE.exists(),
        },
    }


@app.post("/api/config")
def save_config_endpoint(
    payload: ConfigPayload,
) -> dict[str, Any]:
    save_json(CONFIG_FILE, payload.config)
    return {
        "saved": True,
        "saved_at": utc_now(),
    }


@app.post("/api/config/reset")
def reset_config_endpoint() -> dict[str, Any]:
    config = default_config()
    save_json(CONFIG_FILE, config)
    return {"config": config}


@app.post("/api/run")
def start_run(request: RunRequest) -> dict[str, Any]:
    with RUN_STATE.lock:
        if RUN_STATE.running:
            raise HTTPException(
                status_code=409,
                detail="A job search is already running.",
            )

        key_status = env_status(request.config)

        if request.run_ai and not key_status["configured"]:
            raise HTTPException(
                status_code=400,
                detail=(
                    "AI analysis was selected, but no "
                    "OPENAI_API_KEY was found in .env."
                ),
            )

        save_json(CONFIG_FILE, request.config)

        RUN_STATE.reset()
        RUN_STATE.running = True
        RUN_STATE.run_ai = request.run_ai
        RUN_STATE.started_at = utc_now()
        RUN_STATE.stage = "queued"
        RUN_STATE.stage_label = "Starting"
        RUN_STATE.progress = 1

        thread = threading.Thread(
            target=execute_run,
            args=(request,),
            daemon=True,
        )
        RUN_STATE.thread = thread
        thread.start()

    return {
        "started": True,
        "status": RUN_STATE.snapshot(),
    }


@app.get("/api/run/status")
def run_status() -> dict[str, Any]:
    return RUN_STATE.snapshot()


@app.post("/api/run/stop")
def stop_run() -> dict[str, Any]:
    with RUN_STATE.lock:
        if not RUN_STATE.running:
            return {"stopped": False}

        RUN_STATE.cancel_requested = True
        process = RUN_STATE.process

    if process is not None and process.poll() is None:
        process.terminate()

    return {"stopped": True}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "time": utc_now(),
    }


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        print(
            "FastAPI/Uvicorn is not installed.\n"
            "Run: pip install -r requirements_app.txt"
        )
        raise SystemExit(1)

    url = f"http://{HOST}:{PORT}"

    def open_browser() -> None:
        time.sleep(1.0)
        webbrowser.open(url)

    threading.Thread(
        target=open_browser,
        daemon=True,
    ).start()

    print(f"FAC - Job Tracker: {url}")
    print("Press Control+C to stop the application.")

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
