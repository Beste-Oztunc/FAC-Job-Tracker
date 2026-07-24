#!/usr/bin/env python3
"""
Local Job Market Intelligence + Opportunity Coach

This companion layer reads the latest output from the ATS collector,
calculates trustworthy market statistics locally, optionally asks the
OpenAI Responses API for structured analysis, caches the results, and
creates a two-tab intelligence report.

Run:
    python job_agent.py

Force fresh AI analysis:
    python job_agent.py --refresh-ai

Create/update the report without API calls:
    python job_agent.py --no-ai

Environment:
    OPENAI_API_KEY=...
    OPENAI_MODEL=gpt-5-mini

The script also reads a simple local .env file when present.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# ============================================================
# PATHS AND SETTINGS
# ============================================================

PROJECT_FOLDER = Path(__file__).resolve().parent
OUTPUT_FOLDER = PROJECT_FOLDER / "output"

MATCHES_FILE = OUTPUT_FOLDER / "all_ats_matches.json"
DASHBOARD_FILE = OUTPUT_FOLDER / "all_ats_job_dashboard.html"
INTELLIGENCE_REPORT_FILE = OUTPUT_FOLDER / "job_intelligence.html"

MARKET_CACHE_FILE = OUTPUT_FOLDER / "agent_market_cache.json"
COACH_CACHE_FILE = OUTPUT_FOLDER / "agent_coach_cache.json"

BOARD_CACHE_FILES = {
    "Greenhouse": OUTPUT_FOLDER / "greenhouse_board_cache.json",
    "Ashby": OUTPUT_FOLDER / "ashby_board_cache.json",
    "Lever": OUTPUT_FOLDER / "lever_board_cache.json",
}

DEFAULT_MODEL = "gpt-5-mini"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

# The market report uses the strongest relevant slice, not every
# visible result. Increase/decrease this threshold as desired.
MARKET_MIN_SCORE = 60.0

TOP_JOB_COUNT = 15
COACH_BATCH_SIZE = 5
MAX_DESCRIPTION_CHARS = 6_000
MAX_MARKET_JOBS = 300

AGENT_PROMPT_VERSION = "2026-07-24-v1"
PROFILE_VERSION = "2026-07-24-v1"

REQUEST_CONNECT_TIMEOUT_SECONDS = 10
REQUEST_READ_TIMEOUT_SECONDS = 180


# ============================================================
# PERSONAL PROFILE
# ============================================================

USER_PROFILE = {
    "headline": (
        "Enterprise Risk, AI, and Decisioning Solutions Engineer "
        "with 7+ years of technical presales experience"
    ),
    "current_positioning": [
        "Risk Data Scientist / Systems Engineer at SAS",
        "SAS AI Ambassador focused on ethical AI, explainability, bias mitigation, and governance",
        "SAS Certified AI & Machine Learning Professional",
        "Technical Excellence Award recipient",
    ],
    "strongest_evidence": [
        (
            "Leads enterprise discovery, workshops, proof-of-concept design, "
            "demonstrations, architecture discussions, and technical sales cycles "
            "for major banks, Fortune 500 companies, captive finance, telecom, "
            "insurance, and auto-finance customers."
        ),
        (
            "Builds predictive models, decision workflows, API integrations, "
            "GenAI prototypes, and agentic AI workflows for regulated use cases."
        ),
        (
            "Integrated OpenAI, Claude, and Gemini endpoints into enterprise "
            "decisioning prototypes, including standardized inference payloads, "
            "model evaluation, routing, model hot-swapping, validation, guardrails, "
            "and human review."
        ),
        (
            "Deep domain experience in credit risk, identity and application fraud, "
            "AML/KYC, collections, origination, pricing, limits, model risk "
            "management, responsible AI, and automated decisioning."
        ),
        (
            "Prior bank-side modeling experience covering PD, LGD, EAD, IFRS 9, "
            "ICAAP stress testing, concentration risk, Monte Carlo simulation, "
            "delinquency, vintage, and write-off analysis."
        ),
        (
            "Translates APIs, models, deployment choices, architecture constraints, "
            "governance, and implementation tradeoffs into executive business value."
        ),
        (
            "Built a cross-domain reference architecture combining risk, fraud, "
            "customer intelligence, decisioning, model governance, and AI."
        ),
    ],
    "tools_and_methods": [
        "SAS",
        "Python",
        "SQL",
        "R",
        "Excel",
        "Power BI",
        "SPSS",
        "Matlab",
        "REST APIs",
        "OpenAI API",
        "Claude API",
        "Gemini API",
        "logistic regression",
        "tree models",
        "random forests",
        "XGBoost",
        "time series",
        "clustering",
        "PCA",
        "Monte Carlo",
    ],
    "education": [
        "Master's in Data Science, Koç University",
        "Bachelor's degree in Economics, Bilkent University",
        "Mathematics minor",
        (
            "Economics thesis: Brain Drain in Developing Countries: "
            "The case of Turkey Between the Years 2001–2016"
        ),
    ],
    "target_roles": [
        "Solutions Engineer",
        "Sales Engineer",
        "Solutions Consultant",
        "Technical Success",
        "Solutions Architect",
        "Risk Consultant",
        "Fraud Consultant",
        "AI Consultant",
        "Data Scientist",
        "Decision Scientist",
        "Economist",
    ],
    "preferences": [
        "Remote from North Carolina where feasible",
        "Open to relocation for the right opportunity",
        "Prefers customer-facing technical roles defined by the customer challenge",
        "Does not want to be positioned primarily as a software engineer",
    ],
}

RESUME_VERSIONS = {
    "Primary résumé": (
        "Use the candidate's main résumé with only truthful, "
        "role-specific wording changes."
    ),
    "Technical / AI emphasis": (
        "Emphasize technical architecture, AI, data, APIs, "
        "deployment, engineering collaboration, and implementation."
    ),
    "Domain / industry emphasis": (
        "Emphasize relevant industry expertise, customer problems, "
        "regulation, business outcomes, and domain credibility."
    ),
    "Quantitative / analytics emphasis": (
        "Emphasize modeling, statistics, economics, experimentation, "
        "forecasting, data science, and measurable analysis."
    ),
}

# Add the résumé and job preferences saved in the local application.
from job_app_runtime import apply_agent_config

apply_agent_config(
    globals(),
    OUTPUT_FOLDER / "app_config.json",
)


# ============================================================
# MARKET TAXONOMY
# ============================================================

MARKET_REQUIREMENTS = {
    "Cloud architecture": [
        "cloud architecture",
        "cloud architect",
        "cloud infrastructure",
        "cloud platform",
        "cloud-native",
        "cloud native",
        "public cloud",
        "multi-cloud",
        "multicloud",
    ],
    "Python": ["python"],
    "APIs and integrations": [
        "api",
        "apis",
        "rest api",
        "restful",
        "integration",
        "integrations",
        "webhook",
        "microservice",
        "microservices",
    ],
    "Generative AI": [
        "generative ai",
        "gen ai",
        "genai",
        "large language model",
        "large language models",
        "llm",
        "llms",
        "foundation model",
        "foundation models",
    ],
    "Financial-services experience": [
        "financial services",
        "banking",
        "bank",
        "fintech",
        "lending",
        "loan",
        "credit union",
        "capital markets",
        "payments",
    ],
    "Technical discovery and demos": [
        "discovery",
        "demonstration",
        "demo",
        "proof of concept",
        "proof-of-concept",
        "poc",
        "workshop",
        "technical presentation",
    ],
    "Machine learning": [
        "machine learning",
        "predictive model",
        "predictive modeling",
        "data science",
        "ml model",
        "model training",
    ],
    "SQL and data querying": [
        "sql",
        "data warehouse",
        "data querying",
        "relational database",
        "rdbms",
    ],
    "Risk and credit": [
        "credit risk",
        "risk management",
        "risk model",
        "underwriting",
        "credit decision",
        "credit decisioning",
        "lending risk",
    ],
    "Fraud, AML, and KYC": [
        "fraud",
        "anti-money laundering",
        "anti money laundering",
        "aml",
        "know your customer",
        "kyc",
        "identity verification",
    ],
    "AI governance and responsible AI": [
        "ai governance",
        "responsible ai",
        "model governance",
        "model risk management",
        "explainability",
        "fairness",
        "bias mitigation",
    ],
    "Executive communication": [
        "executive communication",
        "executive presentation",
        "c-level",
        "c suite",
        "c-suite",
        "stakeholder management",
        "communicate complex",
    ],
    "Security and compliance": [
        "security",
        "compliance",
        "regulatory",
        "privacy",
        "governance",
        "soc 2",
        "soc2",
        "pci",
        "hipaa",
    ],
    "MLOps and model deployment": [
        "mlops",
        "model deployment",
        "model serving",
        "model monitoring",
        "model registry",
        "production machine learning",
        "inference service",
    ],
    "Kubernetes": ["kubernetes", "k8s"],
    "Containers and Docker": [
        "docker",
        "container",
        "containers",
        "containerization",
        "containerised",
        "containerized",
    ],
    "Infrastructure as code": [
        "terraform",
        "infrastructure as code",
        "infrastructure-as-code",
        "iac",
        "cloudformation",
    ],
    "Retrieval-augmented generation": [
        "retrieval-augmented generation",
        "retrieval augmented generation",
        "rag",
        "vector database",
        "vector store",
        "embeddings",
    ],
    "Production LLM monitoring": [
        "llm monitoring",
        "llm observability",
        "ai observability",
        "prompt monitoring",
        "prompt evaluation",
        "llm evaluation",
        "hallucination monitoring",
        "production llm",
    ],
    "Agentic AI": [
        "agentic ai",
        "ai agent",
        "ai agents",
        "multi-agent",
        "multi agent",
        "agent orchestration",
        "langgraph",
    ],
    "CI/CD and DevOps": [
        "ci/cd",
        "continuous integration",
        "continuous deployment",
        "devops",
        "github actions",
        "gitlab ci",
        "jenkins",
    ],
    "Data platforms": [
        "snowflake",
        "databricks",
        "bigquery",
        "redshift",
        "synapse",
        "data lake",
        "lakehouse",
    ],
    "Streaming and messaging": [
        "kafka",
        "event streaming",
        "pub/sub",
        "pubsub",
        "message queue",
        "messaging platform",
    ],
}

TECHNOLOGY_COUNTS = {
    "Microsoft Azure": ["azure", "microsoft cloud"],
    "AWS": [
        "aws",
        "amazon web services",
        "bedrock",
        "sagemaker",
        "lambda",
        "ec2",
    ],
    "Google Cloud": [
        "google cloud",
        "gcp",
        "vertex ai",
        "bigquery",
        "cloud run",
    ],
    "Kubernetes": ["kubernetes", "k8s"],
    "Terraform": ["terraform"],
    "Docker": ["docker"],
    "OpenAI": ["openai", "chatgpt"],
    "Anthropic / Claude": ["anthropic", "claude"],
    "Google Gemini": ["gemini"],
    "LangChain": ["langchain"],
    "LangGraph": ["langgraph"],
    "Databricks": ["databricks"],
    "Snowflake": ["snowflake"],
    "Kafka": ["kafka"],
    "PyTorch": ["pytorch"],
    "TensorFlow": ["tensorflow"],
}


# ============================================================
# JSON SCHEMAS
# ============================================================

MARKET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "executive_summary": {"type": "string"},
        "strongest_alignment": {
            "type": "array",
            "items": {"type": "string"},
        },
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "area": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["High", "Medium", "Low"],
                    },
                    "why_it_matters": {"type": "string"},
                    "market_evidence": {"type": "string"},
                    "practical_next_step": {"type": "string"},
                },
                "required": [
                    "area",
                    "priority",
                    "why_it_matters",
                    "market_evidence",
                    "practical_next_step",
                ],
            },
        },
        "certification_priorities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "rank": {"type": "integer"},
                    "track": {"type": "string"},
                    "recommended_options": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "why_this_track": {"type": "string"},
                    "job_market_evidence": {"type": "string"},
                    "skills_it_would_strengthen": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "caution": {"type": "string"},
                },
                "required": [
                    "rank",
                    "track",
                    "recommended_options",
                    "why_this_track",
                    "job_market_evidence",
                    "skills_it_would_strengthen",
                    "caution",
                ],
            },
        },
        "market_signals": {
            "type": "array",
            "items": {"type": "string"},
        },
        "recommended_30_day_actions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "executive_summary",
        "strongest_alignment",
        "gaps",
        "certification_priorities",
        "market_signals",
        "recommended_30_day_actions",
    ],
}

COACH_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "job_key": {"type": "string"},
        "recommendation": {
            "type": "string",
            "enum": ["Apply", "Maybe", "Skip"],
        },
        "confidence": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "why_you_fit": {
            "type": "array",
            "items": {"type": "string"},
        },
        "strongest_resume_evidence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "likely_interview_topics": {
            "type": "array",
            "items": {"type": "string"},
        },
        "missing_or_weaker_qualifications": {
            "type": "array",
            "items": {"type": "string"},
        },
        "technical_questions_to_prepare": {
            "type": "array",
            "items": {"type": "string"},
        },
        "behavioral_stories_to_prepare": {
            "type": "array",
            "items": {"type": "string"},
        },
        "company_specific_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "resume_version": {
            "type": "string",
            "enum": list(RESUME_VERSIONS.keys()),
        },
        "keywords_worth_adding": {
            "type": "array",
            "items": {"type": "string"},
        },
        "application_strategy": {"type": "string"},
    },
    "required": [
        "job_key",
        "recommendation",
        "confidence",
        "why_you_fit",
        "strongest_resume_evidence",
        "likely_interview_topics",
        "missing_or_weaker_qualifications",
        "technical_questions_to_prepare",
        "behavioral_stories_to_prepare",
        "company_specific_questions",
        "resume_version",
        "keywords_worth_adding",
        "application_strategy",
    ],
}

COACH_BATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "jobs": {
            "type": "array",
            "items": COACH_ITEM_SCHEMA,
        },
    },
    "required": ["jobs"],
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

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

        if key and key not in os.environ:
            os.environ[key] = value


def load_json(path: Path, default: Any) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return payload


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def stable_hash(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def clean_text(value: str) -> str:
    lowered = safe_text(value).lower()
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def make_job_key(job: dict) -> str:
    """
    Use the same stable raw identity for dashboard matches and ATS
    cache jobs. The dashboard_job_key is a browser-only hash and
    cannot be used to join descriptions back to their source caches.
    """

    source = safe_text(job.get("source"), "unknown").lower()
    company = safe_text(job.get("company"), "unknown").lower()
    identifier = (
        safe_text(job.get("id"))
        or safe_text(job.get("apply_url"))
        or safe_text(job.get("title"))
    )
    return f"{source}:{company}:{identifier}"


def chunks(values: list[dict], size: int) -> list[list[dict]]:
    return [
        values[index : index + size]
        for index in range(0, len(values), size)
    ]


def truncate(value: str, limit: int) -> str:
    text = safe_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# ============================================================
# LOAD JOBS AND DESCRIPTIONS
# ============================================================

def load_matches() -> list[dict]:
    payload = load_json(MATCHES_FILE, [])

    if not isinstance(payload, list):
        raise RuntimeError(
            f"{MATCHES_FILE} does not contain a job list."
        )

    jobs = [
        job
        for job in payload
        if isinstance(job, dict)
    ]

    if not jobs:
        raise RuntimeError(
            "No matching jobs were found. Run the ATS collector first."
        )

    return jobs


def build_description_index() -> dict[str, str]:
    index: dict[str, str] = {}

    for source_name, cache_file in BOARD_CACHE_FILES.items():
        payload = load_json(cache_file, {})

        if not isinstance(payload, dict):
            continue

        for entry in payload.values():
            if not isinstance(entry, dict):
                continue

            jobs = entry.get("jobs")

            if not isinstance(jobs, list):
                continue

            for job in jobs:
                if not isinstance(job, dict):
                    continue

                source = safe_text(
                    job.get("source"),
                    source_name,
                )
                key = make_job_key(
                    {
                        **job,
                        "source": source,
                    }
                )
                description = safe_text(job.get("description"))

                if description:
                    index[key] = description

    return index


def attach_descriptions(
    jobs: list[dict],
    description_index: dict[str, str],
) -> list[dict]:
    enriched = []

    for job in jobs:
        job_copy = dict(job)
        key = make_job_key(job_copy)
        job_copy["agent_job_key"] = key

        description = (
            safe_text(job_copy.get("description"))
            or description_index.get(key, "")
        )

        job_copy["agent_description"] = description
        enriched.append(job_copy)

    return enriched


# ============================================================
# LOCAL MARKET ANALYSIS
# ============================================================

def phrase_found(text: str, phrase: str) -> bool:
    normalized_phrase = clean_text(phrase)

    if not normalized_phrase:
        return False

    if re.fullmatch(r"[a-z0-9 ]+", normalized_phrase):
        pattern = (
            r"(?<![a-z0-9])"
            + re.escape(normalized_phrase)
            + r"(?![a-z0-9])"
        )
        return re.search(pattern, text) is not None

    return normalized_phrase in text


def contains_any(text: str, phrases: list[str]) -> bool:
    return any(
        phrase_found(text, phrase)
        for phrase in phrases
    )


def job_analysis_text(job: dict) -> str:
    fields = [
        safe_text(job.get("title")),
        safe_text(job.get("department")),
        safe_text(job.get("team")),
        safe_text(job.get("employment_type")),
        safe_text(job.get("agent_description")),
    ]
    return clean_text(" ".join(fields))


def choose_market_jobs(jobs: list[dict]) -> list[dict]:
    selected = [
        job
        for job in jobs
        if float(job.get("final_score") or 0) >= MARKET_MIN_SCORE
    ]

    if not selected:
        selected = sorted(
            jobs,
            key=lambda job: float(
                job.get("final_score") or 0
            ),
            reverse=True,
        )[: min(100, len(jobs))]

    return selected[:MAX_MARKET_JOBS]


def count_taxonomy(
    jobs: list[dict],
    taxonomy: dict[str, list[str]],
) -> list[dict]:
    counts = Counter()

    for job in jobs:
        text = job_analysis_text(job)

        for label, phrases in taxonomy.items():
            if contains_any(text, phrases):
                counts[label] += 1

    total = len(jobs)

    results = []

    for label, count in counts.most_common():
        percentage = (
            round((count / total) * 100, 1)
            if total
            else 0.0
        )
        results.append(
            {
                "name": label,
                "count": count,
                "percentage": percentage,
            }
        )

    return results


def build_local_market_stats(
    all_jobs: list[dict],
) -> dict:
    market_jobs = choose_market_jobs(all_jobs)

    requirement_counts = count_taxonomy(
        market_jobs,
        MARKET_REQUIREMENTS,
    )

    technology_counts = count_taxonomy(
        market_jobs,
        TECHNOLOGY_COUNTS,
    )

    sources = Counter(
        safe_text(job.get("source"), "Unknown")
        for job in market_jobs
    )

    arrangements = Counter(
        safe_text(
            job.get("work_arrangement"),
            "Not specified",
        ).title()
        for job in market_jobs
    )

    experiences = Counter(
        safe_text(
            job.get("experience_level"),
            "Not specified",
        )
        for job in market_jobs
    )

    companies = Counter(
        safe_text(job.get("company"), "Unknown")
        for job in market_jobs
    )

    return {
        "generated_at": utc_now_iso(),
        "market_min_score": MARKET_MIN_SCORE,
        "relevant_job_count": len(market_jobs),
        "all_visible_job_count": len(all_jobs),
        "strong_match_count": sum(
            1
            for job in all_jobs
            if safe_text(job.get("match_tier"))
            == "Strong match"
        ),
        "possible_match_count": sum(
            1
            for job in all_jobs
            if safe_text(job.get("match_tier"))
            == "Possible match"
        ),
        "requirements": requirement_counts,
        "technologies": technology_counts,
        "sources": dict(sources.most_common()),
        "arrangements": dict(arrangements.most_common()),
        "experience_levels": dict(experiences.most_common()),
        "top_companies": [
            {"company": company, "count": count}
            for company, count in companies.most_common(15)
        ],
        "market_job_keys": [
            make_job_key(job)
            for job in market_jobs
        ],
    }


# ============================================================
# OPENAI RESPONSES API
# ============================================================

def extract_response_text(payload: dict) -> str:
    direct = payload.get("output_text")

    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    output = payload.get("output")

    if not isinstance(output, list):
        return ""

    pieces = []

    for item in output:
        if not isinstance(item, dict):
            continue

        content = item.get("content")

        if not isinstance(content, list):
            continue

        for part in content:
            if not isinstance(part, dict):
                continue

            text = part.get("text")

            if isinstance(text, str):
                pieces.append(text)

    return "".join(pieces).strip()


def call_openai_structured(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_payload: dict,
    schema_name: str,
    schema: dict,
    max_output_tokens: int,
) -> dict:
    request_payload = {
        "model": model,
        "store": False,
        "input": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": json.dumps(
                    user_payload,
                    ensure_ascii=False,
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
        "max_output_tokens": max_output_tokens,
    }

    response = requests.post(
        OPENAI_RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=request_payload,
        timeout=(
            REQUEST_CONNECT_TIMEOUT_SECONDS,
            REQUEST_READ_TIMEOUT_SECONDS,
        ),
    )

    if response.status_code >= 400:
        detail = truncate(response.text, 1_500)
        raise RuntimeError(
            f"OpenAI API returned {response.status_code}: {detail}"
        )

    payload = response.json()
    response_text = extract_response_text(payload)

    if not response_text:
        raise RuntimeError(
            "OpenAI returned no structured output text."
        )

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "OpenAI returned output that could not be parsed as JSON."
        ) from error

    if not isinstance(parsed, dict):
        raise RuntimeError(
            "OpenAI structured output was not a JSON object."
        )

    return parsed


# ============================================================
# AI MARKET INTELLIGENCE
# ============================================================

def market_ai_fingerprint(
    local_stats: dict,
    model: str,
) -> str:
    stable_stats = {
        key: value
        for key, value in local_stats.items()
        if key != "generated_at"
    }

    return stable_hash(
        {
            "prompt_version": AGENT_PROMPT_VERSION,
            "profile_version": PROFILE_VERSION,
            "model": model,
            "profile": USER_PROFILE,
            "resume_versions": RESUME_VERSIONS,
            "stats": stable_stats,
        }
    )


def market_system_prompt() -> str:
    return """
You are a rigorous job-market intelligence analyst and career strategist.

You will receive:
1. Locally calculated market statistics from relevant job listings.
2. A candidate profile.
3. Available resume positioning versions.

Rules:
- Treat all counts and percentages as authoritative. Do not invent or alter them.
- Ground every market claim in the supplied statistics.
- Compare job demand against evidence explicitly present in the candidate profile.
- Absence from the profile means "not strongly evidenced," not "the candidate cannot do it."
- Be candid but constructive.
- Do not recommend generic education merely to fill space.
- Certification priorities must reflect the actual listing counts and the candidate's target roles.
- Include cloud architecture, risk, fraud, Kubernetes, and AI engineering tracks when supported, but rank them honestly.
- Exact certification names may change; add a caution to verify current prerequisites and exam paths.
- Return only the required structured JSON.
""".strip()


def generate_market_ai(
    *,
    local_stats: dict,
    api_key: str,
    model: str,
) -> dict:
    payload = {
        "candidate_profile": USER_PROFILE,
        "resume_versions": RESUME_VERSIONS,
        "market_statistics": local_stats,
        "instruction": (
            "Produce a concise but substantive intelligence report. "
            "Use exact counts in market evidence when useful."
        ),
    }

    return call_openai_structured(
        api_key=api_key,
        model=model,
        system_prompt=market_system_prompt(),
        user_payload=payload,
        schema_name="job_market_intelligence",
        schema=MARKET_SCHEMA,
        max_output_tokens=8_000,
    )


# ============================================================
# PER-JOB COACH
# ============================================================

def top_jobs_for_coaching(
    jobs: list[dict],
    count: int,
) -> list[dict]:
    return sorted(
        jobs,
        key=lambda job: (
            float(job.get("final_score") or 0),
            float(job.get("raw_total_score") or 0),
            int(job.get("location_priority") or 0),
        ),
        reverse=True,
    )[:count]


def compact_job_for_ai(job: dict) -> dict:
    return {
        "job_key": make_job_key(job),
        "title": safe_text(job.get("title")),
        "company": safe_text(job.get("company")),
        "location": safe_text(job.get("location")),
        "work_arrangement": safe_text(
            job.get("work_arrangement")
        ),
        "employment_type": safe_text(
            job.get("employment_type")
        ),
        "compensation": safe_text(job.get("compensation")),
        "match_score": job.get("final_score"),
        "raw_score": job.get("raw_total_score"),
        "match_tier": safe_text(job.get("match_tier")),
        "score_contributors": job.get("score_reasons") or [],
        "description": truncate(
            safe_text(job.get("agent_description")),
            MAX_DESCRIPTION_CHARS,
        ),
    }


def coach_ai_fingerprint(
    top_jobs: list[dict],
    model: str,
) -> str:
    compact_jobs = [
        compact_job_for_ai(job)
        for job in top_jobs
    ]

    return stable_hash(
        {
            "prompt_version": AGENT_PROMPT_VERSION,
            "profile_version": PROFILE_VERSION,
            "model": model,
            "profile": USER_PROFILE,
            "resume_versions": RESUME_VERSIONS,
            "jobs": compact_jobs,
        }
    )


def coach_system_prompt() -> str:
    return """
You are a demanding but fair job-application coach for a senior enterprise
risk, AI, decisioning, and technical-presales professional.

For every supplied job:
- Assess fit using only the job data and candidate evidence supplied.
- Do not fabricate candidate achievements, company facts, or job requirements.
- "Missing" means not strongly demonstrated in the supplied profile.
- Use Apply when the candidate has a credible path to interview, not only when perfect.
- Use Skip for genuinely poor fit, prohibited entry-level work, or roles fundamentally centered on software engineering with weak alignment.
- Recommend exactly one supplied resume version.
- Technical questions must be realistic questions the employer could ask.
- Behavioral stories should identify the type of candidate story to prepare, grounded in existing experience.
- Company-specific questions may be framed around the company's product, customers, implementation, governance, metrics, and role expectations, but do not invent private facts.
- Keywords should be truthful additions or emphasis changes, never keyword stuffing.
- Return one coaching object for every input job_key, with no extras.
- Return only the required structured JSON.
""".strip()


def generate_coach_batch(
    *,
    jobs: list[dict],
    api_key: str,
    model: str,
) -> list[dict]:
    payload = {
        "candidate_profile": USER_PROFILE,
        "resume_versions": RESUME_VERSIONS,
        "jobs": [
            compact_job_for_ai(job)
            for job in jobs
        ],
    }

    response = call_openai_structured(
        api_key=api_key,
        model=model,
        system_prompt=coach_system_prompt(),
        user_payload=payload,
        schema_name="top_opportunity_coach",
        schema=COACH_BATCH_SCHEMA,
        max_output_tokens=12_000,
    )

    items = response.get("jobs")

    if not isinstance(items, list):
        raise RuntimeError(
            "Opportunity coach response did not include a job list."
        )

    expected_keys = {
        make_job_key(job)
        for job in jobs
    }
    returned_keys = {
        safe_text(item.get("job_key"))
        for item in items
        if isinstance(item, dict)
    }

    if expected_keys != returned_keys:
        missing = expected_keys - returned_keys
        extras = returned_keys - expected_keys
        raise RuntimeError(
            "Opportunity coach returned mismatched job keys. "
            f"Missing={sorted(missing)}, extras={sorted(extras)}"
        )

    return [
        item
        for item in items
        if isinstance(item, dict)
    ]


def generate_all_coaches(
    *,
    top_jobs: list[dict],
    api_key: str,
    model: str,
) -> list[dict]:
    batches = chunks(top_jobs, COACH_BATCH_SIZE)

    results: list[dict] = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(3, len(batches))
    ) as executor:
        futures = [
            executor.submit(
                generate_coach_batch,
                jobs=batch,
                api_key=api_key,
                model=model,
            )
            for batch in batches
        ]

        for future in concurrent.futures.as_completed(futures):
            results.extend(future.result())

    order = {
        make_job_key(job): index
        for index, job in enumerate(top_jobs)
    }

    results.sort(
        key=lambda item: order.get(
            safe_text(item.get("job_key")),
            999_999,
        )
    )

    return results


# ============================================================
# CACHING
# ============================================================

def load_cached_payload(
    path: Path,
    expected_fingerprint: str,
) -> dict | None:
    payload = load_json(path, {})

    if not isinstance(payload, dict):
        return None

    if payload.get("fingerprint") != expected_fingerprint:
        return None

    data = payload.get("data")

    if not isinstance(data, dict):
        return None

    return data


def save_cached_payload(
    path: Path,
    fingerprint: str,
    model: str,
    data: dict,
) -> None:
    save_json(
        path,
        {
            "fingerprint": fingerprint,
            "model": model,
            "generated_at": utc_now_iso(),
            "data": data,
        },
    )


# ============================================================
# DETERMINISTIC FALLBACK CONTENT
# ============================================================

def stat_lookup(stats: dict, name: str) -> dict:
    for item in stats.get("requirements", []):
        if item.get("name") == name:
            return item
    for item in stats.get("technologies", []):
        if item.get("name") == name:
            return item
    return {"name": name, "count": 0, "percentage": 0.0}


def deterministic_market_fallback(stats: dict) -> dict:
    requirements = stats.get("requirements", [])
    technologies = stats.get("technologies", [])

    top_requirements = [
        item
        for item in requirements[:5]
        if isinstance(item, dict)
    ]
    top_technologies = [
        item
        for item in technologies[:5]
        if isinstance(item, dict)
    ]

    market_signals = [
        (
            f"{item.get('name')} appears in "
            f"{int(item.get('count') or 0)} relevant listings "
            f"({float(item.get('percentage') or 0):.1f}%)."
        )
        for item in top_technologies
    ]

    if not market_signals:
        market_signals = [
            "No technology signal crossed the local counting threshold."
        ]

    strongest_alignment = [
        (
            f"Market emphasis: {item.get('name')} "
            f"({float(item.get('percentage') or 0):.1f}% of relevant jobs)"
        )
        for item in top_requirements
    ]

    if not strongest_alignment:
        strongest_alignment = [
            "Personalized alignment requires AI analysis and résumé text."
        ]

    return {
        "executive_summary": (
            "The application calculated the market counts directly from "
            "the relevant job descriptions. AI interpretation was not run, "
            "so this page shows verified local statistics without making "
            "personalized claims or incurring API cost."
        ),
        "strongest_alignment": strongest_alignment,
        "gaps": [],
        "certification_priorities": [],
        "market_signals": market_signals,
        "recommended_30_day_actions": [
            "Review the most frequent requirements and compare them with the résumé.",
            "Use the technology counts to prioritize one practical learning track.",
            "Turn repeated requirements into truthful résumé and interview evidence.",
        ],
    }


# ============================================================
# HTML REPORT
# ============================================================

def escape(value: Any) -> str:
    return html.escape(safe_text(value))


def list_html(items: list[str], empty_text: str = "Not identified") -> str:
    if not items:
        return f'<p class="muted">{escape(empty_text)}</p>'

    return (
        "<ul>"
        + "".join(
            f"<li>{escape(item)}</li>"
            for item in items
        )
        + "</ul>"
    )


def requirement_rows(items: list[dict], limit: int = 15) -> str:
    rows = []

    for index, item in enumerate(items[:limit], start=1):
        percentage = float(item.get("percentage") or 0)
        rows.append(
            f"""
            <div class="requirement-row">
                <div class="requirement-rank">{index}</div>
                <div class="requirement-main">
                    <div class="requirement-label">
                        <span>{escape(item.get("name"))}</span>
                        <strong>{percentage:.1f}%</strong>
                    </div>
                    <div class="bar-track">
                        <div
                            class="bar-fill"
                            style="width:{min(100, percentage):.1f}%"
                        ></div>
                    </div>
                    <div class="requirement-count">
                        {int(item.get("count") or 0)} listings
                    </div>
                </div>
            </div>
            """
        )

    return "".join(rows)


def technology_cards(items: list[dict]) -> str:
    cards = []

    for item in items:
        cards.append(
            f"""
            <div class="tech-card">
                <span>{escape(item.get("name"))}</span>
                <strong>{int(item.get("count") or 0)}</strong>
                <small>{float(item.get("percentage") or 0):.1f}% of relevant jobs</small>
            </div>
            """
        )

    return "".join(cards)


def gap_cards(gaps: list[dict]) -> str:
    cards = []

    for gap in gaps:
        priority = safe_text(gap.get("priority"), "Medium").lower()
        cards.append(
            f"""
            <article class="analysis-card">
                <div class="card-heading">
                    <h3>{escape(gap.get("area"))}</h3>
                    <span class="priority {escape(priority)}">
                        {escape(gap.get("priority"))}
                    </span>
                </div>
                <p>{escape(gap.get("why_it_matters"))}</p>
                <p class="evidence">
                    <strong>Market evidence:</strong>
                    {escape(gap.get("market_evidence"))}
                </p>
                <p class="next-step">
                    <strong>Next step:</strong>
                    {escape(gap.get("practical_next_step"))}
                </p>
            </article>
            """
        )

    return "".join(cards)


def certification_cards(items: list[dict]) -> str:
    cards = []

    for item in sorted(
        items,
        key=lambda row: int(row.get("rank") or 999),
    ):
        options = list_html(
            [
                safe_text(value)
                for value in item.get("recommended_options", [])
            ]
        )
        skills = list_html(
            [
                safe_text(value)
                for value in item.get(
                    "skills_it_would_strengthen",
                    [],
                )
            ]
        )

        cards.append(
            f"""
            <article class="cert-card">
                <div class="cert-rank">
                    {int(item.get("rank") or 0)}
                </div>
                <div>
                    <h3>{escape(item.get("track"))}</h3>
                    <p>{escape(item.get("why_this_track"))}</p>
                    <p class="evidence">
                        <strong>Market evidence:</strong>
                        {escape(item.get("job_market_evidence"))}
                    </p>
                    <div class="two-column">
                        <div>
                            <h4>Possible credentials</h4>
                            {options}
                        </div>
                        <div>
                            <h4>Skills strengthened</h4>
                            {skills}
                        </div>
                    </div>
                    <p class="caution">
                        {escape(item.get("caution"))}
                    </p>
                </div>
            </article>
            """
        )

    return "".join(cards)


def coach_card(job: dict, coach: dict | None) -> str:
    title = escape(job.get("title"))
    company = escape(job.get("company"))
    location = escape(job.get("location"))
    apply_url = html.escape(
        safe_text(job.get("apply_url")),
        quote=True,
    )
    score = float(job.get("final_score") or 0)

    if coach is None:
        return f"""
        <article class="coach-card">
            <div class="coach-card-header">
                <div>
                    <span class="score-pill">{score:.1f}</span>
                    <h3>{title}</h3>
                    <p>{company} · {location}</p>
                </div>
                <a href="{apply_url}" target="_blank" rel="noopener">
                    Open job
                </a>
            </div>
            <div class="empty-coach">
                AI coaching has not been generated for this role yet.
            </div>
        </article>
        """

    recommendation = safe_text(
        coach.get("recommendation"),
        "Maybe",
    )
    recommendation_slug = recommendation.lower()
    confidence = int(coach.get("confidence") or 0)
    resume_version = safe_text(coach.get("resume_version"))

    def section(title_text: str, values: list[str]) -> str:
        return f"""
        <section class="coach-section">
            <h4>{escape(title_text)}</h4>
            {list_html([safe_text(value) for value in values])}
        </section>
        """

    return f"""
    <article class="coach-card">
        <div class="coach-card-header">
            <div class="coach-title-block">
                <div class="coach-badges">
                    <span class="score-pill">{score:.1f}</span>
                    <span class="recommendation {escape(recommendation_slug)}">
                        {escape(recommendation)}
                    </span>
                    <span class="confidence">{confidence}% confidence</span>
                </div>
                <h3>{title}</h3>
                <p>{company} · {location}</p>
            </div>
            <a href="{apply_url}" target="_blank" rel="noopener">
                Open job
            </a>
        </div>

        <div class="resume-callout">
            <strong>Recommended résumé:</strong>
            {escape(resume_version)}
        </div>

        <div class="coach-grid">
            {section("Why you fit", coach.get("why_you_fit", []))}
            {section(
                "Strongest résumé evidence",
                coach.get("strongest_resume_evidence", []),
            )}
            {section(
                "Missing or weaker qualifications",
                coach.get("missing_or_weaker_qualifications", []),
            )}
            {section(
                "Likely interview topics",
                coach.get("likely_interview_topics", []),
            )}
            {section(
                "Technical questions to prepare",
                coach.get("technical_questions_to_prepare", []),
            )}
            {section(
                "Behavioral stories to prepare",
                coach.get("behavioral_stories_to_prepare", []),
            )}
            {section(
                "Questions to ask the company",
                coach.get("company_specific_questions", []),
            )}
            {section(
                "Keywords worth adding",
                coach.get("keywords_worth_adding", []),
            )}
        </div>

        <div class="application-strategy">
            <strong>Application strategy:</strong>
            {escape(coach.get("application_strategy"))}
        </div>
    </article>
    """


def render_report(
    *,
    jobs: list[dict],
    local_stats: dict,
    market_ai: dict,
    top_jobs: list[dict],
    coaches: list[dict],
    model: str,
    api_used: bool,
    error_message: str = "",
) -> Path:
    coach_by_key = {
        safe_text(item.get("job_key")): item
        for item in coaches
        if isinstance(item, dict)
    }

    coach_cards = "".join(
        coach_card(
            job,
            coach_by_key.get(make_job_key(job)),
        )
        for job in top_jobs
    )

    top_requirements = local_stats.get("requirements", [])
    technology_items = local_stats.get("technologies", [])

    generated_at = datetime.now().astimezone().strftime(
        "%B %d, %Y at %I:%M %p"
    )

    status_text = (
        f"AI analysis generated with {model}"
        if api_used
        else "Local statistics generated; AI narrative uses cached or deterministic content"
    )

    error_box = (
        f"""
        <div class="error-box">
            <strong>AI update issue:</strong>
            {escape(error_message)}
        </div>
        """
        if error_message
        else ""
    )

    page = f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >
    <title>Job Market Intelligence</title>
    <style>
        :root {{
            --background: #f4f6f9;
            --surface: #ffffff;
            --surface-soft: #f8fafc;
            --text: #172033;
            --muted: #667085;
            --line: #dce2ea;
            --blue: #1769e0;
            --blue-soft: #eaf2ff;
            --green: #16835b;
            --green-soft: #e8f7f0;
            --amber: #9a6700;
            --amber-soft: #fff5d8;
            --red: #b42318;
            --red-soft: #feeceb;
            --shadow: 0 10px 28px rgba(30, 45, 70, 0.08);
        }}

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            background: var(--background);
            color: var(--text);
            font-family:
                Inter,
                ui-sans-serif,
                system-ui,
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                sans-serif;
        }}

        a {{
            color: inherit;
        }}

        .shell {{
            width: min(1240px, calc(100% - 32px));
            margin: 0 auto;
        }}

        .hero {{
            background:
                radial-gradient(
                    circle at top right,
                    rgba(99, 160, 255, 0.28),
                    transparent 38%
                ),
                linear-gradient(135deg, #10264d, #153d75);
            color: white;
            padding: 36px 0 28px;
        }}

        .hero-row {{
            display: flex;
            justify-content: space-between;
            gap: 24px;
            align-items: flex-start;
        }}

        .hero h1 {{
            margin: 0 0 8px;
            font-size: clamp(28px, 4vw, 44px);
            letter-spacing: -0.04em;
        }}

        .hero p {{
            margin: 0;
            color: rgba(255,255,255,0.8);
            max-width: 760px;
            line-height: 1.55;
        }}

        .back-link {{
            display: inline-flex;
            align-items: center;
            min-height: 42px;
            padding: 0 16px;
            border: 1px solid rgba(255,255,255,0.28);
            border-radius: 10px;
            color: white;
            text-decoration: none;
            font-weight: 750;
            white-space: nowrap;
            background: rgba(255,255,255,0.08);
        }}

        .report-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 20px;
        }}

        .meta-pill {{
            padding: 7px 10px;
            border-radius: 999px;
            background: rgba(255,255,255,0.12);
            color: rgba(255,255,255,0.9);
            font-size: 13px;
            font-weight: 700;
        }}

        .tabs-wrap {{
            position: sticky;
            top: 0;
            z-index: 20;
            background: rgba(244,246,249,0.94);
            backdrop-filter: blur(14px);
            border-bottom: 1px solid var(--line);
        }}

        .tabs {{
            display: flex;
            gap: 8px;
            padding: 12px 0;
            overflow-x: auto;
        }}

        .tab-button {{
            border: 0;
            border-radius: 10px;
            padding: 11px 16px;
            background: transparent;
            color: var(--muted);
            font: inherit;
            font-weight: 800;
            cursor: pointer;
            white-space: nowrap;
        }}

        .tab-button.active {{
            background: var(--surface);
            color: var(--blue);
            box-shadow: 0 3px 12px rgba(25,55,100,0.1);
        }}

        main {{
            padding: 28px 0 60px;
        }}

        .tab-panel {{
            display: none;
        }}

        .tab-panel.active {{
            display: block;
        }}

        .section {{
            margin-bottom: 26px;
        }}

        .section-title {{
            display: flex;
            justify-content: space-between;
            gap: 20px;
            align-items: end;
            margin-bottom: 14px;
        }}

        .section-title h2 {{
            margin: 0;
            font-size: 24px;
            letter-spacing: -0.025em;
        }}

        .section-title p {{
            margin: 5px 0 0;
            color: var(--muted);
        }}

        .summary-card,
        .panel,
        .analysis-card,
        .cert-card,
        .coach-card {{
            background: var(--surface);
            border: 1px solid var(--line);
            border-radius: 16px;
            box-shadow: var(--shadow);
        }}

        .summary-card {{
            padding: 24px;
            font-size: 17px;
            line-height: 1.65;
        }}

        .metric-grid {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 14px;
            margin-bottom: 24px;
        }}

        .metric {{
            background: var(--surface);
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 18px;
            box-shadow: var(--shadow);
        }}

        .metric strong {{
            display: block;
            font-size: 30px;
            letter-spacing: -0.04em;
        }}

        .metric span {{
            color: var(--muted);
            font-size: 13px;
            font-weight: 750;
        }}

        .two-panel-grid {{
            display: grid;
            grid-template-columns: 1.15fr 0.85fr;
            gap: 18px;
        }}

        .panel {{
            padding: 20px;
        }}

        .panel h3 {{
            margin: 0 0 16px;
        }}

        .requirement-row {{
            display: grid;
            grid-template-columns: 28px 1fr;
            gap: 10px;
            margin-bottom: 14px;
        }}

        .requirement-rank {{
            display: grid;
            place-items: center;
            width: 26px;
            height: 26px;
            border-radius: 8px;
            background: var(--blue-soft);
            color: var(--blue);
            font-size: 12px;
            font-weight: 900;
        }}

        .requirement-label {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            font-size: 14px;
        }}

        .bar-track {{
            height: 7px;
            margin: 6px 0 4px;
            overflow: hidden;
            border-radius: 999px;
            background: #e9edf3;
        }}

        .bar-fill {{
            height: 100%;
            border-radius: inherit;
            background: linear-gradient(90deg, #1769e0, #54a1ff);
        }}

        .requirement-count {{
            color: var(--muted);
            font-size: 12px;
        }}

        .tech-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
        }}

        .tech-card {{
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 4px 10px;
            align-items: center;
            padding: 13px;
            border: 1px solid var(--line);
            border-radius: 12px;
            background: var(--surface-soft);
        }}

        .tech-card strong {{
            color: var(--blue);
            font-size: 22px;
        }}

        .tech-card small {{
            grid-column: 1 / -1;
            color: var(--muted);
        }}

        .bullet-panel {{
            padding: 20px 22px;
        }}

        ul {{
            margin: 10px 0 0;
            padding-left: 21px;
        }}

        li {{
            margin: 7px 0;
            line-height: 1.48;
        }}

        .analysis-grid {{
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 14px;
        }}

        .analysis-card {{
            padding: 18px;
        }}

        .analysis-card h3,
        .cert-card h3 {{
            margin: 0;
        }}

        .card-heading {{
            display: flex;
            justify-content: space-between;
            gap: 10px;
            align-items: flex-start;
        }}

        .priority,
        .recommendation {{
            display: inline-flex;
            align-items: center;
            min-height: 26px;
            padding: 0 9px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 900;
        }}

        .priority.high,
        .recommendation.skip {{
            color: var(--red);
            background: var(--red-soft);
        }}

        .priority.medium,
        .recommendation.maybe {{
            color: var(--amber);
            background: var(--amber-soft);
        }}

        .priority.low,
        .recommendation.apply {{
            color: var(--green);
            background: var(--green-soft);
        }}

        .evidence,
        .next-step {{
            color: #39445a;
            line-height: 1.5;
        }}

        .cert-list {{
            display: grid;
            gap: 14px;
        }}

        .cert-card {{
            display: grid;
            grid-template-columns: 48px 1fr;
            gap: 16px;
            padding: 20px;
        }}

        .cert-rank {{
            display: grid;
            place-items: center;
            width: 44px;
            height: 44px;
            border-radius: 13px;
            background: var(--blue);
            color: white;
            font-size: 20px;
            font-weight: 900;
        }}

        .two-column {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 20px;
        }}

        h4 {{
            margin: 12px 0 6px;
        }}

        .caution {{
            padding: 10px 12px;
            border-radius: 10px;
            color: #6b5200;
            background: var(--amber-soft);
            font-size: 13px;
        }}

        .coach-list {{
            display: grid;
            gap: 18px;
        }}

        .coach-card {{
            padding: 22px;
        }}

        .coach-card-header {{
            display: flex;
            justify-content: space-between;
            gap: 20px;
            align-items: flex-start;
        }}

        .coach-card-header h3 {{
            margin: 8px 0 4px;
            font-size: 22px;
        }}

        .coach-card-header p {{
            margin: 0;
            color: var(--muted);
        }}

        .coach-card-header > a {{
            display: inline-flex;
            align-items: center;
            min-height: 40px;
            padding: 0 14px;
            border-radius: 10px;
            color: white;
            background: var(--blue);
            text-decoration: none;
            font-weight: 800;
            white-space: nowrap;
        }}

        .coach-badges {{
            display: flex;
            flex-wrap: wrap;
            gap: 7px;
        }}

        .score-pill,
        .confidence {{
            display: inline-flex;
            align-items: center;
            min-height: 26px;
            padding: 0 9px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 900;
        }}

        .score-pill {{
            color: var(--blue);
            background: var(--blue-soft);
        }}

        .confidence {{
            color: var(--muted);
            background: #eef1f5;
        }}

        .resume-callout {{
            margin: 16px 0;
            padding: 12px 14px;
            border-radius: 11px;
            color: #174373;
            background: var(--blue-soft);
        }}

        .coach-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }}

        .coach-section {{
            padding: 14px;
            border: 1px solid var(--line);
            border-radius: 12px;
            background: var(--surface-soft);
        }}

        .coach-section h4 {{
            margin: 0;
        }}

        .application-strategy {{
            margin-top: 14px;
            padding: 14px;
            border-left: 4px solid var(--blue);
            border-radius: 8px;
            background: #f4f8ff;
            line-height: 1.55;
        }}

        .empty-coach {{
            margin-top: 16px;
            padding: 16px;
            border-radius: 10px;
            color: var(--muted);
            background: var(--surface-soft);
        }}

        .error-box {{
            margin-bottom: 18px;
            padding: 14px 16px;
            border: 1px solid #f2b8b5;
            border-radius: 12px;
            color: var(--red);
            background: var(--red-soft);
        }}

        .muted {{
            color: var(--muted);
        }}

        @media (max-width: 960px) {{
            .metric-grid {{
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }}

            .two-panel-grid,
            .analysis-grid {{
                grid-template-columns: 1fr;
            }}
        }}

        @media (max-width: 700px) {{
            .shell {{
                width: min(100% - 20px, 1240px);
            }}

            .hero-row,
            .coach-card-header {{
                flex-direction: column;
            }}

            .metric-grid,
            .tech-grid,
            .two-column,
            .coach-grid {{
                grid-template-columns: 1fr;
            }}

            .cert-card {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <header class="hero">
        <div class="shell">
            <div class="hero-row">
                <div>
                    <h1>Job Market Intelligence</h1>
                    <p>
                        A local, evidence-based view of your relevant job market,
                        followed by personalized coaching for the highest-ranked opportunities.
                    </p>
                    <div class="report-meta">
                        <span class="meta-pill">{escape(status_text)}</span>
                        <span class="meta-pill">Generated {escape(generated_at)}</span>
                        <span class="meta-pill">
                            Score threshold: {MARKET_MIN_SCORE:.0f}+
                        </span>
                    </div>
                </div>
                <a
                    class="back-link"
                    href="all_ats_job_dashboard.html"
                >
                    Back to jobs
                </a>
            </div>
        </div>
    </header>

    <div class="tabs-wrap">
        <nav class="shell tabs" aria-label="Intelligence sections">
            <button
                class="tab-button active"
                type="button"
                data-tab="market"
            >
                Market Intelligence
            </button>
            <button
                class="tab-button"
                type="button"
                data-tab="coach"
            >
                Top Opportunities Coach
            </button>
        </nav>
    </div>

    <main class="shell">
        {error_box}

        <section id="market" class="tab-panel active">
            <div class="metric-grid">
                <div class="metric">
                    <strong>{int(local_stats.get("relevant_job_count") or 0)}</strong>
                    <span>Relevant jobs analyzed</span>
                </div>
                <div class="metric">
                    <strong>{int(local_stats.get("strong_match_count") or 0)}</strong>
                    <span>Strong matches found</span>
                </div>
                <div class="metric">
                    <strong>{int(stat_lookup(local_stats, "Microsoft Azure")["count"])}</strong>
                    <span>Azure mentions</span>
                </div>
                <div class="metric">
                    <strong>{int(stat_lookup(local_stats, "Kubernetes")["count"])}</strong>
                    <span>Kubernetes mentions</span>
                </div>
            </div>

            <section class="section">
                <div class="section-title">
                    <div>
                        <h2>Market summary</h2>
                        <p>
                            Based on {int(local_stats.get("relevant_job_count") or 0)}
                            jobs scoring {MARKET_MIN_SCORE:.0f} or higher.
                        </p>
                    </div>
                </div>
                <div class="summary-card">
                    {escape(market_ai.get("executive_summary"))}
                </div>
            </section>

            <section class="section two-panel-grid">
                <div class="panel">
                    <h3>Most common requirements</h3>
                    {requirement_rows(top_requirements)}
                </div>
                <div class="panel">
                    <h3>Technology and platform mentions</h3>
                    <div class="tech-grid">
                        {technology_cards(technology_items)}
                    </div>
                </div>
            </section>

            <section class="section two-panel-grid">
                <div class="panel bullet-panel">
                    <h3>Your strongest market alignment</h3>
                    {list_html(
                        [
                            safe_text(value)
                            for value in market_ai.get(
                                "strongest_alignment",
                                [],
                            )
                        ]
                    )}
                </div>
                <div class="panel bullet-panel">
                    <h3>Current market signals</h3>
                    {list_html(
                        [
                            safe_text(value)
                            for value in market_ai.get(
                                "market_signals",
                                [],
                            )
                        ]
                    )}
                </div>
            </section>

            <section class="section">
                <div class="section-title">
                    <div>
                        <h2>Gaps and areas to strengthen</h2>
                        <p>
                            These are evidence gaps relative to the market,
                            not claims that you cannot do the work.
                        </p>
                    </div>
                </div>
                <div class="analysis-grid">
                    {gap_cards(market_ai.get("gaps", []))}
                </div>
            </section>

            <section class="section">
                <div class="section-title">
                    <div>
                        <h2>Certification priorities</h2>
                        <p>
                            Ranked using demand in the relevant listings and
                            your existing differentiation.
                        </p>
                    </div>
                </div>
                <div class="cert-list">
                    {certification_cards(
                        market_ai.get(
                            "certification_priorities",
                            [],
                        )
                    )}
                </div>
            </section>

            <section class="section">
                <div class="panel bullet-panel">
                    <h3>Recommended actions for the next 30 days</h3>
                    {list_html(
                        [
                            safe_text(value)
                            for value in market_ai.get(
                                "recommended_30_day_actions",
                                [],
                            )
                        ]
                    )}
                </div>
            </section>
        </section>

        <section id="coach" class="tab-panel">
            <div class="section-title">
                <div>
                    <h2>Top Opportunities Coach</h2>
                    <p>
                        Personalized preparation for the top
                        {len(top_jobs)} ranked jobs.
                    </p>
                </div>
            </div>
            <div class="coach-list">
                {coach_cards}
            </div>
        </section>
    </main>

    <script>
        const tabButtons = Array.from(
            document.querySelectorAll(".tab-button")
        );
        const tabPanels = Array.from(
            document.querySelectorAll(".tab-panel")
        );

        function activateTab(tabName, updateHash = true) {{
            const validName = (
                tabName === "coach"
                ? "coach"
                : "market"
            );

            tabButtons.forEach((button) => {{
                button.classList.toggle(
                    "active",
                    button.dataset.tab === validName,
                );
            }});

            tabPanels.forEach((panel) => {{
                panel.classList.toggle(
                    "active",
                    panel.id === validName,
                );
            }});

            if (updateHash) {{
                history.replaceState(
                    null,
                    "",
                    `#${{validName}}`,
                );
            }}

            window.scrollTo({{ top: 0, behavior: "instant" }});
        }}

        tabButtons.forEach((button) => {{
            button.addEventListener("click", () => {{
                activateTab(button.dataset.tab);
            }});
        }});

        window.addEventListener("hashchange", () => {{
            activateTab(
                location.hash.replace("#", ""),
                false,
            );
        }});

        activateTab(
            location.hash.replace("#", ""),
            false,
        );
    </script>
</body>
</html>
"""

    INTELLIGENCE_REPORT_FILE.write_text(
        page,
        encoding="utf-8",
    )

    return INTELLIGENCE_REPORT_FILE


# ============================================================
# DASHBOARD LINK INJECTION
# ============================================================

AGENT_LINK_MARKER_START = "<!-- JOB_AGENT_LINKS_START -->"
AGENT_LINK_MARKER_END = "<!-- JOB_AGENT_LINKS_END -->"


def inject_dashboard_links() -> None:
    if not DASHBOARD_FILE.exists():
        return

    try:
        dashboard = DASHBOARD_FILE.read_text(encoding="utf-8")
    except OSError:
        return

    marker_pattern = re.compile(
        re.escape(AGENT_LINK_MARKER_START)
        + r".*?"
        + re.escape(AGENT_LINK_MARKER_END),
        re.DOTALL,
    )

    dashboard = marker_pattern.sub("", dashboard)

    links = f"""
    {AGENT_LINK_MARKER_START}
    <div
        style="
            position:fixed;
            right:18px;
            bottom:18px;
            z-index:9999;
            display:flex;
            gap:8px;
            flex-wrap:wrap;
            justify-content:flex-end;
        "
    >
        <a
            href="job_intelligence.html#market"
            target="_blank"
            rel="noopener"
            style="
                display:inline-flex;
                align-items:center;
                min-height:42px;
                padding:0 14px;
                border-radius:11px;
                color:white;
                background:#1769e0;
                box-shadow:0 7px 20px rgba(20,60,120,.25);
                text-decoration:none;
                font-family:system-ui,-apple-system,sans-serif;
                font-size:13px;
                font-weight:800;
            "
        >
            Market Intelligence
        </a>
        <a
            href="job_intelligence.html#coach"
            target="_blank"
            rel="noopener"
            style="
                display:inline-flex;
                align-items:center;
                min-height:42px;
                padding:0 14px;
                border-radius:11px;
                color:white;
                background:#173d75;
                box-shadow:0 7px 20px rgba(20,60,120,.25);
                text-decoration:none;
                font-family:system-ui,-apple-system,sans-serif;
                font-size:13px;
                font-weight:800;
            "
        >
            Opportunity Coach
        </a>
    </div>
    {AGENT_LINK_MARKER_END}
    """

    body_close = dashboard.lower().rfind("</body>")

    if body_close == -1:
        return

    updated = (
        dashboard[:body_close]
        + links
        + dashboard[body_close:]
    )

    DASHBOARD_FILE.write_text(
        updated,
        encoding="utf-8",
    )


# ============================================================
# MAIN
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate local job-market intelligence and optional "
            "AI coaching from the latest ATS matches."
        )
    )

    parser.add_argument(
        "--refresh-ai",
        action="store_true",
        help="Ignore valid agent caches and regenerate AI analysis.",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Generate local statistics without making API calls.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=TOP_JOB_COUNT,
        help="Number of top jobs to coach. Default: 15.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    load_dotenv(PROJECT_FOLDER / ".env")

    api_key = safe_text(os.getenv("OPENAI_API_KEY"))
    model = safe_text(
        os.getenv("OPENAI_MODEL"),
        DEFAULT_MODEL,
    )

    jobs = load_matches()
    descriptions = build_description_index()
    jobs = attach_descriptions(jobs, descriptions)

    local_stats = build_local_market_stats(jobs)
    top_jobs = top_jobs_for_coaching(
        jobs,
        max(1, min(args.top, 20)),
    )

    market_fingerprint = market_ai_fingerprint(
        local_stats,
        model,
    )
    coach_fingerprint = coach_ai_fingerprint(
        top_jobs,
        model,
    )

    cached_market = load_cached_payload(
        MARKET_CACHE_FILE,
        market_fingerprint,
    )
    cached_coach = load_cached_payload(
        COACH_CACHE_FILE,
        coach_fingerprint,
    )

    market_ai = cached_market
    coaches = (
        cached_coach.get("jobs", [])
        if cached_coach
        else []
    )

    api_used = False
    error_messages = []

    can_call_ai = bool(api_key) and not args.no_ai

    if args.refresh_ai:
        market_ai = None
        coaches = []

    if market_ai is None and can_call_ai:
        try:
            print("Generating market intelligence...")
            market_ai = generate_market_ai(
                local_stats=local_stats,
                api_key=api_key,
                model=model,
            )
            save_cached_payload(
                MARKET_CACHE_FILE,
                market_fingerprint,
                model,
                market_ai,
            )
            api_used = True
        except Exception as error:
            error_messages.append(
                f"Market intelligence: {error}"
            )

    if not coaches and can_call_ai:
        try:
            print(
                f"Coaching the top {len(top_jobs)} opportunities..."
            )
            coaches = generate_all_coaches(
                top_jobs=top_jobs,
                api_key=api_key,
                model=model,
            )
            save_cached_payload(
                COACH_CACHE_FILE,
                coach_fingerprint,
                model,
                {"jobs": coaches},
            )
            api_used = True
        except Exception as error:
            error_messages.append(
                f"Opportunity coach: {error}"
            )

    if market_ai is None:
        market_ai = deterministic_market_fallback(
            local_stats
        )

    report = render_report(
        jobs=jobs,
        local_stats=local_stats,
        market_ai=market_ai,
        top_jobs=top_jobs,
        coaches=coaches,
        model=model,
        api_used=api_used,
        error_message=" | ".join(error_messages),
    )

    inject_dashboard_links()

    print()
    print("Job intelligence ready")
    print("-" * 50)
    print(
        "Relevant jobs analyzed: "
        f"{local_stats['relevant_job_count']}"
    )
    print(f"Top jobs coached: {len(coaches)}")
    print(f"Report: {report}")

    if not api_key and not args.no_ai:
        print()
        print(
            "No OPENAI_API_KEY was found. Local counts were generated, "
            "but fresh AI narrative/coaching was not requested."
        )
        print(
            "Add OPENAI_API_KEY to a .env file, then run: "
            "python job_agent.py --refresh-ai"
        )


if __name__ == "__main__":
    main()
