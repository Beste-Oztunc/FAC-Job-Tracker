import contextlib
import hashlib
import io
import html
import json
import math
import re
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from companies import build_ats_company_dict


# ============================================================
# SETTINGS
# ============================================================

GREENHOUSE_COMPANIES = build_ats_company_dict(
    "greenhouse",
    max_priority=1,
)

ASHBY_COMPANIES = build_ats_company_dict(
    "ashby",
    max_priority=1,
)

LEVER_COMPANIES = build_ats_company_dict(
    "lever",
    max_priority=1,
)

ENABLE_GREENHOUSE = True
ENABLE_ASHBY = True
ENABLE_LEVER = True

# Ashby job-board names are usually the final part of a hosted
# jobs.ashbyhq.com URL. Add exceptions here when a company's public
# board name differs from the normalized company name.
ASHBY_TOKEN_OVERRIDES = {
    "OpenAI": "openai",
    "Airwallex": "airwallex",
    "UiPath": "uipath",
    "Unit": "unit",
    "Ramp": "ramp",
    "Brex": "brex",
    "Mercury": "mercury",
    "Plaid": "plaid",
    "Anthropic": "anthropic",
}

for company_name, board_name in ASHBY_TOKEN_OVERRIDES.items():
    if company_name in ASHBY_COMPANIES:
        ASHBY_COMPANIES[company_name] = board_name

# Lever site names are normally the final path component of
# jobs.lever.co/<site>. Put confirmed exceptions here.
LEVER_TOKEN_OVERRIDES = {
}

# Most Lever boards use the global instance. When a confirmed board
# uses jobs.eu.lever.co, list the company here with the value "eu".
LEVER_INSTANCE_OVERRIDES = {
}

for company_name, site_name in LEVER_TOKEN_OVERRIDES.items():
    if company_name in LEVER_COMPANIES:
        LEVER_COMPANIES[company_name] = site_name

__version__ = "4.7-quiet-final"

MIN_POSSIBLE_SCORE = 50
MIN_STRONG_SCORE = 65

OUTPUT_FOLDER = Path("output")
ALL_US_JOBS_FILE = OUTPUT_FOLDER / "all_ats_us_jobs.json"
MATCHES_FILE = OUTPUT_FOLDER / "all_ats_matches.json"
REPORT_FILE = OUTPUT_FOLDER / "all_ats_job_dashboard.html"

GREENHOUSE_BOARD_CACHE_FILE = (
    OUTPUT_FOLDER / "greenhouse_board_cache.json"
)
ASHBY_BOARD_CACHE_FILE = (
    OUTPUT_FOLDER / "ashby_board_cache.json"
)
LEVER_BOARD_CACHE_FILE = (
    OUTPUT_FOLDER / "lever_board_cache.json"
)

GREENHOUSE_ACTIVE_BOARDS_FILE = (
    OUTPUT_FOLDER / "greenhouse_active_boards.json"
)
ASHBY_ACTIVE_BOARDS_FILE = (
    OUTPUT_FOLDER / "ashby_active_boards.json"
)
LEVER_ACTIVE_BOARDS_FILE = (
    OUTPUT_FOLDER / "lever_active_boards.json"
)
JOB_SCORE_CACHE_FILE = (
    OUTPUT_FOLDER / "job_score_cache.json"
)
JOB_HISTORY_FILE = (
    OUTPUT_FOLDER / "job_history.json"
)

INCLUDE_ASHBY_COMPENSATION = True

# Each ATS keeps its existing per-host concurrency, while the three
# ATS collectors run at the same time.
MAX_DOWNLOAD_WORKERS = 24
COLLECT_ATS_SOURCES_IN_PARALLEL = True

# Normal runs query only boards that have previously been confirmed.
# A full discovery automatically runs once a week, or immediately
# when no discovery/cache data exists.
BOARD_DISCOVERY_INTERVAL_DAYS = 7
FORCE_FULL_DISCOVERY = False
DISCOVERY_REQUESTED = (
    FORCE_FULL_DISCOVERY
    or "--discover" in sys.argv
)

# Lever EU discovery is deliberately opt-in. Most Lever boards are
# global; automatically retrying every missing board against the EU
# endpoint nearly doubles Lever discovery requests.
TRY_LEVER_EU_DURING_DISCOVERY = False

REQUEST_CONNECT_TIMEOUT_SECONDS = 4
REQUEST_READ_TIMEOUT_SECONDS = 20

# Incremental scoring reuses results for unchanged jobs and
# automatically invalidates when scoring/location rules change.
ENABLE_INCREMENTAL_SCORING = True
SCORING_CACHE_VERSION = "v4-fast-hard-prefilter"

# Reuse fresh active-board results for four hours.
ACTIVE_BOARD_CACHE_HOURS = 4

# Recheck missing/invalid board guesses only every 30 days.
MISSING_BOARD_CACHE_DAYS = 30

# Skip the enormous all-U.S.-jobs debug file by default.
# It contains thousands of descriptions and is expensive to serialize.
SAVE_ALL_US_JOBS_JSON = False

# Do not copy full descriptions into the final match JSON/report data.
# The description is used for scoring, then discarded from output.
KEEP_DESCRIPTIONS_IN_OUTPUT = False

# Show visible progress while filtering and scoring.
SCORING_PROGRESS_INTERVAL = 500

# ============================================================
# SCORE NORMALIZATION
# ============================================================

# Raw rule points remain unchanged and are still shown in the report.
# A fixed monotonic Hill curve converts the raw total to a stable
# 0-100 score without hard-capping or category caps.
#
# These anchors are preserved exactly:
# raw 50 -> normalized 50
# raw 65 -> normalized 65
#
# Higher raw scores approach 100 gradually but never exceed it.
NORMALIZATION_SCALE = 50.0
NORMALIZATION_EXPONENT = (
    math.log(65.0 / 35.0)
    / math.log(65.0 / NORMALIZATION_SCALE)
)


# ============================================================
# SCORING RULES
# ============================================================

TARGET_ROLE_PHRASES = {
    # Best-fit customer-facing technical roles
    "presales engineer": 40,
    "pre-sales engineer": 40,
    "solutions engineer": 40,
    "solution engineer": 40,
    "solutions consultant": 38,
    "solution consultant": 38,
    "sales engineer": 40,
    "presales": 40,
    "pre-sales": 40,
    "customer engineer": 34,
    "technical solutions": 32,
    "technical consultant": 32,
    "technical success": 30,
    "forward deployed": 30,
    "solutions architect": 32,
    "solution architect": 32,
    "risk consultant": 30,
    "fraud consultant": 30,

    # Data-science / economics roles
    "data scientist": 28,
    "ML engineer": 28,
    "Machine Learning Engineer": 28,
    "Applied AI Engineer": 28,
    "decision scientist": 28,
    "economist": 10,
}

TITLE_SPECIALTY_TERMS = {
    # Specialty points are intentionally lower than full role points.
    "risk": 15,
    "credit risk": 15,
    "model risk": 15,
    "fraud": 10,
    "ai governance": 12,
    "responsible ai": 12,
    "generative ai": 8,
    "agentic ai": 8,
    "financial services": 12,
    "auto finance": 10,
    "decisioning": 15,
    "underwriting": 6,
    "collections": 8,
    "origination": 8,
    "lending": 8,
    "banking": 8,
    "fintech": 8,
    "machine learning": 8,
    "predictive model": 8,
    "fraud strategy": 6,
    "kyc": 5,
    "aml": 3,
    "insurance": 2,
}

DOMAIN_TERMS = {
    "risk": 6,
    "fraud": 5,
    "credit": 6,
    "lending": 6,
    "banking": 6,
    "fintech": 6,
    "decisioning": 6,
    "underwriting": 5,
    "collections": 5,
    "origination": 6,
    "aml": 2,
    "kyc": 5,
    "model risk": 6,
    "model governance": 6,
    "responsible ai": 6,
    "ai governance": 6,
    "artificial intelligence": 5,
    "generative ai": 5,
    "agentic ai": 5,
    "machine learning": 6,
    "predictive model": 6,
    "financial services": 6,
    "insurance": 2,
    "auto finance": 5,
}

CUSTOMER_FACING_TERMS = {
    "customer-facing": 6,
    "client-facing": 6,
    "pre-sales": 5,
    "presales": 5,
    "proof of concept": 5,
    "proof-of-concept": 5,
    "poc": 5,
    "demonstration": 5,
    "demo": 5,
    "discovery": 4,
    "solution design": 5,
    "technical sales": 5,
    "sales cycle": 4,
    "account executive": 2,
    "executive presentation": 5,
    "workshop": 4,
    "request for proposal": 5,
    "rfp": 5,
    "C-level presentation": 5,
}

EXCLUDED_TITLE_TERMS = [
    "intern",
    "internship",
    "new grad",
    "graduate program",
    "registered nurse",
    "physician",
    "attorney",
    "paralegal",
    "recruiter",
    "talent acquisition",
    "business development representative",
    "customer support representative",
    "frontend engineer",
    "front-end engineer",
    "mobile engineer",
    "ios engineer",
    "android engineer",
    "site reliability engineer",
    "devops engineer",
    "network engineer",
    "hardware engineer",
    "software developer",
    "software engineer",
    "Fullstack engineer",
    "fullstack developer"
]

# These levels are rejected before scoring and never reach the report.
EXCLUDED_EXPERIENCE_LEVELS = {
    "Intern",
    "Entry",
}

# These terms cause immediate rejection before history tracking,
# U.S. filtering, scoring, normalization, or score-cache lookup.
HARD_ENTRY_TITLE_TERMS = [
    "intern",
    "internship",
    "co-op",
    "co op",
    "new grad",
    "new graduate",
    "recent graduate",
    "graduate program",
    "graduate scheme",
    "early career",
    "entry level",
    "entry-level",
    "junior",
    "jr.",
    "trainee",
    "apprentice",
    "apprenticeship",
    "campus hire",
    "campus recruiting",
    "rotational program",
]

HARD_ENTRY_EMPLOYMENT_TERMS = [
    "intern",
    "internship",
    "co-op",
    "co op",
    "apprentice",
    "apprenticeship",
    "trainee",
]

ENTRY_DESCRIPTION_PATTERNS = [
    r"\bthis is an? entry[- ]level (?:role|position|opportunity)\b",
    r"\bentry[- ]level (?:role|position|opportunity|candidate)\b",
    r"\bideal for (?:a )?(?:recent|new) graduate",
    r"\b(?:recent|new) graduates? (?:are )?(?:encouraged|welcome)",
    r"\bno (?:prior|professional|relevant) experience (?:is )?required\b",
    r"\bno experience (?:is )?required\b",
    r"\bearly[- ]career (?:role|position|opportunity|program)\b",
    r"\bcampus (?:hire|recruiting|program)\b",
]

ENTRY_DESCRIPTION_REGEX = re.compile(
    "|".join(
        f"(?:{pattern})"
        for pattern in ENTRY_DESCRIPTION_PATTERNS
    ),
    re.IGNORECASE,
)

EXPERIENCE_REQUIREMENT_REGEX = re.compile(
    r"""
    \b
    (?:
        minimum\s+(?:of\s+)?
        |at\s+least\s+
        |up\s+to\s+
        |less\s+than\s+
    )?
    (?P<years>\d{1,2})
    \s*
    (?:
        \+
        |[-–—]\s*\d{1,2}
        |to\s+\d{1,2}
    )?
    \s*
    (?:years?|yrs?)
    (?:\s+of)?
    \s+
    (?:
        relevant\s+
        |professional\s+
        |industry\s+
        |work\s+
    )?
    experience
    \b
    |
    \bexperience\s*[:\-]\s*
    (?P<years_after>\d{1,2})
    \s*
    (?:
        \+
        |[-–—]\s*\d{1,2}
        |to\s+\d{1,2}
    )?
    \s*(?:years?|yrs?)
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

NO_EXPERIENCE_REQUIRED_REGEX = re.compile(
    r"""
    \b
    no\s+
    (?:
        prior\s+
        |professional\s+
        |relevant\s+
    )?
    experience\s+
    (?:is\s+)?
    required
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

SENIORITY_TERMS = [
    "senior",
    "lead",
    "principal",
    "staff",
    "manager",
    "director",
]


# ============================================================
# LOCATION RULES
# ============================================================

LOCAL_AREA_TERMS = [
    "raleigh",
    "durham",
    "chapel hill",
    "research triangle",
    "research triangle park",
    "triangle area",
    "rtp",
    "morrisville",
    "cary",
]

REMOTE_TERMS = [
    "remote",
    "work from home",
    "home-based",
    "home based",
    "virtual position",
    "virtual role",
]

HYBRID_TERMS = [
    "hybrid",
    "days in office",
    "days per week in office",
    "days in the office",
]

ONSITE_TERMS = [
    "on-site",
    "onsite",
    "in-office",
    "in office",
    "office-based",
    "office based",
]

US_STATE_NAMES = [
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "district of columbia",
]

US_STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

US_GENERAL_TERMS = [
    "united states",
    "usa",
    "u.s.a.",
    "u.s.",
    "us only",
    "u.s. only",
    "us remote",
    "remote - us",
    "remote, us",
    "remote us",
    "nationwide",
    "washington, dc",
]

NON_US_LOCATION_TERMS = [
    "united kingdom",
    "england",
    "scotland",
    "wales",
    "ireland",
    "canada",
    "europe",
    "emea",
    "india",
    "australia",
    "singapore",
    "germany",
    "france",
    "spain",
    "italy",
    "netherlands",
    "belgium",
    "switzerland",
    "austria",
    "poland",
    "portugal",
    "sweden",
    "norway",
    "denmark",
    "finland",
    "czech",
    "romania",
    "hungary",
    "greece",
    "israel",
    "turkey",
    "united arab emirates",
    "uae",
    "dubai",
    "saudi arabia",
    "south africa",
    "brazil",
    "mexico",
    "argentina",
    "colombia",
    "chile",
    "japan",
    "china",
    "hong kong",
    "taiwan",
    "south korea",
    "philippines",
    "malaysia",
    "indonesia",
    "thailand",
    "vietnam",
    "new zealand",
]


# ============================================================
# TEXT HELPERS
# ============================================================

def safe_text(value: object, default: str = "") -> str:
    if value is None:
        return default

    text = str(value).strip()
    return text or default


def clean_html(raw_html: object) -> str:
    text = safe_text(raw_html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def cached_lower_job_text(
    job: dict,
    field: str,
    default: str = "",
) -> str:
    """
    Lowercase each job field only once.

    The previous version repeatedly lowercased the same long description
    during U.S. filtering, location detection, and rule scoring.
    """

    cache_key = f"__lower_{field}"

    cached_value = job.get(cache_key)

    if isinstance(cached_value, str):
        return cached_value

    lowered = safe_text(
        job.get(field),
        default,
    ).lower()

    job[cache_key] = lowered
    return lowered


def is_word_character(character: str) -> bool:
    return character == "_" or character.isalnum()


def find_whole_term_spans(
    lowered_text: str,
    lowered_term: str,
) -> list[tuple[int, int]]:
    """
    Find whole terms using fast string search instead of recompiling
    a regular expression for every term and every job.
    """

    spans = []
    start_at = 0
    term_length = len(lowered_term)

    if term_length == 0:
        return spans

    while True:
        index = lowered_text.find(
            lowered_term,
            start_at,
        )

        if index == -1:
            break

        end = index + term_length

        left_is_clear = (
            index == 0
            or not is_word_character(lowered_text[index - 1])
        )

        right_is_clear = (
            end == len(lowered_text)
            or not is_word_character(lowered_text[end])
        )

        if left_is_clear and right_is_clear:
            spans.append((index, end))

        start_at = index + 1

    return spans


def contains_term(lowered_text: str, term: str) -> bool:
    """
    Match a complete term.

    All callers pass already-lowercased text, so the long description is
    not lowercased again for every dictionary entry.
    """

    lowered_term = term.lower()
    start_at = 0
    term_length = len(lowered_term)

    if term_length == 0:
        return False

    while True:
        index = lowered_text.find(
            lowered_term,
            start_at,
        )

        if index == -1:
            return False

        end = index + term_length

        left_is_clear = (
            index == 0
            or not is_word_character(lowered_text[index - 1])
        )

        right_is_clear = (
            end == len(lowered_text)
            or not is_word_character(lowered_text[end])
        )

        if left_is_clear and right_is_clear:
            return True

        start_at = index + 1


def contains_any(
    lowered_text: str,
    terms: list[str],
) -> bool:
    return any(
        contains_term(lowered_text, term)
        for term in terms
    )


def compile_term_group(terms: list[str]) -> re.Pattern:
    """
    Compile one boundary-aware expression for a large term group.

    This is mainly used for the 50 U.S. state names, replacing fifty
    separate scans of every job description.
    """

    alternatives = "|".join(
        re.escape(term.lower())
        for term in sorted(
            set(terms),
            key=len,
            reverse=True,
        )
    )

    return re.compile(
        rf"(?<!\w)(?:{alternatives})(?!\w)"
    )


US_STATE_NAME_PATTERN = compile_term_group(
    US_STATE_NAMES
)


def contains_us_state_name(lowered_text: str) -> bool:
    return US_STATE_NAME_PATTERN.search(
        lowered_text
    ) is not None


def contains_us_state_abbreviation(value: str) -> bool:
    candidates = re.findall(
        r"(?:^|[,/\s-])([A-Z]{2})(?=$|[,/\s-])",
        value.upper(),
    )
    return any(
        code in US_STATE_ABBREVIATIONS
        for code in candidates
    )



# ============================================================
# FAST HTTP, BOARD DISCOVERY, AND INCREMENTAL CACHE HELPERS
# ============================================================

_HTTP_THREAD_LOCAL = threading.local()


def get_http_session() -> requests.Session:
    """
    Reuse HTTPS connections within each worker thread.

    The three ATS platforms use different hosts, so each source keeps
    its existing per-host concurrency while all sources can run in
    parallel.
    """

    existing = getattr(
        _HTTP_THREAD_LOCAL,
        "session",
        None,
    )

    if isinstance(existing, requests.Session):
        return existing

    retry_policy = Retry(
        total=2,
        connect=2,
        read=1,
        status=1,
        backoff_factor=0.25,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry_policy,
        pool_connections=MAX_DOWNLOAD_WORKERS,
        pool_maxsize=MAX_DOWNLOAD_WORKERS,
    )

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "UnifiedATSJobCollector/1.0"
            ),
            "Accept": "application/json",
        }
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    _HTTP_THREAD_LOCAL.session = session
    return session


def request_timeout() -> tuple[int, int]:
    return (
        REQUEST_CONNECT_TIMEOUT_SECONDS,
        REQUEST_READ_TIMEOUT_SECONDS,
    )


def active_registry_file(source_key: str) -> Path:
    files = {
        "greenhouse": GREENHOUSE_ACTIVE_BOARDS_FILE,
        "ashby": ASHBY_ACTIVE_BOARDS_FILE,
        "lever": LEVER_ACTIVE_BOARDS_FILE,
    }

    return files[source_key]


def load_active_board_registry(
    source_key: str,
) -> dict:
    registry_file = active_registry_file(source_key)

    if not registry_file.exists():
        return {
            "source": source_key,
            "last_discovery_at": "",
            "boards": {},
        }

    try:
        payload = json.loads(
            registry_file.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {
            "source": source_key,
            "last_discovery_at": "",
            "boards": {},
        }

    if not isinstance(payload, dict):
        payload = {}

    boards = payload.get("boards")

    if not isinstance(boards, dict):
        boards = {}

    return {
        "source": source_key,
        "last_discovery_at": safe_text(
            payload.get("last_discovery_at")
        ),
        "boards": boards,
    }


def save_active_board_registry(
    source_key: str,
    registry: dict,
) -> None:
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    registry_file = active_registry_file(source_key)
    temporary_file = registry_file.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(
            registry,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    temporary_file.replace(registry_file)


def bootstrap_registry_from_cache(
    source_key: str,
    registry: dict,
    cache: dict,
) -> bool:
    """
    Convert the user's existing ATS caches into an active-board
    registry, so upgrading does not trigger another full discovery.
    """

    boards = registry.setdefault("boards", {})
    changed = False

    for cache_key, entry in cache.items():
        if not isinstance(entry, dict):
            continue

        if safe_text(entry.get("status")).lower() != "active":
            continue

        board_token = safe_text(
            entry.get("board_token"),
            cache_key,
        )

        normalized_key = board_token.lower()

        if normalized_key in boards:
            continue

        boards[normalized_key] = {
            "company": safe_text(
                entry.get("company"),
                "Unknown company",
            ),
            "board_token": board_token,
            "instance": safe_text(
                entry.get("instance")
            ),
            "last_seen_at": safe_text(
                entry.get("checked_at")
            ),
        }

        changed = True

    # Existing cache entries mean a discovery has already happened.
    # Record it now so the first optimized run uses only confirmed
    # boards rather than repeating hundreds of guesses.
    if cache and not safe_text(
        registry.get("last_discovery_at")
    ):
        registry["last_discovery_at"] = (
            utc_now().isoformat()
        )
        changed = True

    if changed:
        save_active_board_registry(
            source_key,
            registry,
        )

    return changed


def discovery_is_due(registry: dict) -> bool:
    if DISCOVERY_REQUESTED:
        return True

    last_discovery = parse_cache_time(
        registry.get("last_discovery_at")
    )

    if last_discovery is None:
        return True

    return (
        utc_now() - last_discovery
        >= timedelta(
            days=BOARD_DISCOVERY_INTERVAL_DAYS
        )
    )


def cache_entry_is_fresh_for_collection(
    entry: dict,
    full_discovery: bool,
) -> bool:
    checked_at = parse_cache_time(entry.get("checked_at"))

    if checked_at is None:
        return False

    age = utc_now() - checked_at
    status = safe_text(entry.get("status")).lower()

    if status == "active":
        return age <= timedelta(
            hours=ACTIVE_BOARD_CACHE_HOURS
        )

    # Missing boards are only relevant in discovery mode. A discovery
    # requested twice on the same day should not repeat every 404.
    if status == "missing" and full_discovery:
        return age <= timedelta(hours=24)

    return False


def registry_candidates(
    companies: dict[str, str],
    registry: dict,
    full_discovery: bool,
) -> list[tuple[str, str, dict]]:
    if full_discovery:
        return [
            (
                company,
                board_token,
                {},
            )
            for company, board_token in companies.items()
        ]

    candidates = []

    for entry in registry.get("boards", {}).values():
        if not isinstance(entry, dict):
            continue

        company = safe_text(
            entry.get("company"),
            "Unknown company",
        )
        board_token = safe_text(
            entry.get("board_token")
        )

        if not board_token:
            continue

        candidates.append(
            (
                company,
                board_token,
                entry,
            )
        )

    return candidates


def update_registry_from_result(
    source_key: str,
    registry: dict,
    result: dict,
) -> None:
    boards = registry.setdefault("boards", {})
    board_token = safe_text(
        result.get("board_token")
    )

    if not board_token:
        return

    cache_key = board_token.lower()
    status = safe_text(
        result.get("status")
    ).lower()

    if status == "active":
        boards[cache_key] = {
            "company": safe_text(
                result.get("company"),
                "Unknown company",
            ),
            "board_token": board_token,
            "instance": safe_text(
                result.get("instance")
            ),
            "last_seen_at": utc_now().isoformat(),
        }

    elif status == "missing":
        boards.pop(cache_key, None)


def empty_collection_stats() -> dict:
    return {
        "cached_active": 0,
        "cached_missing": 0,
        "network_requests": 0,
        "active_boards": 0,
        "missing_boards": 0,
        "errors": 0,
        "registry_boards": 0,
        "candidate_boards": 0,
        "discovery_mode": False,
    }


def job_cache_key(job: dict) -> str:
    identifier = safe_text(
        job.get("id")
        or job.get("apply_url")
    )

    return "|".join(
        [
            safe_text(job.get("source")).lower(),
            safe_text(job.get("company")).lower(),
            identifier,
        ]
    )


def stable_json_hash(value: object) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def scoring_rules_fingerprint() -> str:
    return stable_json_hash(
        {
            "cache_version": SCORING_CACHE_VERSION,
            "target_roles": TARGET_ROLE_PHRASES,
            "title_specialties": TITLE_SPECIALTY_TERMS,
            "domains": DOMAIN_TERMS,
            "customer_facing": CUSTOMER_FACING_TERMS,
            "excluded_titles": EXCLUDED_TITLE_TERMS,
            "excluded_experience_levels": sorted(
                EXCLUDED_EXPERIENCE_LEVELS
            ),
            "hard_entry_title_terms": HARD_ENTRY_TITLE_TERMS,
            "hard_entry_employment_terms": (
                HARD_ENTRY_EMPLOYMENT_TERMS
            ),
            "entry_description_patterns": (
                ENTRY_DESCRIPTION_PATTERNS
            ),
            "seniority": SENIORITY_TERMS,
            "local_areas": LOCAL_AREA_TERMS,
            "remote_terms": REMOTE_TERMS,
            "hybrid_terms": HYBRID_TERMS,
            "onsite_terms": ONSITE_TERMS,
            "us_states": US_STATE_NAMES,
            "us_general": US_GENERAL_TERMS,
            "non_us": NON_US_LOCATION_TERMS,
            "possible_threshold": MIN_POSSIBLE_SCORE,
            "strong_threshold": MIN_STRONG_SCORE,
            "normalization_scale": NORMALIZATION_SCALE,
            "normalization_exponent": (
                NORMALIZATION_EXPONENT
            ),
        }
    )


def job_content_fingerprint(job: dict) -> str:
    return stable_json_hash(
        {
            "title": job.get("title"),
            "location": job.get("location"),
            "description": job.get("description"),
            "updated_at": job.get("updated_at"),
            "apply_url": job.get("apply_url"),
            "workplace_type": job.get(
                "workplace_type"
            ),
            "is_remote": job.get("is_remote"),
            "country": job.get("country"),
            "secondary_countries": job.get(
                "secondary_countries"
            ),
            "employment_type": job.get(
                "employment_type"
            ),
        }
    )


def load_job_score_cache() -> dict:
    rules_hash = scoring_rules_fingerprint()

    if not (
        ENABLE_INCREMENTAL_SCORING
        and JOB_SCORE_CACHE_FILE.exists()
    ):
        return {
            "rules_fingerprint": rules_hash,
            "jobs": {},
        }

    try:
        payload = json.loads(
            JOB_SCORE_CACHE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    if safe_text(
        payload.get("rules_fingerprint")
    ) != rules_hash:
        return {
            "rules_fingerprint": rules_hash,
            "jobs": {},
        }

    jobs = payload.get("jobs")

    if not isinstance(jobs, dict):
        jobs = {}

    return {
        "rules_fingerprint": rules_hash,
        "jobs": jobs,
    }


def save_job_score_cache(cache: dict) -> None:
    if not ENABLE_INCREMENTAL_SCORING:
        return

    OUTPUT_FOLDER.mkdir(exist_ok=True)

    temporary_file = JOB_SCORE_CACHE_FILE.with_suffix(
        ".tmp"
    )

    temporary_file.write_text(
        json.dumps(
            cache,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    temporary_file.replace(JOB_SCORE_CACHE_FILE)

# ============================================================
# GREENHOUSE
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_cache_time(value: object) -> datetime | None:
    text = safe_text(value)

    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def load_board_cache() -> dict:
    if not GREENHOUSE_BOARD_CACHE_FILE.exists():
        return {}

    try:
        payload = json.loads(
            GREENHOUSE_BOARD_CACHE_FILE.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}

    return payload if isinstance(payload, dict) else {}


def save_board_cache(cache: dict) -> None:
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    temporary_file = GREENHOUSE_BOARD_CACHE_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(
            cache,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary_file.replace(GREENHOUSE_BOARD_CACHE_FILE)


def cached_board_is_fresh(entry: dict) -> bool:
    checked_at = parse_cache_time(entry.get("checked_at"))

    if checked_at is None:
        return False

    age = utc_now() - checked_at
    status = safe_text(entry.get("status")).lower()

    if status == "active":
        return age <= timedelta(hours=ACTIVE_BOARD_CACHE_HOURS)

    if status == "missing":
        return age <= timedelta(days=MISSING_BOARD_CACHE_DAYS)

    return False


def normalize_greenhouse_jobs(
    company: str,
    raw_jobs: object,
) -> list[dict]:
    normalized = []

    if not isinstance(raw_jobs, list):
        return normalized

    for job in raw_jobs:
        if not isinstance(job, dict):
            continue

        location_data = job.get("location") or {}

        if not isinstance(location_data, dict):
            location_data = {}

        normalized.append(
            {
                "id": safe_text(job.get("id")),
                "company": safe_text(company, "Unknown company"),
                "title": safe_text(
                    job.get("title"),
                    "Unknown title",
                ),
                "location": safe_text(
                    location_data.get("name"),
                    "Location not listed",
                ),
                "description": clean_html(job.get("content")),
                "updated_at": safe_text(job.get("updated_at")),
                "apply_url": safe_text(job.get("absolute_url")),
                "source": "Greenhouse",
            }
        )

    return normalized


def fetch_greenhouse_board(
    company: str,
    board_token: str,
) -> dict:
    url = (
        "https://boards-api.greenhouse.io/v1/boards/"
        f"{board_token}/jobs"
    )

    try:
        response = get_http_session().get(
            url,
            params={"content": "true"},
            timeout=request_timeout(),
        )

        if response.status_code == 404:
            return {
                "company": company,
                "board_token": board_token,
                "status": "missing",
                "jobs": [],
                "error": "",
            }

        response.raise_for_status()
        payload = response.json()
        raw_jobs = payload.get("jobs") or []

        return {
            "company": company,
            "board_token": board_token,
            "status": "active",
            "jobs": normalize_greenhouse_jobs(
                company,
                raw_jobs,
            ),
            "error": "",
        }

    except requests.RequestException as error:
        return {
            "company": company,
            "board_token": board_token,
            "status": "error",
            "jobs": [],
            "error": str(error),
        }

    except (TypeError, ValueError) as error:
        return {
            "company": company,
            "board_token": board_token,
            "status": "error",
            "jobs": [],
            "error": str(error),
        }


def collect_source_jobs_fast(
    source_key: str,
    display_name: str,
    companies: dict[str, str],
    load_cache_function,
    save_cache_function,
    fetch_function,
) -> tuple[list[dict], dict]:
    cache = load_cache_function()
    registry = load_active_board_registry(source_key)

    bootstrap_registry_from_cache(
        source_key,
        registry,
        cache,
    )

    full_discovery = discovery_is_due(registry)

    candidates = registry_candidates(
        companies,
        registry,
        full_discovery,
    )

    all_jobs = []
    pending = []
    stats = empty_collection_stats()
    stats["discovery_mode"] = full_discovery
    stats["candidate_boards"] = len(candidates)

    for company, board_token, registry_entry in candidates:
        cache_key = board_token.lower()
        cached_entry = cache.get(cache_key)

        if (
            isinstance(cached_entry, dict)
            and cache_entry_is_fresh_for_collection(
                cached_entry,
                full_discovery,
            )
        ):
            status = safe_text(
                cached_entry.get("status")
            ).lower()

            if status == "active":
                cached_jobs = cached_entry.get("jobs") or []

                if isinstance(cached_jobs, list):
                    all_jobs.extend(cached_jobs)

                stats["cached_active"] += 1
                stats["active_boards"] += 1

                update_registry_from_result(
                    source_key,
                    registry,
                    {
                        "company": company,
                        "board_token": board_token,
                        "status": "active",
                        "instance": safe_text(
                            cached_entry.get("instance")
                        ),
                    },
                )

            elif status == "missing":
                stats["cached_missing"] += 1
                stats["missing_boards"] += 1

                update_registry_from_result(
                    source_key,
                    registry,
                    {
                        "company": company,
                        "board_token": board_token,
                        "status": "missing",
                    },
                )

            continue

        pending.append(
            (
                company,
                board_token,
                registry_entry,
            )
        )

    mode_label = (
        "full discovery"
        if full_discovery
        else "confirmed boards only"
    )

    print(
        f"{display_name}: {mode_label}; "
        f"{len(candidates)} candidate boards, "
        f"{len(pending)} network refreshes."
    )

    completed_since_save = 0

    with ThreadPoolExecutor(
        max_workers=MAX_DOWNLOAD_WORKERS
    ) as executor:
        future_map = {
            executor.submit(
                fetch_function,
                company,
                board_token,
                registry_entry,
                full_discovery,
            ): (company, board_token)
            for company, board_token, registry_entry in pending
        }

        for future in as_completed(future_map):
            company, board_token = future_map[future]

            try:
                result = future.result()
            except Exception as error:
                result = {
                    "company": company,
                    "board_token": board_token,
                    "status": "error",
                    "jobs": [],
                    "instance": "",
                    "request_count": 1,
                    "error": str(error),
                }

            status = safe_text(
                result.get("status")
            ).lower()
            jobs = result.get("jobs") or []

            stats["network_requests"] += int(
                result.get("request_count", 1)
            )

            # Temporary errors are not cached and do not remove a
            # previously confirmed board from the registry.
            if status in {"active", "missing"}:
                cache[board_token.lower()] = {
                    "company": company,
                    "board_token": board_token,
                    "status": status,
                    "checked_at": utc_now().isoformat(),
                    "jobs": (
                        jobs
                        if status == "active"
                        else []
                    ),
                    "instance": safe_text(
                        result.get("instance")
                    ),
                }

                update_registry_from_result(
                    source_key,
                    registry,
                    result,
                )

            if status == "active":
                stats["active_boards"] += 1

                if isinstance(jobs, list):
                    all_jobs.extend(jobs)

            elif status == "missing":
                stats["missing_boards"] += 1

            else:
                stats["errors"] += 1
                print(
                    f"{display_name} — {company}: "
                    f"request failed — "
                    f"{safe_text(result.get('error'))}"
                )

            completed_since_save += 1

            if completed_since_save >= 25:
                save_cache_function(cache)
                save_active_board_registry(
                    source_key,
                    registry,
                )
                completed_since_save = 0

    if full_discovery:
        registry["last_discovery_at"] = (
            utc_now().isoformat()
        )

    stats["registry_boards"] = len(
        registry.get("boards", {})
    )

    save_cache_function(cache)
    save_active_board_registry(
        source_key,
        registry,
    )

    return all_jobs, stats


def collect_greenhouse_jobs(
    companies: dict[str, str],
) -> tuple[list[dict], dict]:
    return collect_source_jobs_fast(
        source_key="greenhouse",
        display_name="Greenhouse",
        companies=companies,
        load_cache_function=load_board_cache,
        save_cache_function=save_board_cache,
        fetch_function=(
            lambda company, board_token, _entry, _discovery:
            fetch_greenhouse_board(
                company,
                board_token,
            )
        ),
    )


# ============================================================
# ASHBY
# ============================================================

def load_ashby_board_cache() -> dict:
    if not ASHBY_BOARD_CACHE_FILE.exists():
        return {}

    try:
        payload = json.loads(
            ASHBY_BOARD_CACHE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return {}

    return payload if isinstance(payload, dict) else {}


def save_ashby_board_cache(cache: dict) -> None:
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    temporary_file = ASHBY_BOARD_CACHE_FILE.with_suffix(
        ".tmp"
    )

    temporary_file.write_text(
        json.dumps(
            cache,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    temporary_file.replace(ASHBY_BOARD_CACHE_FILE)


def extract_country_from_ashby_address(
    address: object,
) -> str:
    if not isinstance(address, dict):
        return ""

    postal_address = address.get("postalAddress") or {}

    if not isinstance(postal_address, dict):
        return ""

    return safe_text(
        postal_address.get("addressCountry")
    )


def extract_secondary_ashby_locations(
    secondary_locations: object,
) -> tuple[list[str], list[str]]:
    locations = []
    countries = []

    if not isinstance(secondary_locations, list):
        return locations, countries

    for item in secondary_locations:
        if not isinstance(item, dict):
            continue

        location_name = safe_text(item.get("location"))

        if location_name and location_name not in locations:
            locations.append(location_name)

        address = item.get("address") or {}

        if isinstance(address, dict):
            country = safe_text(
                address.get("addressCountry")
            )

            if country and country not in countries:
                countries.append(country)

    return locations, countries


def ashby_compensation_summary(job: dict) -> str:
    compensation = job.get("compensation") or {}

    if not isinstance(compensation, dict):
        return ""

    return safe_text(
        compensation.get("compensationTierSummary")
        or compensation.get(
            "scrapeableCompensationSalarySummary"
        )
    )


def normalize_ashby_jobs(
    company: str,
    raw_jobs: object,
) -> list[dict]:
    normalized = []

    if not isinstance(raw_jobs, list):
        return normalized

    for job in raw_jobs:
        if not isinstance(job, dict):
            continue

        # Ashby can expose direct-link postings that should not appear
        # in the normal public listing.
        if job.get("isListed") is False:
            continue

        primary_location = safe_text(
            job.get("location"),
            "Location not listed",
        )

        secondary_locations, secondary_countries = (
            extract_secondary_ashby_locations(
                job.get("secondaryLocations")
            )
        )

        all_locations = [primary_location]

        for location_name in secondary_locations:
            if location_name not in all_locations:
                all_locations.append(location_name)

        location_text = " | ".join(
            location
            for location in all_locations
            if location
        )

        job_url = safe_text(job.get("jobUrl"))
        apply_url = safe_text(
            job.get("applyUrl")
            or job_url
        )

        job_identifier = ""

        if job_url:
            job_identifier = job_url.rstrip("/").split("/")[-1]
        elif apply_url:
            job_identifier = (
                apply_url.rstrip("/").split("/")[-1]
            )

        description = safe_text(
            job.get("descriptionPlain")
        )

        if not description:
            description = clean_html(
                job.get("descriptionHtml")
            )

        workplace_type = safe_text(
            job.get("workplaceType")
        )

        is_remote = bool(job.get("isRemote"))

        normalized.append(
            {
                "id": job_identifier or apply_url,
                "company": safe_text(
                    company,
                    "Unknown company",
                ),
                "title": safe_text(
                    job.get("title"),
                    "Unknown title",
                ),
                "location": location_text
                or "Location not listed",
                "description": description,
                "updated_at": safe_text(
                    job.get("publishedAt")
                ),
                "apply_url": apply_url,
                "source": "Ashby",
                "workplace_type": workplace_type,
                "is_remote": is_remote,
                "country": extract_country_from_ashby_address(
                    job.get("address")
                ),
                "secondary_countries": secondary_countries,
                "employment_type": safe_text(
                    job.get("employmentType")
                ),
                "department": safe_text(
                    job.get("department")
                ),
                "team": safe_text(job.get("team")),
                "compensation": ashby_compensation_summary(
                    job
                ),
            }
        )

    return normalized


def fetch_ashby_board(
    company: str,
    board_token: str,
) -> dict:
    url = (
        "https://api.ashbyhq.com/posting-api/"
        f"job-board/{board_token}"
    )

    try:
        response = get_http_session().get(
            url,
            params={
                "includeCompensation": (
                    "true"
                    if INCLUDE_ASHBY_COMPENSATION
                    else "false"
                )
            },
            timeout=request_timeout(),
        )

        if response.status_code in {400, 404}:
            return {
                "company": company,
                "board_token": board_token,
                "status": "missing",
                "jobs": [],
                "error": "",
            }

        response.raise_for_status()

        payload = response.json()
        raw_jobs = payload.get("jobs") or []

        return {
            "company": company,
            "board_token": board_token,
            "status": "active",
            "jobs": normalize_ashby_jobs(
                company,
                raw_jobs,
            ),
            "error": "",
        }

    except requests.RequestException as error:
        return {
            "company": company,
            "board_token": board_token,
            "status": "error",
            "jobs": [],
            "error": str(error),
        }

    except (TypeError, ValueError) as error:
        return {
            "company": company,
            "board_token": board_token,
            "status": "error",
            "jobs": [],
            "error": str(error),
        }


def collect_ashby_jobs(
    companies: dict[str, str],
) -> tuple[list[dict], dict]:
    return collect_source_jobs_fast(
        source_key="ashby",
        display_name="Ashby",
        companies=companies,
        load_cache_function=load_ashby_board_cache,
        save_cache_function=save_ashby_board_cache,
        fetch_function=(
            lambda company, board_token, _entry, _discovery:
            fetch_ashby_board(
                company,
                board_token,
            )
        ),
    )


# ============================================================
# LEVER
# ============================================================

LEVER_API_BASE_URLS = {
    "global": "https://api.lever.co/v0/postings",
    "eu": "https://api.eu.lever.co/v0/postings",
}


def load_lever_board_cache() -> dict:
    if not LEVER_BOARD_CACHE_FILE.exists():
        return {}

    try:
        payload = json.loads(
            LEVER_BOARD_CACHE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return {}

    return payload if isinstance(payload, dict) else {}


def save_lever_board_cache(cache: dict) -> None:
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    temporary_file = LEVER_BOARD_CACHE_FILE.with_suffix(
        ".tmp"
    )

    temporary_file.write_text(
        json.dumps(
            cache,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    temporary_file.replace(LEVER_BOARD_CACHE_FILE)


def lever_location_text(categories: object) -> str:
    if not isinstance(categories, dict):
        return "Location not listed"

    all_locations = categories.get("allLocations")

    if isinstance(all_locations, list):
        cleaned_locations = []

        for value in all_locations:
            location = safe_text(value)

            if (
                location
                and location not in cleaned_locations
            ):
                cleaned_locations.append(location)

        if cleaned_locations:
            return " | ".join(cleaned_locations)

    return safe_text(
        categories.get("location"),
        "Location not listed",
    )


def lever_description_text(job: dict) -> str:
    """
    Keep Lever's main description plus structured requirement lists.

    Lever often stores qualifications, responsibilities, and benefits
    in the separate `lists` field rather than descriptionPlain.
    """

    parts = []

    description = safe_text(
        job.get("descriptionPlain")
    )

    if not description:
        description = clean_html(
            job.get("description")
        )

    if description:
        parts.append(description)

    lists = job.get("lists") or []

    if isinstance(lists, list):
        for item in lists:
            if not isinstance(item, dict):
                continue

            heading = safe_text(item.get("text"))
            content = clean_html(item.get("content"))

            combined = " ".join(
                value
                for value in [heading, content]
                if value
            )

            if combined:
                parts.append(combined)

    additional = safe_text(
        job.get("additionalPlain")
    )

    if not additional:
        additional = clean_html(
            job.get("additional")
        )

    if additional:
        parts.append(additional)

    return re.sub(
        r"\s+",
        " ",
        " ".join(parts),
    ).strip()


def lever_compensation_summary(job: dict) -> str:
    plain_description = safe_text(
        job.get("salaryDescriptionPlain")
    )

    if plain_description:
        return plain_description

    salary_range = job.get("salaryRange") or {}

    if not isinstance(salary_range, dict):
        return ""

    minimum = salary_range.get("min")
    maximum = salary_range.get("max")
    currency = safe_text(
        salary_range.get("currency")
    )
    interval = safe_text(
        salary_range.get("interval")
    )

    if minimum is None and maximum is None:
        return ""

    def readable_amount(value: object) -> str:
        if isinstance(value, bool):
            return safe_text(value)

        if isinstance(value, (int, float)):
            if float(value).is_integer():
                return f"{int(value):,}"

            return f"{float(value):,.2f}"

        return safe_text(value)

    if minimum is not None and maximum is not None:
        amount_text = (
            f"{readable_amount(minimum)}–"
            f"{readable_amount(maximum)}"
        )
    else:
        amount_text = readable_amount(
            minimum if minimum is not None else maximum
        )

    pieces = [
        currency,
        amount_text,
    ]

    if interval:
        pieces.append(f"per {interval}")

    return " ".join(
        piece
        for piece in pieces
        if piece
    )


def normalize_lever_jobs(
    company: str,
    raw_jobs: object,
    instance_name: str,
) -> list[dict]:
    normalized = []

    if not isinstance(raw_jobs, list):
        return normalized

    for job in raw_jobs:
        if not isinstance(job, dict):
            continue

        categories = job.get("categories") or {}

        if not isinstance(categories, dict):
            categories = {}

        workplace_type = safe_text(
            job.get("workplaceType")
        )

        normalized.append(
            {
                "id": safe_text(job.get("id")),
                "company": safe_text(
                    company,
                    "Unknown company",
                ),
                "title": safe_text(
                    job.get("text"),
                    "Unknown title",
                ),
                "location": lever_location_text(
                    categories
                ),
                "description": lever_description_text(
                    job
                ),
                # Lever's public postings list does not provide a
                # published or updated timestamp.
                "updated_at": "",
                "apply_url": safe_text(
                    job.get("applyUrl")
                    or job.get("hostedUrl")
                ),
                "source": "Lever",
                "workplace_type": workplace_type,
                "is_remote": (
                    workplace_type.lower() == "remote"
                ),
                "country": safe_text(
                    job.get("country")
                ),
                "secondary_countries": [],
                "employment_type": safe_text(
                    categories.get("commitment")
                ),
                "department": safe_text(
                    categories.get("department")
                ),
                "team": safe_text(
                    categories.get("team")
                ),
                "compensation": (
                    lever_compensation_summary(job)
                ),
                "lever_instance": instance_name,
            }
        )

    return normalized


def lever_instances_to_try(
    company: str,
    known_instance: str = "",
    full_discovery: bool = False,
) -> list[str]:
    known = safe_text(known_instance).lower()

    if known in LEVER_API_BASE_URLS:
        return [known]

    override = safe_text(
        LEVER_INSTANCE_OVERRIDES.get(company)
    ).lower()

    if override in LEVER_API_BASE_URLS:
        return [override]

    instances = ["global"]

    if (
        full_discovery
        and TRY_LEVER_EU_DURING_DISCOVERY
    ):
        instances.append("eu")

    return instances


def fetch_lever_board(
    company: str,
    board_token: str,
    known_instance: str = "",
    full_discovery: bool = False,
) -> dict:
    request_count = 0
    last_error = ""

    for instance_name in lever_instances_to_try(
        company,
        known_instance=known_instance,
        full_discovery=full_discovery,
    ):
        base_url = LEVER_API_BASE_URLS[instance_name]
        url = f"{base_url}/{board_token}"

        try:
            request_count += 1

            response = get_http_session().get(
                url,
                params={"mode": "json"},
                timeout=request_timeout(),
            )

            if response.status_code in {400, 404}:
                continue

            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, list):
                raise ValueError(
                    "Lever returned a non-list payload."
                )

            return {
                "company": company,
                "board_token": board_token,
                "status": "active",
                "jobs": normalize_lever_jobs(
                    company,
                    payload,
                    instance_name,
                ),
                "instance": instance_name,
                "request_count": request_count,
                "error": "",
            }

        except requests.RequestException as error:
            last_error = str(error)

            return {
                "company": company,
                "board_token": board_token,
                "status": "error",
                "jobs": [],
                "instance": instance_name,
                "request_count": request_count,
                "error": last_error,
            }

        except (TypeError, ValueError) as error:
            last_error = str(error)

            return {
                "company": company,
                "board_token": board_token,
                "status": "error",
                "jobs": [],
                "instance": instance_name,
                "request_count": request_count,
                "error": last_error,
            }

    return {
        "company": company,
        "board_token": board_token,
        "status": "missing",
        "jobs": [],
        "instance": "",
        "request_count": request_count,
        "error": last_error,
    }


def collect_lever_jobs(
    companies: dict[str, str],
) -> tuple[list[dict], dict]:
    return collect_source_jobs_fast(
        source_key="lever",
        display_name="Lever",
        companies=companies,
        load_cache_function=load_lever_board_cache,
        save_cache_function=save_lever_board_cache,
        fetch_function=(
            lambda company, board_token, entry, discovery:
            fetch_lever_board(
                company,
                board_token,
                known_instance=safe_text(
                    entry.get("instance")
                    if isinstance(entry, dict)
                    else ""
                ),
                full_discovery=discovery,
            )
        ),
    )


def deduplicate_jobs(jobs: list[dict]) -> list[dict]:
    unique = []
    seen = set()

    for job in jobs:
        key = (
            job["source"],
            job["company"].lower(),
            job["id"] or job["apply_url"],
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(job)

    return unique


# ============================================================
# U.S. FILTER AND LOCATION PRIORITY
# ============================================================

def is_us_job(job: dict) -> bool:
    country = safe_text(job.get("country")).lower()

    secondary_countries = [
        safe_text(value).lower()
        for value in (job.get("secondary_countries") or [])
        if safe_text(value)
    ]

    us_country_values = {
        "us",
        "usa",
        "u.s.",
        "u.s.a.",
        "united states",
        "united states of america",
    }

    if country in us_country_values:
        return True

    if any(
        value in us_country_values
        for value in secondary_countries
    ):
        return True

    if (
        country
        and country not in us_country_values
        and not secondary_countries
    ):
        return False

    location = safe_text(
        job.get("location"),
        "Location not listed",
    )
    location_lower = cached_lower_job_text(
        job,
        "location",
        "Location not listed",
    )
    description_lower = cached_lower_job_text(
        job,
        "description",
    )
    combined = f"{location_lower} {description_lower}"

    foreign_location = contains_any(
        location_lower,
        NON_US_LOCATION_TERMS,
    )

    explicit_us_location = (
        contains_any(location_lower, US_GENERAL_TERMS)
        or contains_us_state_name(location_lower)
        or contains_us_state_abbreviation(location)
        or contains_any(location_lower, LOCAL_AREA_TERMS)
    )

    if foreign_location and not explicit_us_location:
        return False

    if explicit_us_location:
        return True

    explicit_us_description = (
        contains_any(description_lower, US_GENERAL_TERMS)
        or contains_us_state_name(description_lower)
        or bool(
            re.search(
                r"\b(?:remote|work)\s+"
                r"(?:within|in|across|from)\s+"
                r"(?:the\s+)?(?:u\.?s\.?|united states)\b",
                combined,
            )
        )
        or bool(
            re.search(
                r"\b(?:must|should)\s+"
                r"(?:be|reside|live|work)\s+"
                r"(?:in|within)\s+(?:the\s+)?"
                r"(?:u\.?s\.?|united states)\b",
                combined,
            )
        )
    )

    return explicit_us_description


def detect_work_arrangement(job: dict) -> str:
    structured_workplace_type = safe_text(
        job.get("workplace_type")
    ).lower()

    if structured_workplace_type == "remote":
        return "remote"

    if structured_workplace_type == "hybrid":
        return "hybrid"

    if structured_workplace_type in {
        "onsite",
        "on-site",
    }:
        return "onsite"

    if job.get("is_remote") is True:
        return "remote"

    location = cached_lower_job_text(
        job,
        "location",
    )
    description = cached_lower_job_text(
        job,
        "description",
    )

    if contains_any(location, HYBRID_TERMS):
        return "hybrid"

    if contains_any(location, REMOTE_TERMS):
        return "remote"

    if contains_any(location, ONSITE_TERMS):
        return "onsite"

    hybrid_patterns = [
        "hybrid role",
        "hybrid position",
        "hybrid schedule",
        "hybrid work",
        "days per week in office",
        "days in the office",
    ]

    if any(pattern in description for pattern in hybrid_patterns):
        return "hybrid"

    remote_patterns = [
        "this is a remote role",
        "this is a remote position",
        "remote within the united states",
        "remote in the united states",
        "work remotely within the united states",
        "us-remote",
        "u.s.-remote",
        "remote-first",
        "fully remote",
    ]

    if any(pattern in description for pattern in remote_patterns):
        return "remote"

    if contains_any(description, ONSITE_TERMS):
        return "onsite"

    return "onsite"


def is_triangle_job(job: dict) -> bool:
    location = cached_lower_job_text(
        job,
        "location",
    )

    if contains_any(location, LOCAL_AREA_TERMS):
        return True

    description = cached_lower_job_text(
        job,
        "description",
    )

    local_patterns = [
        "based in raleigh",
        "based in durham",
        "based in chapel hill",
        "raleigh office",
        "durham office",
        "chapel hill office",
        "research triangle park office",
        "based in research triangle park",
        "hybrid in raleigh",
        "hybrid in durham",
        "hybrid in chapel hill",
        "hybrid in cary",
        "hybrid in morrisville",
    ]

    return any(pattern in description for pattern in local_patterns)


def get_location_priority(job: dict) -> tuple[int, str]:
    arrangement = detect_work_arrangement(job)
    triangle = is_triangle_job(job)

    if arrangement == "remote":
        return 4, "U.S. remote"

    if arrangement == "hybrid" and triangle:
        return 3, "Triangle hybrid"

    if arrangement == "hybrid":
        return 2, "Other U.S. hybrid"

    if triangle:
        return 2, "Triangle onsite"

    return 1, "Other U.S. onsite"


# ============================================================
# TRANSPARENT RULE SCORING
# ============================================================

def find_term_spans(
    lowered_text: str,
    term: str,
) -> list[tuple[int, int]]:
    """Return every whole-term position for a phrase."""

    return find_whole_term_spans(
        lowered_text,
        term.lower(),
    )


def spans_overlap(
    first: tuple[int, int],
    second: tuple[int, int],
) -> bool:
    return (
        first[0] < second[1]
        and second[0] < first[1]
    )


def best_role_match(title: str) -> dict | None:
    """
    Return only the strongest matching role phrase.

    This prevents titles such as "Risk Data Scientist" from also
    receiving the lower "Data Scientist" role score.
    """

    candidates = []

    for term, points in TARGET_ROLE_PHRASES.items():
        for span in find_term_spans(title, term):
            candidates.append(
                {
                    "term": term,
                    "points": points,
                    "span": span,
                }
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda candidate: (
            candidate["points"],
            len(candidate["term"]),
            -candidate["span"][0],
        ),
        reverse=True,
    )

    return candidates[0]


def non_overlapping_specialty_matches(
    title: str,
    blocked_spans: list[tuple[int, int]],
) -> list[dict]:
    """
    Add distinct title specialties without reusing words already
    credited as the role or as another specialty.
    """

    candidates = []

    for term, points in TITLE_SPECIALTY_TERMS.items():
        for span in find_term_spans(title, term):
            candidates.append(
                {
                    "term": term,
                    "points": points,
                    "span": span,
                }
            )

    candidates.sort(
        key=lambda candidate: (
            candidate["points"],
            len(candidate["term"]),
            -candidate["span"][0],
        ),
        reverse=True,
    )

    selected = []
    occupied_spans = list(blocked_spans)
    selected_terms = set()

    for candidate in candidates:
        if candidate["term"] in selected_terms:
            continue

        if any(
            spans_overlap(
                candidate["span"],
                occupied,
            )
            for occupied in occupied_spans
        ):
            continue

        selected.append(candidate)
        selected_terms.add(candidate["term"])
        occupied_spans.append(candidate["span"])

    selected.sort(key=lambda item: item["span"][0])
    return selected


def title_term_is_covered(
    title: str,
    term: str,
    occupied_spans: list[tuple[int, int]],
) -> bool:
    return any(
        any(
            spans_overlap(span, occupied)
            for occupied in occupied_spans
        )
        for span in find_term_spans(title, term)
    )


def calculate_rule_score(job: dict) -> tuple[int, list[dict]]:
    """
    Score the job using separate role and specialty concepts.

    A complete role such as "Sales Engineer" receives a larger score
    than a generic role plus specialty such as
    "Data Scientist, Model Risk".
    """

    title = cached_lower_job_text(
        job,
        "title",
    )
    description = cached_lower_job_text(
        job,
        "description",
    )

    score = 0
    reasons = []

    role_match = best_role_match(title)
    role_spans = [role_match["span"]] if role_match else []

    excluded_matches = [
        term
        for term in EXCLUDED_TITLE_TERMS
        if contains_term(title, term)
    ]

    if excluded_matches:
        return -100, [
            {
                "category": "Exclusion",
                "term": term,
                "points": -100,
            }
            for term in excluded_matches
        ]

    if role_match:
        score += role_match["points"]
        reasons.append(
            {
                "category": "Target role",
                "term": role_match["term"],
                "points": role_match["points"],
            }
        )

    specialty_matches = non_overlapping_specialty_matches(
        title,
        blocked_spans=role_spans,
    )

    specialty_spans = [
        match["span"]
        for match in specialty_matches
    ]

    specialty_terms = {
        match["term"]
        for match in specialty_matches
    }

    for match in specialty_matches:
        score += match["points"]
        reasons.append(
            {
                "category": "Title specialty",
                "term": match["term"],
                "points": match["points"],
            }
        )

    occupied_title_spans = role_spans + specialty_spans

    # Description-domain points are skipped when that concept has already
    # been credited from the title.
    for term, weight in DOMAIN_TERMS.items():
        if term in specialty_terms:
            continue

        if title_term_is_covered(
            title,
            term,
            occupied_title_spans,
        ):
            continue

        if contains_term(description, term):
            score += weight
            reasons.append(
                {
                    "category": "Domain in description",
                    "term": term,
                    "points": weight,
                }
            )

    customer_total = 0

    for term, weight in CUSTOMER_FACING_TERMS.items():
        if not contains_term(description, term):
            continue

        if customer_total >= 20:
            break

        awarded = min(
            weight,
            20 - customer_total,
        )

        score += awarded
        customer_total += awarded

        reasons.append(
            {
                "category": "Customer-facing",
                "term": term,
                "points": awarded,
            }
        )

    seniority_match = next(
        (
            term
            for term in SENIORITY_TERMS
            if contains_term(title, term)
        ),
        None,
    )

    if seniority_match:
        score += 3
        reasons.append(
            {
                "category": "Seniority",
                "term": seniority_match,
                "points": 3,
            }
        )

    if contains_term(title, "engineer") and role_match is None:
        relevant_engineering_title = any(
            contains_term(title, term)
            for term in [
                "ai",
                "machine learning",
                "risk",
                "fraud",
                "credit",
                "decision",
                "data science",
            ]
        )

        if not relevant_engineering_title:
            score -= 15
            reasons.append(
                {
                    "category": "Penalty",
                    "term": "generic engineering title",
                    "points": -15,
                }
            )

    return score, reasons


def normalize_score(raw_score: float) -> float:
    """
    Convert an unrestricted raw score into a stable 0-100 score.

    This is not min-max normalization, so a job's score does not change
    merely because different companies or jobs were included in a run.

    The curve preserves:
        raw 50 -> 50
        raw 65 -> 65

    It is strictly increasing, so job ranking is preserved.
    """

    if raw_score <= 0:
        return 0.0

    powered_score = raw_score ** NORMALIZATION_EXPONENT
    powered_scale = (
        NORMALIZATION_SCALE ** NORMALIZATION_EXPONENT
    )

    normalized = (
        100.0
        * powered_score
        / (powered_score + powered_scale)
    )

    return round(normalized, 1)


def assign_tier(final_score: float) -> str | None:
    if final_score >= MIN_STRONG_SCORE:
        return "Strong match"

    if final_score >= MIN_POSSIBLE_SCORE:
        return "Possible match"

    return None


# ============================================================
# JOB HISTORY
# ============================================================

def load_job_history() -> dict[str, str]:
    if not JOB_HISTORY_FILE.exists():
        return {}

    try:
        payload = json.loads(
            JOB_HISTORY_FILE.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}

    return {
        safe_text(key): safe_text(value)
        for key, value in payload.items()
        if safe_text(key) and safe_text(value)
    }


def save_job_history(history: dict[str, str]) -> None:
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    temporary_file = JOB_HISTORY_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(
            history,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary_file.replace(JOB_HISTORY_FILE)


# ============================================================
# DASHBOARD METADATA
# ============================================================

def infer_required_years(description: str) -> int | None:
    """
    Infer the most senior explicit experience requirement.

    Using the highest requirement avoids treating a senior role as
    entry level merely because it also asks for two years with one
    specific product or technology.
    """

    if not description:
        return None

    years = []

    if NO_EXPERIENCE_REQUIRED_REGEX.search(description):
        years.append(0)

    for match in EXPERIENCE_REQUIREMENT_REGEX.finditer(description):
        raw_years = (
            match.group("years")
            or match.group("years_after")
        )

        try:
            years.append(int(raw_years))
        except (TypeError, ValueError):
            continue

    return max(years) if years else None


def infer_experience_level(job: dict) -> tuple[str, int | None]:
    """Infer a useful, conservative experience-level label."""

    title = cached_lower_job_text(job, "title")
    description = cached_lower_job_text(
        job,
        "description",
    )

    title_rules = [
        (
            "Intern",
            ["intern", "internship"],
        ),
        (
            "Director+",
            [
                "director",
                "vice president",
                "vp",
                "head of",
                "chief",
            ],
        ),
        (
            "Manager",
            ["manager"],
        ),
        (
            "Lead / Principal",
            ["principal", "staff", "lead"],
        ),
        (
            "Senior",
            ["senior", "sr."],
        ),
        (
            "Entry",
            [
                "junior",
                "jr.",
                "entry level",
                "entry-level",
                "new grad",
                "graduate",
                "associate",
            ],
        ),
        (
            "Mid",
            ["mid level", "mid-level"],
        ),
    ]

    for label, terms in title_rules:
        if any(contains_term(title, term) for term in terms):
            return label, infer_required_years(description)

    required_years = infer_required_years(description)

    if required_years is None:
        return "Not specified", None

    if required_years <= 2:
        return "Entry", required_years

    if required_years <= 5:
        return "Mid", required_years

    if required_years <= 8:
        return "Senior", required_years

    return "Lead / Principal", required_years


def hard_entry_level_reason(job: dict) -> str | None:
    """
    Reject internships and entry-level jobs before they enter any
    scoring, cache, history, or dashboard workflow.
    """

    title = cached_lower_job_text(job, "title")
    description = cached_lower_job_text(
        job,
        "description",
    )
    employment_type = safe_text(
        job.get("employment_type")
    ).lower()

    for term in HARD_ENTRY_TITLE_TERMS:
        if contains_term(title, term):
            return f"title:{term}"

    if contains_term(title, "associate"):
        senior_associate_exceptions = [
            "associate director",
            "associate vice president",
            "associate vp",
            "senior associate",
            "principal associate",
            "associate manager",
        ]

        if not any(
            contains_term(title, term)
            for term in senior_associate_exceptions
        ):
            return "title:associate"

    if re.search(
        r"(?:\blevel\s*(?:i|1)\b|\b(?:i|1)\s*$)",
        title,
    ):
        return "title:level_1"

    for term in HARD_ENTRY_EMPLOYMENT_TERMS:
        if contains_term(employment_type, term):
            return f"employment:{term}"

    if ENTRY_DESCRIPTION_REGEX.search(description):
        return "description:entry_level"

    required_years = infer_required_years(description)

    if (
        required_years is not None
        and required_years <= 2
    ):
        return f"experience:{required_years}_years"

    return None



def parse_salary_amount(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.km]", "", value.lower())

    if not cleaned:
        return None

    multiplier = 1.0

    if cleaned.endswith("k"):
        multiplier = 1_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("m"):
        multiplier = 1_000_000.0
        cleaned = cleaned[:-1]

    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def annual_salary_range(job: dict) -> tuple[int | None, int | None]:
    """
    Convert a displayed U.S.-salary range to annual dollars when possible.

    The original compensation text remains visible on the card. Values in
    clearly non-USD currencies are not used by the salary slider.
    """

    compensation = safe_text(job.get("compensation"))

    if not compensation:
        return None, None

    lowered = compensation.lower()

    non_us_currency_markers = [
        "eur",
        "gbp",
        "cad",
        "aud",
        "chf",
        "€",
        "£",
    ]

    if (
        any(marker in lowered for marker in non_us_currency_markers)
        and "usd" not in lowered
        and "$" not in compensation
    ):
        return None, None

    amount_tokens = re.findall(
        r"(?:usd\s*|\$\s*)?"
        r"(\d[\d,]*(?:\.\d+)?\s*[kKmM]?)",
        compensation,
    )

    values = []

    for token in amount_tokens:
        amount = parse_salary_amount(token)

        if amount is not None and amount > 0:
            values.append(amount)

    if not values:
        return None, None

    minimum = min(values[:2])
    maximum = max(values[:2])

    if any(
        marker in lowered
        for marker in [
            "per hour",
            "/hour",
            "/hr",
            " hourly",
        ]
    ):
        multiplier = 2_080
    elif any(
        marker in lowered
        for marker in ["per day", "/day", " daily"]
    ):
        multiplier = 260
    elif any(
        marker in lowered
        for marker in ["per week", "/week", " weekly"]
    ):
        multiplier = 52
    elif any(
        marker in lowered
        for marker in ["per month", "/month", " monthly"]
    ):
        multiplier = 12
    elif maximum <= 500:
        # Small unqualified values in U.S. postings are usually hourly.
        multiplier = 2_080
    else:
        multiplier = 1

    annual_minimum = int(round(minimum * multiplier))
    annual_maximum = int(round(maximum * multiplier))

    # Ignore obvious parsing artifacts such as benefit percentages.
    if annual_maximum < 10_000:
        return None, None

    return annual_minimum, annual_maximum


def dashboard_job_key(job: dict) -> str:
    stable_key = job_cache_key(job)

    return hashlib.sha1(
        stable_key.encode("utf-8")
    ).hexdigest()[:20]


def dashboard_search_text(job: dict, computed: dict) -> str:
    reason_terms = " ".join(
        safe_text(reason.get("term"))
        for reason in computed.get("score_reasons", [])
        if isinstance(reason, dict)
    )

    values = [
        safe_text(job.get("title")),
        safe_text(job.get("company")),
        safe_text(job.get("location")),
        safe_text(job.get("department")),
        safe_text(job.get("team")),
        safe_text(job.get("employment_type")),
        reason_terms,
        safe_text(job.get("description"))[:5_000],
    ]

    return re.sub(
        r"\s+",
        " ",
        " ".join(values),
    ).strip().lower()


def parse_sort_timestamp(value: object) -> float:
    text = safe_text(value)

    if not text:
        return 0.0

    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError:
        return 0.0

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.timestamp()


def enrich_dashboard_job(
    original_job: dict,
    ranked_job: dict,
    change_status: str,
) -> None:
    experience_level, required_years = (
        infer_experience_level(original_job)
    )

    salary_minimum, salary_maximum = (
        annual_salary_range(original_job)
    )

    ranked_job.update(
        {
            "experience_level": experience_level,
            "required_years": required_years,
            "salary_min": salary_minimum,
            "salary_max": salary_maximum,
            "has_salary": salary_minimum is not None,
            "change_status": change_status,
            "dashboard_job_key": dashboard_job_key(
                original_job
            ),
            "dashboard_search_text": (
                dashboard_search_text(
                    original_job,
                    ranked_job,
                )
            ),
            "sort_timestamp": parse_sort_timestamp(
                original_job.get("updated_at")
            ),
        }
    )


# ============================================================
# HTML DASHBOARD
# ============================================================

def breakdown_table(reasons: list[dict]) -> str:
    rows = []

    for reason in reasons:
        category = html.escape(safe_text(reason.get("category")))
        term = html.escape(safe_text(reason.get("term")))
        points = int(reason.get("points", 0))
        points_text = f"+{points}" if points > 0 else str(points)

        rows.append(
            f"""
            <tr>
                <td>{category}</td>
                <td>{term}</td>
                <td class="points">{points_text}</td>
            </tr>
            """
        )

    return f"""
    <table>
        <thead>
            <tr>
                <th>Category</th>
                <th>Matched term</th>
                <th>Points</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """


def source_slug(source_name: str) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "-",
        source_name.lower(),
    ).strip("-")


def value_slug(value: str) -> str:
    return source_slug(value) or "not-specified"


def readable_employment_type(value: str) -> str:
    replacements = {
        "FullTime": "Full time",
        "PartTime": "Part time",
        "Temporary": "Temporary",
        "Contract": "Contract",
        "Intern": "Internship",
        "fulltime": "Full time",
        "parttime": "Part time",
    }

    return replacements.get(value, value)


def html_data(value: object) -> str:
    return html.escape(
        safe_text(value),
        quote=True,
    )


def job_cards(jobs: list[dict]) -> str:
    cards = []

    for position, job in enumerate(jobs):
        title = html.escape(job["title"])
        company = html.escape(job["company"])
        location = html.escape(job["location"])
        apply_url = html.escape(
            job["apply_url"],
            quote=True,
        )
        tier = html.escape(job["match_tier"])
        tier_slug = (
            "strong"
            if job["match_tier"] == "Strong match"
            else "possible"
        )
        arrangement_text = job["work_arrangement"].title()
        arrangement = html.escape(arrangement_text)
        arrangement_slug = value_slug(
            job["work_arrangement"]
        )
        location_label = html.escape(
            job["location_priority_label"]
        )

        source_name = safe_text(
            job.get("source"),
            "Unknown",
        )
        source_text = html.escape(source_name)
        source_css = source_slug(source_name)

        experience_level = safe_text(
            job.get("experience_level"),
            "Not specified",
        )
        experience_slug = value_slug(experience_level)
        experience_text = html.escape(experience_level)
        required_years = job.get("required_years")

        normalized_score = float(job["final_score"])
        raw_total_score = int(job["raw_total_score"])
        rule_score = int(job["rule_score"])
        location_points = int(job["location_points"])

        score_text = (
            f"{normalized_score:.1f}"
            .rstrip("0")
            .rstrip(".")
        )

        employment_type = readable_employment_type(
            safe_text(job.get("employment_type"))
        )
        employment_slug = value_slug(
            employment_type or "Not specified"
        )

        department = safe_text(job.get("department"))
        compensation = safe_text(job.get("compensation"))
        salary_minimum = job.get("salary_min")
        salary_maximum = job.get("salary_max")
        has_salary = salary_minimum is not None

        change_status = safe_text(
            job.get("change_status"),
            "seen",
        )
        change_label = {
            "new": "New",
            "updated": "Updated",
            "seen": "Previously seen",
        }.get(change_status, "Previously seen")

        change_badge = ""

        if change_status in {"new", "updated"}:
            change_badge = (
                f'<span class="change-badge change-{change_status}">'
                f'{html.escape(change_label)}</span>'
            )

        optional_metadata = []

        if employment_type:
            optional_metadata.append(
                f"<span>{html.escape(employment_type)}</span>"
            )

        if department:
            optional_metadata.append(
                f"<span>{html.escape(department)}</span>"
            )

        if compensation:
            optional_metadata.append(
                f"<span>{html.escape(compensation)}</span>"
            )

        if required_years is not None:
            optional_metadata.append(
                f"<span>{required_years}+ years indicated</span>"
            )

        cards.append(
            f"""
            <article
                class="job-card"
                data-original-position="{position}"
                data-job-key="{html_data(job['dashboard_job_key'])}"
                data-source="{source_css}"
                data-tier="{tier_slug}"
                data-score="{normalized_score}"
                data-raw-score="{raw_total_score}"
                data-company="{html_data(job['company'].lower())}"
                data-location="{html_data(job['location'].lower())}"
                data-search="{html_data(job['dashboard_search_text'])}"
                data-experience="{experience_slug}"
                data-arrangement="{arrangement_slug}"
                data-employment="{employment_slug}"
                data-change="{html_data(change_status)}"
                data-salary-min="{salary_minimum if has_salary else ''}"
                data-salary-max="{salary_maximum if has_salary else ''}"
                data-has-salary="{'1' if has_salary else '0'}"
                data-updated="{float(job.get('sort_timestamp', 0.0))}"
            >
                <div class="top-row">
                    <div class="score">{score_text}</div>

                    <div class="badges">
                        <span>{tier}</span>
                        <span>{experience_text}</span>
                        {change_badge}
                        <span class="source-badge source-{source_css}">
                            {source_text}
                        </span>
                    </div>
                </div>

                <h2>{title}</h2>
                <h3>{company}</h3>

                <div class="metadata">
                    <span>{location}</span>
                    <span>{arrangement}</span>
                    <span>{location_label}</span>
                    {''.join(optional_metadata)}
                    <span>Raw job-fit points: {rule_score}</span>
                    <span>Raw location points: +{location_points}</span>
                    <span>Raw total: {raw_total_score}</span>
                    <span>Normalized: {score_text}/100</span>
                </div>

                <details>
                    <summary>Score contributors</summary>
                    {breakdown_table(job["score_reasons"])}
                </details>

                <div class="card-actions">
                    <a
                        class="apply-link"
                        href="{apply_url}"
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        View job and apply
                    </a>

                    <button
                        class="state-button save-button"
                        type="button"
                        data-set-state="saved"
                    >
                        ☆ Save
                    </button>

                    <button
                        class="state-button applied-button"
                        type="button"
                        data-set-state="applied"
                    >
                        Mark applied
                    </button>

                    <button
                        class="state-button dismiss-button"
                        type="button"
                        data-set-state="dismissed"
                    >
                        Dismiss
                    </button>
                </div>
            </article>
            """
        )

    return "".join(cards)


def checkbox_group(
    name: str,
    values: list[str],
    selected: bool = False,
) -> str:
    items = []

    for value in values:
        slug = value_slug(value)
        checked = " checked" if selected else ""

        items.append(
            f"""
            <label class="check-pill">
                <input
                    type="checkbox"
                    name="{html.escape(name)}"
                    value="{html.escape(slug)}"
                    {checked}
                >
                <span>{html.escape(value)}</span>
            </label>
            """
        )

    return "".join(items)


def create_report(jobs: list[dict]) -> Path:
    strong_count = sum(
        1
        for job in jobs
        if job["match_tier"] == "Strong match"
    )
    possible_count = len(jobs) - strong_count
    new_count = sum(
        1
        for job in jobs
        if job.get("change_status") in {"new", "updated"}
    )

    companies = sorted(
        {
            safe_text(job.get("company"))
            for job in jobs
            if safe_text(job.get("company"))
        },
        key=str.lower,
    )
    locations = sorted(
        {
            safe_text(job.get("location"))
            for job in jobs
            if safe_text(job.get("location"))
        },
        key=str.lower,
    )

    source_values = sorted(
        {
            safe_text(job.get("source"))
            for job in jobs
            if safe_text(job.get("source"))
        }
    )
    arrangement_values = sorted(
        {
            safe_text(job.get("work_arrangement")).title()
            for job in jobs
            if safe_text(job.get("work_arrangement"))
        }
    )
    employment_values = sorted(
        {
            readable_employment_type(
                safe_text(job.get("employment_type"))
            )
            for job in jobs
            if safe_text(job.get("employment_type"))
        }
    )

    if any(
        not safe_text(job.get("employment_type"))
        for job in jobs
    ):
        employment_values.append("Not specified")

    experience_values = [
        "Mid",
        "Senior",
        "Lead / Principal",
        "Manager",
        "Director+",
        "Not specified",
    ]

    salaries = [
        int(job["salary_max"])
        for job in jobs
        if job.get("salary_max") is not None
    ]

    if salaries:
        salary_floor = max(
            0,
            (min(salaries) // 10_000) * 10_000,
        )
        salary_ceiling = (
            ((max(salaries) + 9_999) // 10_000)
            * 10_000
        )
        salary_ceiling = max(
            salary_ceiling,
            salary_floor + 10_000,
        )
    else:
        salary_floor = 0
        salary_ceiling = 500_000

    company_options = "".join(
        f'<option value="{html.escape(company, quote=True)}"></option>'
        for company in companies
    )
    location_options = "".join(
        f'<option value="{html.escape(location, quote=True)}"></option>'
        for location in locations
    )

    report = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >
        <title>Unified ATS Job Dashboard</title>

        <style>
            :root {{
                color-scheme: light;
                --background: #f3f5f8;
                --card: #ffffff;
                --text: #18212f;
                --muted: #5d6978;
                --line: #dfe4ea;
                --soft: #eef2f6;
                --accent: #263244;
                --blue-soft: #e4edff;
                --green-soft: #e8f6ec;
                --danger-soft: #fff0f0;
            }}

            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                background: var(--background);
                color: var(--text);
                font-family:
                    -apple-system,
                    BlinkMacSystemFont,
                    "Segoe UI",
                    sans-serif;
            }}

            button,
            input,
            select {{
                font: inherit;
            }}

            main {{
                width: min(1440px, 95%);
                margin: 32px auto 70px;
            }}

            .notice {{
                background: var(--green-soft);
                border: 1px solid #b9dfc4;
                border-radius: 14px;
                padding: 15px 18px;
                margin: 16px 0 20px;
                font-weight: 700;
            }}

            .summary-tabs {{
                display: grid;
                grid-template-columns: repeat(5, minmax(130px, 1fr));
                gap: 10px;
                margin-bottom: 18px;
            }}

            .summary-tab {{
                border: 1px solid var(--line);
                border-radius: 14px;
                background: var(--card);
                color: var(--text);
                cursor: pointer;
                padding: 14px;
                text-align: left;
            }}

            .summary-tab strong {{
                display: block;
                font-size: 23px;
                margin-bottom: 3px;
            }}

            .summary-tab.active {{
                background: var(--accent);
                border-color: var(--accent);
                color: white;
            }}

            .dashboard-layout {{
                display: grid;
                grid-template-columns: minmax(270px, 330px) minmax(0, 1fr);
                gap: 22px;
                align-items: start;
            }}

            .filter-panel {{
                position: sticky;
                top: 14px;
                max-height: calc(100vh - 28px);
                overflow-y: auto;
                background: var(--card);
                border: 1px solid var(--line);
                border-radius: 16px;
                padding: 18px;
                box-shadow: 0 5px 16px rgba(20, 30, 50, 0.06);
            }}

            .panel-heading {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 10px;
                margin-bottom: 12px;
            }}

            .panel-heading h2 {{
                margin: 0;
                font-size: 20px;
            }}

            .clear-button {{
                border: 0;
                background: transparent;
                color: #3158a6;
                cursor: pointer;
                font-weight: 750;
                padding: 5px;
            }}

            .filter-section {{
                border-top: 1px solid #e7ebf0;
                padding-top: 15px;
                margin-top: 15px;
            }}

            .filter-section:first-of-type {{
                border-top: 0;
                margin-top: 0;
                padding-top: 0;
            }}

            .filter-title {{
                display: block;
                font-weight: 800;
                margin-bottom: 9px;
            }}

            .field {{
                display: block;
                margin-bottom: 10px;
            }}

            .field span {{
                display: block;
                color: var(--muted);
                font-size: 13px;
                font-weight: 700;
                margin-bottom: 5px;
            }}

            .field input[type="text"],
            .field select,
            .sort-select {{
                width: 100%;
                border: 1px solid #cbd3dd;
                border-radius: 10px;
                background: white;
                padding: 10px 11px;
            }}

            .range-labels {{
                display: flex;
                justify-content: space-between;
                gap: 12px;
                font-size: 13px;
                font-weight: 750;
                margin-bottom: 4px;
            }}

            .drag-range {{
                position: relative;
                height: 42px;
                margin: 0 2px 8px;
                touch-action: none;
                user-select: none;
            }}

            .drag-range-track {{
                position: absolute;
                left: 13px;
                right: 13px;
                top: 50%;
                height: 7px;
                transform: translateY(-50%);
                border-radius: 999px;
                background: #d9dee6;
                box-shadow:
                    inset 0 1px 2px rgba(20, 30, 50, 0.13);
                cursor: pointer;
            }}

            .drag-range-fill {{
                position: absolute;
                top: 0;
                bottom: 0;
                left: 0;
                width: 100%;
                border-radius: inherit;
                background: #1877f2;
                pointer-events: none;
            }}

            .drag-range-handle {{
                position: absolute;
                top: 50%;
                width: 28px;
                height: 28px;
                transform: translate(-50%, -50%);
                border: 3px solid white;
                border-radius: 50%;
                background: #1877f2;
                box-shadow:
                    0 2px 5px rgba(20, 30, 50, 0.3),
                    0 0 0 1px rgba(24, 119, 242, 0.22);
                cursor: grab;
                padding: 0;
                z-index: 3;
                touch-action: none;
                transition:
                    box-shadow 100ms ease,
                    transform 100ms ease;
            }}

            .drag-range-handle:hover {{
                box-shadow:
                    0 3px 7px rgba(20, 30, 50, 0.34),
                    0 0 0 4px rgba(24, 119, 242, 0.12);
            }}

            .drag-range-handle.is-dragging {{
                cursor: grabbing;
                transform: translate(-50%, -50%) scale(1.08);
                box-shadow:
                    0 4px 10px rgba(20, 30, 50, 0.34),
                    0 0 0 5px rgba(24, 119, 242, 0.14);
            }}

            .drag-range-handle:focus-visible {{
                outline: 3px solid rgba(24, 119, 242, 0.3);
                outline-offset: 3px;
            }}

            .drag-range.is-disabled {{
                opacity: 0.5;
            }}

            .drag-range.is-disabled,
            .drag-range.is-disabled .drag-range-track,
            .drag-range.is-disabled .drag-range-handle {{
                cursor: not-allowed;
            }}

            body.is-slider-dragging {{
                cursor: grabbing;
                user-select: none;
            }}

            .filter-hint {{
                display: block;
                color: var(--muted);
                font-size: 12px;
                line-height: 1.35;
                margin: -4px 0 10px;
            }}

            .check-grid {{
                display: flex;
                flex-wrap: wrap;
                gap: 7px;
            }}

            .check-pill {{
                position: relative;
            }}

            .check-pill input {{
                position: absolute;
                opacity: 0;
                pointer-events: none;
            }}

            .check-pill span {{
                display: block;
                border: 1px solid #cbd3dd;
                border-radius: 999px;
                background: white;
                cursor: pointer;
                font-size: 13px;
                font-weight: 700;
                padding: 7px 10px;
                transition:
                    background 120ms ease,
                    border-color 120ms ease,
                    color 120ms ease,
                    transform 120ms ease;
                user-select: none;
            }}

            .check-pill span:hover {{
                background: #f4f7fb;
                border-color: #9eabbc;
                transform: translateY(-1px);
            }}

            .check-pill input:focus-visible + span {{
                outline: 3px solid rgba(24, 119, 242, 0.22);
                outline-offset: 2px;
            }}

            .check-pill input:checked + span {{
                background: var(--accent);
                border-color: var(--accent);
                color: white;
            }}

            .checkbox-row {{
                display: flex;
                align-items: flex-start;
                gap: 8px;
                font-size: 13px;
                font-weight: 650;
                margin-top: 8px;
            }}

            .salary-missing-row {{
                line-height: 1.35;
                margin-top: 12px;
            }}

            .salary-missing-row input {{
                flex: 0 0 auto;
                margin-top: 2px;
            }}

            .content-panel {{
                min-width: 0;
            }}

            .result-toolbar {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 14px;
                background: var(--card);
                border: 1px solid var(--line);
                border-radius: 14px;
                padding: 13px 15px;
                margin-bottom: 12px;
            }}

            #visible-count {{
                font-weight: 800;
                margin: 0;
            }}

            .sort-wrap {{
                display: flex;
                align-items: center;
                gap: 8px;
                min-width: 270px;
            }}

            .sort-wrap label {{
                color: var(--muted);
                font-size: 13px;
                font-weight: 750;
                white-space: nowrap;
            }}

            .active-filters {{
                display: flex;
                flex-wrap: wrap;
                gap: 7px;
                min-height: 30px;
                margin: 0 0 12px;
            }}

            .filter-token {{
                border: 0;
                border-radius: 999px;
                background: #e4edff;
                color: #203861;
                cursor: pointer;
                font-size: 12px;
                font-weight: 750;
                padding: 6px 9px;
            }}

            .job-card,
            .empty-state {{
                background: var(--card);
                border: 1px solid var(--line);
                border-radius: 16px;
                margin-bottom: 16px;
                padding: 22px;
                box-shadow: 0 5px 16px rgba(20, 30, 50, 0.06);
            }}

            .job-card.is-hidden {{
                display: none;
            }}

            .job-card.is-saved {{
                border-left: 5px solid #4267b2;
            }}

            .job-card.is-applied {{
                border-left: 5px solid #2f8a50;
            }}

            .job-card.is-dismissed {{
                opacity: 0.65;
            }}

            .top-row {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 14px;
            }}

            .score {{
                display: grid;
                place-items: center;
                width: 62px;
                height: 62px;
                flex: 0 0 auto;
                border-radius: 50%;
                background: var(--blue-soft);
                font-size: 21px;
                font-weight: 800;
            }}

            .badges,
            .metadata,
            .card-actions {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
            }}

            .badges {{
                justify-content: flex-end;
            }}

            .badges span,
            .metadata span {{
                background: var(--soft);
                border-radius: 999px;
                padding: 7px 11px;
                font-size: 13px;
                font-weight: 650;
            }}

            .source-greenhouse {{
                background: #e7f3e8 !important;
            }}

            .source-ashby {{
                background: #f1eafe !important;
            }}

            .source-lever {{
                background: #fff0df !important;
            }}

            .change-new {{
                background: #dff7e8 !important;
                color: #17643a;
            }}

            .change-updated {{
                background: #fff2cc !important;
                color: #735500;
            }}

            h2 {{
                margin: 15px 0 4px;
            }}

            h3 {{
                margin: 0 0 13px;
                color: var(--muted);
                font-weight: 500;
            }}

            details {{
                border-top: 1px solid #e7ebf0;
                padding-top: 13px;
                margin-top: 15px;
            }}

            summary {{
                cursor: pointer;
                font-weight: 750;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 13px;
            }}

            th,
            td {{
                border-bottom: 1px solid #e7ebf0;
                padding: 9px 8px;
                text-align: left;
            }}

            th {{
                color: var(--muted);
                font-size: 13px;
            }}

            .points {{
                width: 80px;
                font-weight: 750;
            }}

            .card-actions {{
                align-items: center;
                margin-top: 16px;
            }}

            .apply-link,
            .state-button {{
                border-radius: 9px;
                font-weight: 750;
                padding: 9px 12px;
                text-decoration: none;
            }}

            .apply-link {{
                background: var(--accent);
                color: white;
            }}

            .state-button {{
                border: 1px solid #cbd3dd;
                background: white;
                color: var(--text);
                cursor: pointer;
            }}

            .state-button.active {{
                background: #e4edff;
                border-color: #9eb7eb;
            }}

            .dismiss-button.active {{
                background: var(--danger-soft);
                border-color: #e6a9a9;
            }}

            .empty-state {{
                display: none;
                text-align: center;
            }}

            .empty-state.visible {{
                display: block;
            }}

            @media (max-width: 980px) {{
                .summary-tabs {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}

                .dashboard-layout {{
                    grid-template-columns: 1fr;
                }}

                .filter-panel {{
                    position: static;
                    max-height: none;
                }}
            }}

            @media (max-width: 620px) {{
                main {{
                    width: 94%;
                }}

                .summary-tabs {{
                    grid-template-columns: 1fr 1fr;
                }}

                .result-toolbar,
                .top-row {{
                    align-items: flex-start;
                    flex-direction: column;
                }}

                .sort-wrap {{
                    min-width: 0;
                    width: 100%;
                }}
            }}
        </style>
    </head>

    <body>
        <main>
            <h1>Unified ATS Job Dashboard</h1>

            <div class="notice">
                Greenhouse, Ashby, and Lever jobs are ranked together.
                Filters and sorting run instantly in your browser; saved,
                applied, and dismissed states remain stored locally.
            </div>

            <div class="summary-tabs" role="group" aria-label="Match views">
                <button class="summary-tab active" type="button" data-view="all">
                    <strong>{len(jobs)}</strong>
                    All matches
                </button>
                <button class="summary-tab" type="button" data-view="strong">
                    <strong>{strong_count}</strong>
                    Strong matches
                </button>
                <button class="summary-tab" type="button" data-view="possible">
                    <strong>{possible_count}</strong>
                    Possible matches
                </button>
                <button class="summary-tab" type="button" data-view="new">
                    <strong>{new_count}</strong>
                    New or updated
                </button>
                <button class="summary-tab" type="button" data-view="saved">
                    <strong id="saved-summary-count">0</strong>
                    Saved jobs
                </button>
            </div>

            <div class="dashboard-layout">
                <aside class="filter-panel">
                    <div class="panel-heading">
                        <h2>Filters</h2>
                        <button id="clear-filters" class="clear-button" type="button">
                            Clear all
                        </button>
                    </div>

                    <section class="filter-section">
                        <label class="field">
                            <span>Title or keyword</span>
                            <input
                                id="keyword-filter"
                                type="text"
                                placeholder="Risk, AI, solutions engineer..."
                            >
                        </label>

                        <label class="field">
                            <span>Company</span>
                            <input
                                id="company-filter"
                                type="text"
                                list="company-suggestions"
                                placeholder="Start typing a company"
                            >
                            <datalist id="company-suggestions">
                                {company_options}
                            </datalist>
                        </label>

                        <label class="field">
                            <span>Location</span>
                            <input
                                id="location-filter"
                                type="text"
                                list="location-suggestions"
                                placeholder="Remote, Raleigh, New York..."
                            >
                            <datalist id="location-suggestions">
                                {location_options}
                            </datalist>
                        </label>
                    </section>

                    <section class="filter-section">
                        <span class="filter-title">Match score</span>
                        <div class="range-labels">
                            <span id="score-min-label">{MIN_POSSIBLE_SCORE}</span>
                            <span id="score-max-label">100</span>
                        </div>

                        <div
                            id="score-range"
                            class="drag-range"
                            data-min="0"
                            data-max="100"
                            data-step="1"
                        >
                            <div class="drag-range-track">
                                <div class="drag-range-fill"></div>
                            </div>

                            <button
                                class="drag-range-handle drag-range-min"
                                type="button"
                                role="slider"
                                aria-label="Minimum match score"
                            ></button>

                            <button
                                class="drag-range-handle drag-range-max"
                                type="button"
                                role="slider"
                                aria-label="Maximum match score"
                            ></button>

                            <input
                                id="score-min"
                                type="hidden"
                                value="{MIN_POSSIBLE_SCORE}"
                            >
                            <input
                                id="score-max"
                                type="hidden"
                                value="100"
                            >
                        </div>
                    </section>

                    <section class="filter-section">
                        <span class="filter-title">Annual salary</span>
                        <div class="range-labels">
                            <span id="salary-min-label">${salary_floor:,}</span>
                            <span id="salary-max-label">${salary_ceiling:,}</span>
                        </div>

                        <div
                            id="salary-range"
                            class="drag-range{' is-disabled' if not salaries else ''}"
                            data-min="{salary_floor}"
                            data-max="{salary_ceiling}"
                            data-step="1000"
                            data-disabled="{'true' if not salaries else 'false'}"
                        >
                            <div class="drag-range-track">
                                <div class="drag-range-fill"></div>
                            </div>

                            <button
                                class="drag-range-handle drag-range-min"
                                type="button"
                                role="slider"
                                aria-label="Minimum annual salary"
                                {'disabled' if not salaries else ''}
                            ></button>

                            <button
                                class="drag-range-handle drag-range-max"
                                type="button"
                                role="slider"
                                aria-label="Maximum annual salary"
                                {'disabled' if not salaries else ''}
                            ></button>

                            <input
                                id="salary-min"
                                type="hidden"
                                value="{salary_floor}"
                            >
                            <input
                                id="salary-max"
                                type="hidden"
                                value="{salary_ceiling}"
                            >
                        </div>

                        <label class="checkbox-row salary-missing-row">
                            <input
                                id="include-missing-salary"
                                type="checkbox"
                                checked
                            >
                            <span>
                                Show results when salary information is not available
                            </span>
                        </label>
                    </section>

                    <section class="filter-section">
                        <span class="filter-title">Experience level</span>
                        <span class="filter-hint">
                            Select one or more. No selection shows all.
                        </span>
                        <div class="check-grid" id="experience-filters">
                            {checkbox_group('experience', experience_values)}
                        </div>
                    </section>

                    <section class="filter-section">
                        <span class="filter-title">Work arrangement</span>
                        <div class="check-grid" id="arrangement-filters">
                            {checkbox_group('arrangement', arrangement_values)}
                        </div>
                    </section>

                    <section class="filter-section">
                        <span class="filter-title">ATS source</span>
                        <div class="check-grid" id="source-filters">
                            {checkbox_group('source', source_values)}
                        </div>
                    </section>

                    <section class="filter-section">
                        <span class="filter-title">Employment type</span>
                        <div class="check-grid" id="employment-filters">
                            {checkbox_group('employment', employment_values)}
                        </div>
                    </section>

                    <section class="filter-section">
                        <span class="filter-title">Posting changes</span>
                        <div class="check-grid" id="change-filters">
                            {checkbox_group('change', ['New', 'Updated', 'Previously seen'])}
                        </div>
                    </section>

                    <section class="filter-section">
                        <span class="filter-title">Application status</span>
                        <div class="check-grid" id="state-filters">
                            {checkbox_group('state', ['Unmarked', 'Saved', 'Applied', 'Dismissed'])}
                        </div>
                        <label class="checkbox-row">
                            <input id="hide-dismissed" type="checkbox" checked>
                            <span>Hide dismissed jobs</span>
                        </label>
                    </section>
                </aside>

                <section class="content-panel">
                    <div class="result-toolbar">
                        <p id="visible-count">
                            Showing {len(jobs)} jobs
                        </p>

                        <div class="sort-wrap">
                            <label for="sort-select">Sort</label>
                            <select id="sort-select" class="sort-select">
                                <option value="score-desc" selected>
                                    Strongest match first
                                </option>
                                <option value="newest-desc">Newest first</option>
                                <option value="salary-desc">Salary: high to low</option>
                                <option value="salary-asc">Salary: low to high</option>
                                <option value="company-asc">Company: A–Z</option>
                                <option value="location-asc">Location: A–Z</option>
                                <option value="raw-desc">Raw score: high to low</option>
                            </select>
                        </div>
                    </div>

                    <div id="active-filters" class="active-filters"></div>

                    <div id="job-list" class="job-list">
                        {job_cards(jobs)}
                    </div>

                    <section id="empty-state" class="empty-state">
                        <h2>No jobs match these filters</h2>
                        <p>Clear one or more filters to widen the results.</p>
                    </section>
                </section>
            </div>
        </main>

        <script>
            const STORAGE_KEY = "ats-job-dashboard-states-v1";
            const cards = Array.from(document.querySelectorAll(".job-card"));
            const jobList = document.getElementById("job-list");
            const visibleCount = document.getElementById("visible-count");
            const emptyState = document.getElementById("empty-state");
            const activeFilters = document.getElementById("active-filters");
            const sortSelect = document.getElementById("sort-select");
            const summaryTabs = Array.from(document.querySelectorAll(".summary-tab"));

            const keywordFilter = document.getElementById("keyword-filter");
            const companyFilter = document.getElementById("company-filter");
            const locationFilter = document.getElementById("location-filter");
            const scoreMin = document.getElementById("score-min");
            const scoreMax = document.getElementById("score-max");
            const salaryMin = document.getElementById("salary-min");
            const salaryMax = document.getElementById("salary-max");
            const includeMissingSalary = document.getElementById("include-missing-salary");
            const hideDismissed = document.getElementById("hide-dismissed");

            const DEFAULTS = {{
                scoreMin: {MIN_POSSIBLE_SCORE},
                scoreMax: 100,
                salaryMin: {salary_floor},
                salaryMax: {salary_ceiling},
            }};

            let activeView = "all";
            let jobStates = loadJobStates();
            let filterFrame = null;
            let scoreRangeControl = null;
            let salaryRangeControl = null;

            function scheduleApplyFilters() {{
                if (filterFrame !== null) return;

                filterFrame = requestAnimationFrame(() => {{
                    filterFrame = null;
                    applyFilters();
                }});
            }}


            function loadJobStates() {{
                try {{
                    const stored = localStorage.getItem(STORAGE_KEY);
                    return stored ? JSON.parse(stored) : {{}};
                }} catch (error) {{
                    return {{}};
                }}
            }}

            function saveJobStates() {{
                try {{
                    localStorage.setItem(
                        STORAGE_KEY,
                        JSON.stringify(jobStates),
                    );
                }} catch (error) {{
                    console.warn("Could not persist job states", error);
                }}
            }}

            function cardState(card) {{
                return jobStates[card.dataset.jobKey] || "unmarked";
            }}

            function refreshCardState(card) {{
                const state = cardState(card);

                card.classList.toggle("is-saved", state === "saved");
                card.classList.toggle("is-applied", state === "applied");
                card.classList.toggle("is-dismissed", state === "dismissed");
                card.dataset.userState = state;

                card.querySelectorAll("[data-set-state]").forEach((button) => {{
                    const buttonState = button.dataset.setState;
                    button.classList.toggle("active", buttonState === state);

                    if (buttonState === "saved") {{
                        button.textContent = state === "saved" ? "★ Saved" : "☆ Save";
                    }} else if (buttonState === "applied") {{
                        button.textContent = state === "applied" ? "✓ Applied" : "Mark applied";
                    }} else if (buttonState === "dismissed") {{
                        button.textContent = state === "dismissed" ? "Undo dismiss" : "Dismiss";
                    }}
                }});
            }}

            function refreshAllCardStates() {{
                cards.forEach(refreshCardState);
                updateSavedSummary();
            }}

            function updateSavedSummary() {{
                const count = cards.filter((card) => cardState(card) === "saved").length;
                document.getElementById("saved-summary-count").textContent = count;
            }}

            function checkedValues(name) {{
                return new Set(
                    Array.from(document.querySelectorAll(`input[name="${{name}}"]:checked`))
                        .map((input) => input.value),
                );
            }}

            function groupAllows(name, value) {{
                const selected = checkedValues(name);
                return selected.size === 0 || selected.has(value);
            }}

            function normalizedChangeAllows(value) {{
                const selected = new Set(
                    Array.from(
                        checkedValues("change"),
                        normalizeChangeValue,
                    ),
                );

                return selected.size === 0 || selected.has(value);
            }}

            function normalizeChangeValue(value) {{
                if (value === "previously-seen") return "seen";
                return value;
            }}

            function intersectRanges(cardMin, cardMax, filterMin, filterMax) {{
                return cardMax >= filterMin && cardMin <= filterMax;
            }}

            function clamp(value, minimum, maximum) {{
                return Math.min(maximum, Math.max(minimum, value));
            }}

            function initDocumentDragRange(
                container,
                minInput,
                maxInput,
            ) {{
                const track = container.querySelector(
                    ".drag-range-track"
                );
                const fill = container.querySelector(
                    ".drag-range-fill"
                );
                const minHandle = container.querySelector(
                    ".drag-range-min"
                );
                const maxHandle = container.querySelector(
                    ".drag-range-max"
                );

                const minimum = Number(container.dataset.min);
                const maximum = Number(container.dataset.max);
                const step = Number(container.dataset.step) || 1;
                const disabled = (
                    container.dataset.disabled === "true"
                );

                let activeHandle = null;
                let latestPointerX = null;
                let renderFrame = null;

                function clampValue(value) {{
                    return Math.min(
                        maximum,
                        Math.max(minimum, value),
                    );
                }}

                function snap(value) {{
                    const stepIndex = Math.round(
                        (value - minimum) / step
                    );

                    return clampValue(
                        minimum + stepIndex * step
                    );
                }}

                function toPercent(value) {{
                    if (maximum === minimum) return 0;

                    return (
                        (Number(value) - minimum)
                        / (maximum - minimum)
                    ) * 100;
                }}

                function pointerValue(clientX) {{
                    const rectangle = track.getBoundingClientRect();
                    const ratio = Math.min(
                        1,
                        Math.max(
                            0,
                            (clientX - rectangle.left)
                            / rectangle.width,
                        ),
                    );

                    return snap(
                        minimum
                        + ratio * (maximum - minimum)
                    );
                }}

                function render() {{
                    renderFrame = null;

                    const minimumValue = Number(minInput.value);
                    const maximumValue = Number(maxInput.value);
                    const minimumPercent = toPercent(minimumValue);
                    const maximumPercent = toPercent(maximumValue);

                    minHandle.style.left = `${{minimumPercent}}%`;
                    maxHandle.style.left = `${{maximumPercent}}%`;
                    fill.style.left = `${{minimumPercent}}%`;
                    fill.style.width = `${{Math.max(
                        0,
                        maximumPercent - minimumPercent,
                    )}}%`;

                    minHandle.setAttribute(
                        "aria-valuemin",
                        String(minimum),
                    );
                    minHandle.setAttribute(
                        "aria-valuemax",
                        String(maximumValue),
                    );
                    minHandle.setAttribute(
                        "aria-valuenow",
                        String(minimumValue),
                    );

                    maxHandle.setAttribute(
                        "aria-valuemin",
                        String(minimumValue),
                    );
                    maxHandle.setAttribute(
                        "aria-valuemax",
                        String(maximum),
                    );
                    maxHandle.setAttribute(
                        "aria-valuenow",
                        String(maximumValue),
                    );

                    updateRangeLabels();
                }}

                function requestRender() {{
                    if (renderFrame !== null) return;
                    renderFrame = requestAnimationFrame(render);
                }}

                function setHandleValue(which, value) {{
                    const snapped = snap(value);

                    if (which === "min") {{
                        minInput.value = String(
                            Math.min(
                                snapped,
                                Number(maxInput.value),
                            ),
                        );
                    }} else {{
                        maxInput.value = String(
                            Math.max(
                                snapped,
                                Number(minInput.value),
                            ),
                        );
                    }}

                    requestRender();
                }}

                function processLatestPointer() {{
                    if (
                        activeHandle === null
                        || latestPointerX === null
                    ) {{
                        return;
                    }}

                    setHandleValue(
                        activeHandle,
                        pointerValue(latestPointerX),
                    );
                }}

                function onPointerMove(event) {{
                    if (activeHandle === null) return;

                    event.preventDefault();
                    latestPointerX = event.clientX;
                    processLatestPointer();
                }}

                function stopDragging(event) {{
                    if (activeHandle === null) return;

                    if (event) {{
                        latestPointerX = event.clientX;
                        processLatestPointer();
                    }}

                    minHandle.classList.remove("is-dragging");
                    maxHandle.classList.remove("is-dragging");
                    document.body.classList.remove(
                        "is-slider-dragging"
                    );

                    activeHandle = null;
                    latestPointerX = null;

                    window.removeEventListener(
                        "pointermove",
                        onPointerMove,
                    );
                    window.removeEventListener(
                        "pointerup",
                        stopDragging,
                    );
                    window.removeEventListener(
                        "pointercancel",
                        stopDragging,
                    );

                    render();
                    applyFilters();
                }}

                function startDragging(event, which) {{
                    if (disabled) return;

                    event.preventDefault();
                    event.stopPropagation();

                    activeHandle = which;
                    latestPointerX = event.clientX;

                    const handle = (
                        which === "min"
                        ? minHandle
                        : maxHandle
                    );

                    handle.classList.add("is-dragging");
                    document.body.classList.add(
                        "is-slider-dragging"
                    );

                    processLatestPointer();

                    window.addEventListener(
                        "pointermove",
                        onPointerMove,
                        {{ passive: false }},
                    );
                    window.addEventListener(
                        "pointerup",
                        stopDragging,
                    );
                    window.addEventListener(
                        "pointercancel",
                        stopDragging,
                    );
                }}

                function startFromTrack(event) {{
                    if (disabled) return;

                    const value = pointerValue(event.clientX);
                    const distanceFromMin = Math.abs(
                        value - Number(minInput.value)
                    );
                    const distanceFromMax = Math.abs(
                        value - Number(maxInput.value)
                    );

                    startDragging(
                        event,
                        (
                            distanceFromMin <= distanceFromMax
                            ? "min"
                            : "max"
                        ),
                    );
                }}

                function keyboardAdjust(event, which) {{
                    if (disabled) return;

                    const current = Number(
                        which === "min"
                        ? minInput.value
                        : maxInput.value
                    );

                    let next = current;

                    if (
                        event.key === "ArrowLeft"
                        || event.key === "ArrowDown"
                    ) {{
                        next = current - step;
                    }} else if (
                        event.key === "ArrowRight"
                        || event.key === "ArrowUp"
                    ) {{
                        next = current + step;
                    }} else if (event.key === "PageDown") {{
                        next = current - step * 10;
                    }} else if (event.key === "PageUp") {{
                        next = current + step * 10;
                    }} else if (event.key === "Home") {{
                        next = minimum;
                    }} else if (event.key === "End") {{
                        next = maximum;
                    }} else {{
                        return;
                    }}

                    event.preventDefault();
                    setHandleValue(which, next);
                    render();
                    applyFilters();
                }}

                minHandle.addEventListener(
                    "pointerdown",
                    (event) => startDragging(event, "min"),
                );

                maxHandle.addEventListener(
                    "pointerdown",
                    (event) => startDragging(event, "max"),
                );

                track.addEventListener(
                    "pointerdown",
                    startFromTrack,
                );

                minHandle.addEventListener(
                    "keydown",
                    (event) => keyboardAdjust(event, "min"),
                );

                maxHandle.addEventListener(
                    "keydown",
                    (event) => keyboardAdjust(event, "max"),
                );

                render();

                return {{
                    refresh: render,
                    reset(minimumValue, maximumValue) {{
                        minInput.value = String(minimumValue);
                        maxInput.value = String(maximumValue);
                        render();
                    }},
                }};
            }}

            function formatMoney(value) {{
                return new Intl.NumberFormat("en-US", {{
                    style: "currency",
                    currency: "USD",
                    maximumFractionDigits: 0,
                }}).format(Number(value));
            }}

            function updateRangeLabels() {{
                document.getElementById("score-min-label").textContent = scoreMin.value;
                document.getElementById("score-max-label").textContent = scoreMax.value;
                document.getElementById("salary-min-label").textContent = formatMoney(salaryMin.value);
                document.getElementById("salary-max-label").textContent = formatMoney(salaryMax.value);
            }}

            function activeViewMatches(card) {{
                const state = cardState(card);

                if (activeView === "strong") return card.dataset.tier === "strong";
                if (activeView === "possible") return card.dataset.tier === "possible";
                if (activeView === "new") return ["new", "updated"].includes(card.dataset.change);
                if (activeView === "saved") return state === "saved";
                return true;
            }}

            function cardMatches(card) {{
                if (!activeViewMatches(card)) return false;

                const keyword = keywordFilter.value.trim().toLowerCase();
                const company = companyFilter.value.trim().toLowerCase();
                const location = locationFilter.value.trim().toLowerCase();
                const score = Number(card.dataset.score);
                const filterScoreMin = Number(scoreMin.value);
                const filterScoreMax = Number(scoreMax.value);
                const state = cardState(card);

                if (keyword && !card.dataset.search.includes(keyword)) return false;
                if (company && !card.dataset.company.includes(company)) return false;
                if (location && !card.dataset.location.includes(location)) return false;
                if (score < filterScoreMin || score > filterScoreMax) return false;

                if (!groupAllows("experience", card.dataset.experience)) return false;
                if (!groupAllows("arrangement", card.dataset.arrangement)) return false;
                if (!groupAllows("source", card.dataset.source)) return false;
                if (!groupAllows("employment", card.dataset.employment)) return false;
                if (!groupAllows("state", state)) return false;
                if (!normalizedChangeAllows(card.dataset.change)) return false;
                if (hideDismissed.checked && state === "dismissed") return false;

                if (card.dataset.hasSalary === "1") {{
                    const cardMin = Number(card.dataset.salaryMin);
                    const cardMax = Number(card.dataset.salaryMax);

                    if (!intersectRanges(
                        cardMin,
                        cardMax,
                        Number(salaryMin.value),
                        Number(salaryMax.value),
                    )) {{
                        return false;
                    }}
                }} else if (!includeMissingSalary.checked) {{
                    return false;
                }}

                return true;
            }}

            function compareCards(a, b) {{
                const mode = sortSelect.value;

                if (mode === "newest-desc") {{
                    return Number(b.dataset.updated) - Number(a.dataset.updated)
                        || Number(b.dataset.score) - Number(a.dataset.score);
                }}

                if (mode === "salary-desc") {{
                    return Number(b.dataset.salaryMax || -1) - Number(a.dataset.salaryMax || -1)
                        || Number(b.dataset.score) - Number(a.dataset.score);
                }}

                if (mode === "salary-asc") {{
                    const aSalary = a.dataset.hasSalary === "1" ? Number(a.dataset.salaryMin) : Infinity;
                    const bSalary = b.dataset.hasSalary === "1" ? Number(b.dataset.salaryMin) : Infinity;
                    return aSalary - bSalary || Number(b.dataset.score) - Number(a.dataset.score);
                }}

                if (mode === "company-asc") {{
                    return a.dataset.company.localeCompare(b.dataset.company)
                        || Number(b.dataset.score) - Number(a.dataset.score);
                }}

                if (mode === "location-asc") {{
                    return a.dataset.location.localeCompare(b.dataset.location)
                        || Number(b.dataset.score) - Number(a.dataset.score);
                }}

                if (mode === "raw-desc") {{
                    return Number(b.dataset.rawScore) - Number(a.dataset.rawScore)
                        || Number(b.dataset.score) - Number(a.dataset.score);
                }}

                return Number(b.dataset.score) - Number(a.dataset.score)
                    || Number(b.dataset.rawScore) - Number(a.dataset.rawScore)
                    || Number(a.dataset.originalPosition) - Number(b.dataset.originalPosition);
            }}

            function sortCards() {{
                [...cards].sort(compareCards).forEach((card) => jobList.appendChild(card));
            }}

            function filterLabel(input) {{
                const text = input.closest("label")?.innerText?.trim();
                return text || input.value;
            }}

            function updateActiveFilterTokens() {{
                const tokens = [];

                if (activeView !== "all") {{
                    const label = {{
                        strong: "Strong matches",
                        possible: "Possible matches",
                        new: "New or updated",
                        saved: "Saved jobs",
                    }}[activeView];
                    tokens.push({{ label, action: () => setActiveView("all") }});
                }}

                if (keywordFilter.value.trim()) {{
                    tokens.push({{
                        label: `Keyword: ${{keywordFilter.value.trim()}}`,
                        action: () => {{ keywordFilter.value = ""; applyFilters(); }},
                    }});
                }}

                if (companyFilter.value.trim()) {{
                    tokens.push({{
                        label: `Company: ${{companyFilter.value.trim()}}`,
                        action: () => {{ companyFilter.value = ""; applyFilters(); }},
                    }});
                }}

                if (locationFilter.value.trim()) {{
                    tokens.push({{
                        label: `Location: ${{locationFilter.value.trim()}}`,
                        action: () => {{ locationFilter.value = ""; applyFilters(); }},
                    }});
                }}

                if (
                    Number(scoreMin.value) !== DEFAULTS.scoreMin
                    || Number(scoreMax.value) !== DEFAULTS.scoreMax
                ) {{
                    tokens.push({{
                        label: `Score: ${{scoreMin.value}}–${{scoreMax.value}}`,
                        action: () => {{
                            scoreMin.value = DEFAULTS.scoreMin;
                            scoreMax.value = DEFAULTS.scoreMax;
                            applyFilters();
                        }},
                    }});
                }}

                if (
                    Number(salaryMin.value) !== DEFAULTS.salaryMin
                    || Number(salaryMax.value) !== DEFAULTS.salaryMax
                    || !includeMissingSalary.checked
                ) {{
                    tokens.push({{
                        label: `Salary: ${{formatMoney(salaryMin.value)}}–${{formatMoney(salaryMax.value)}}`,
                        action: () => {{
                            salaryMin.value = DEFAULTS.salaryMin;
                            salaryMax.value = DEFAULTS.salaryMax;
                            includeMissingSalary.checked = true;
                            applyFilters();
                        }},
                    }});
                }}

                ["experience", "arrangement", "source", "employment", "change", "state"].forEach((name) => {{
                    const all = Array.from(
                        document.querySelectorAll(`input[name="${{name}}"]`),
                    );
                    const checked = all.filter((input) => input.checked);

                    if (checked.length > 0) {{
                        const labels = checked.map(filterLabel);
                        const selectedText = labels.length <= 2
                            ? labels.join(", ")
                            : `${{labels.length}} selected`;

                        tokens.push({{
                            label: `${{name[0].toUpperCase() + name.slice(1)}}: ${{selectedText}}`,
                            action: () => {{
                                all.forEach((input) => {{
                                    input.checked = false;
                                }});
                                applyFilters();
                            }},
                        }});
                    }}
                }});

                activeFilters.innerHTML = "";

                tokens.forEach((token) => {{
                    const button = document.createElement("button");
                    button.type = "button";
                    button.className = "filter-token";
                    button.textContent = `${{token.label}} ×`;
                    button.addEventListener("click", token.action);
                    activeFilters.appendChild(button);
                }});
            }}

            function applyFilters() {{
                updateRangeLabels();

                let shown = 0;

                cards.forEach((card) => {{
                    const show = cardMatches(card);
                    card.classList.toggle("is-hidden", !show);
                    if (show) shown += 1;
                }});

                visibleCount.textContent = `Showing ${{shown}} of ${{cards.length}} jobs`;
                emptyState.classList.toggle("visible", shown === 0);
                updateActiveFilterTokens();
                updateSavedSummary();
            }}

            function setActiveView(view) {{
                activeView = view;
                summaryTabs.forEach((tab) => {{
                    tab.classList.toggle("active", tab.dataset.view === view);
                }});
                applyFilters();
            }}

            function resetCheckboxes() {{
                document.querySelectorAll(
                    'input[name="experience"], input[name="arrangement"], input[name="source"], input[name="employment"], input[name="change"], input[name="state"]',
                ).forEach((input) => {{ input.checked = false; }});
            }}

            function clearFilters() {{
                keywordFilter.value = "";
                companyFilter.value = "";
                locationFilter.value = "";
                scoreRangeControl.reset(
                    DEFAULTS.scoreMin,
                    DEFAULTS.scoreMax,
                );
                salaryRangeControl.reset(
                    DEFAULTS.salaryMin,
                    DEFAULTS.salaryMax,
                );
                includeMissingSalary.checked = true;
                hideDismissed.checked = true;
                sortSelect.value = "score-desc";
                resetCheckboxes();
                setActiveView("all");
            }}

            cards.forEach((card) => {{
                card.querySelectorAll("[data-set-state]").forEach((button) => {{
                    button.addEventListener("click", () => {{
                        const requested = button.dataset.setState;
                        const current = cardState(card);

                        if (current === requested) {{
                            delete jobStates[card.dataset.jobKey];
                        }} else {{
                            jobStates[card.dataset.jobKey] = requested;
                        }}

                        saveJobStates();
                        refreshCardState(card);
                        applyFilters();
                    }});
                }});
            }});

            summaryTabs.forEach((tab) => {{
                tab.addEventListener("click", () => setActiveView(tab.dataset.view));
            }});

            [keywordFilter, companyFilter, locationFilter].forEach((input) => {{
                input.addEventListener("input", scheduleApplyFilters);
            }});

            [includeMissingSalary, hideDismissed].forEach((input) => {{
                input.addEventListener("change", applyFilters);
            }});

            document.querySelectorAll(
                'input[name="experience"], input[name="arrangement"], input[name="source"], input[name="employment"], input[name="change"], input[name="state"]',
            ).forEach((input) => {{
                input.addEventListener("change", applyFilters);
            }});

            sortSelect.addEventListener("change", () => {{
                sortCards();
                applyFilters();
            }});
            document.getElementById("clear-filters").addEventListener("click", clearFilters);

            scoreRangeControl = initDocumentDragRange(
                document.getElementById("score-range"),
                scoreMin,
                scoreMax,
            );

            salaryRangeControl = initDocumentDragRange(
                document.getElementById("salary-range"),
                salaryMin,
                salaryMax,
            );

            refreshAllCardStates();
            sortCards();
            updateRangeLabels();
            applyFilters();
        </script>
    </body>
    </html>
    """

    REPORT_FILE.write_text(report, encoding="utf-8")
    return REPORT_FILE


def public_job_data(job: dict) -> dict:
    """
    Remove internal lowercase caches and optionally omit descriptions
    before JSON/report output.
    """

    result = {
        key: value
        for key, value in job.items()
        if not key.startswith("__")
    }

    if not KEEP_DESCRIPTIONS_IN_OUTPUT:
        result.pop("description", None)

    return result


def evaluate_job_with_incremental_cache(
    job: dict,
    cached_jobs: dict,
    next_cached_jobs: dict,
) -> tuple[dict | None, bool]:
    """
    Return computed ranking fields and whether the result came from
    the incremental score cache.

    The caller has already removed non-U.S. and entry-level jobs.
    """

    cache_key = job_cache_key(job)
    content_hash = job_content_fingerprint(job)
    cached_entry = cached_jobs.get(cache_key)

    if (
        ENABLE_INCREMENTAL_SCORING
        and isinstance(cached_entry, dict)
        and safe_text(
            cached_entry.get("content_fingerprint")
        ) == content_hash
        and isinstance(
            cached_entry.get("computed"),
            dict,
        )
    ):
        next_cached_jobs[cache_key] = cached_entry
        return cached_entry["computed"], True

    # Safety guard for direct use outside the normal main pipeline.
    if not is_us_job(job):
        computed = {
            "retain": False,
            "reason": "non_us",
        }

    else:
        rule_score, reasons = calculate_rule_score(job)

        if rule_score <= -100:
            computed = {
                "retain": False,
                "reason": "excluded_title",
            }

        else:
            arrangement = detect_work_arrangement(job)

            location_priority, location_label = (
                get_location_priority(job)
            )

            location_points = location_priority * 5
            raw_total_score = rule_score + location_points
            final_score = normalize_score(raw_total_score)
            tier = assign_tier(final_score)

            computed = {
                "retain": True,
                "rule_score": rule_score,
                "work_arrangement": arrangement,
                "location_priority": location_priority,
                "location_priority_label": location_label,
                "location_points": location_points,
                "raw_total_score": raw_total_score,
                "final_score": final_score,
                "match_tier": tier,
                "score_reasons": reasons
                + [
                    {
                        "category": "Location",
                        "term": location_label,
                        "points": location_points,
                    }
                ],
            }

    next_cached_jobs[cache_key] = {
        "content_fingerprint": content_hash,
        "computed": computed,
    }

    return computed, False


def collect_enabled_sources_in_parallel() -> dict:
    """
    Run Greenhouse, Ashby, and Lever collectors simultaneously.

    Each ATS uses a different API host, so this does not increase the
    existing per-host worker count.
    """

    source_specs = {}

    if ENABLE_GREENHOUSE:
        source_specs["Greenhouse"] = (
            collect_greenhouse_jobs,
            GREENHOUSE_COMPANIES,
        )

    if ENABLE_ASHBY:
        source_specs["Ashby"] = (
            collect_ashby_jobs,
            ASHBY_COMPANIES,
        )

    if ENABLE_LEVER:
        source_specs["Lever"] = (
            collect_lever_jobs,
            LEVER_COMPANIES,
        )

    results = {}

    if not source_specs:
        return results

    if (
        not COLLECT_ATS_SOURCES_IN_PARALLEL
        or len(source_specs) == 1
    ):
        for source_name, (
            collector,
            companies,
        ) in source_specs.items():
            print(f"Collecting {source_name} jobs...")
            jobs, stats = collector(companies)
            results[source_name] = {
                "jobs": jobs,
                "stats": stats,
            }

        return results

    print(
        "Collecting Greenhouse, Ashby, and Lever "
        "in parallel..."
    )

    with ThreadPoolExecutor(
        max_workers=len(source_specs)
    ) as executor:
        future_map = {
            executor.submit(
                collector,
                companies,
            ): source_name
            for source_name, (
                collector,
                companies,
            ) in source_specs.items()
        }

        for future in as_completed(future_map):
            source_name = future_map[future]

            try:
                jobs, stats = future.result()
            except Exception as error:
                print(
                    f"{source_name} collector failed: "
                    f"{error}"
                )
                jobs = []
                stats = empty_collection_stats()
                stats["errors"] = 1

            results[source_name] = {
                "jobs": jobs,
                "stats": stats,
            }

    return results


# ============================================================
# MAIN
# ============================================================

def print_collection_stats(
    source_name: str,
    stats: dict,
) -> None:
    print()
    print(f"{source_name} collection")
    print("-" * 58)
    print(
        "Mode: "
        + (
            "full discovery"
            if stats.get("discovery_mode")
            else "confirmed boards only"
        )
    )
    print(
        "Confirmed boards in registry: "
        f"{stats.get('registry_boards', 0)}"
    )
    print(
        "Candidate boards this run: "
        f"{stats.get('candidate_boards', 0)}"
    )
    print(
        f"Active boards: {stats['active_boards']}"
    )
    print(
        "Active boards reused from cache: "
        f"{stats['cached_active']}"
    )
    print(
        "Missing boards skipped from cache: "
        f"{stats['cached_missing']}"
    )
    print(
        f"Network requests: {stats['network_requests']}"
    )
    print(f"Errors: {stats['errors']}")


def _run_pipeline_verbose() -> None:
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    print("No AI calls exist in this program.")
    print(
        "Greenhouse companies registered: "
        f"{len(GREENHOUSE_COMPANIES)}"
    )
    print(
        f"Ashby companies registered: {len(ASHBY_COMPANIES)}"
    )
    print(
        f"Lever companies registered: {len(LEVER_COMPANIES)}"
    )
    print(
        "Run mode: "
        + (
            "forced full discovery"
            if DISCOVERY_REQUESTED
            else "automatic fast refresh"
        )
    )
    print()

    started_at = time.perf_counter()

    source_results = (
        collect_enabled_sources_in_parallel()
    )

    greenhouse_jobs = source_results.get(
        "Greenhouse",
        {},
    ).get("jobs", [])

    ashby_jobs = source_results.get(
        "Ashby",
        {},
    ).get("jobs", [])

    lever_jobs = source_results.get(
        "Lever",
        {},
    ).get("jobs", [])

    greenhouse_stats = source_results.get(
        "Greenhouse",
        {},
    ).get("stats", empty_collection_stats())

    ashby_stats = source_results.get(
        "Ashby",
        {},
    ).get("stats", empty_collection_stats())

    lever_stats = source_results.get(
        "Lever",
        {},
    ).get("stats", empty_collection_stats())

    all_jobs = deduplicate_jobs(
        greenhouse_jobs
        + ashby_jobs
        + lever_jobs
    )

    collected_job_count = len(all_jobs)
    collection_finished_at = time.perf_counter()

    print()
    print(
        "Collection and deduplication finished in "
        f"{collection_finished_at - started_at:.1f} seconds."
    )

    prefilter_started_at = time.perf_counter()
    hard_entry_exclusions = {}
    non_us_excluded_count = 0
    scoring_candidates = []

    for job in all_jobs:
        # Location is cheaper to determine than scanning a full
        # description, so non-U.S. postings are discarded first.
        if not is_us_job(job):
            non_us_excluded_count += 1
            continue

        exclusion_reason = hard_entry_level_reason(job)

        if exclusion_reason:
            hard_entry_exclusions[exclusion_reason] = (
                hard_entry_exclusions.get(
                    exclusion_reason,
                    0,
                )
                + 1
            )
            continue

        scoring_candidates.append(job)

    hard_entry_excluded_count = sum(
        hard_entry_exclusions.values()
    )
    prefilter_finished_at = time.perf_counter()

    print(
        "Hard eligibility filtering finished in "
        f"{prefilter_finished_at - prefilter_started_at:.1f} seconds."
    )
    print(
        f"Filtering and scoring {len(scoring_candidates)} "
        "U.S. non-entry jobs..."
    )

    score_cache = load_job_score_cache()
    cached_score_jobs = score_cache.get("jobs", {})
    next_cached_score_jobs = {}

    previous_job_history = load_job_history()
    next_job_history = {}

    us_jobs = []
    visible_matches = []

    score_cache_hits = 0
    score_cache_misses = 0
    scoring_started_at = time.perf_counter()

    for job_number, job in enumerate(
        scoring_candidates,
        start=1,
    ):
        if (
            job_number % SCORING_PROGRESS_INTERVAL == 0
            or job_number == len(scoring_candidates)
        ):
            print(
                "Processed "
                f"{job_number}/{len(scoring_candidates)} jobs"
            )

        current_cache_key = job_cache_key(job)
        current_content_fingerprint = (
            job_content_fingerprint(job)
        )
        previous_content_fingerprint = (
            previous_job_history.get(current_cache_key)
        )

        if previous_content_fingerprint is None:
            change_status = "new"
        elif (
            previous_content_fingerprint
            != current_content_fingerprint
        ):
            change_status = "updated"
        else:
            change_status = "seen"

        next_job_history[current_cache_key] = (
            current_content_fingerprint
        )

        computed, cache_hit = (
            evaluate_job_with_incremental_cache(
                job,
                cached_score_jobs,
                next_cached_score_jobs,
            )
        )

        if cache_hit:
            score_cache_hits += 1
        else:
            score_cache_misses += 1

        if not computed or not computed.get("retain"):
            continue

        ranked_job = {
            **public_job_data(job),
            **computed,
        }

        us_jobs.append(ranked_job)

        if computed.get("match_tier") is not None:
            enrich_dashboard_job(
                job,
                ranked_job,
                change_status,
            )
            visible_matches.append(ranked_job)

    score_cache["jobs"] = next_cached_score_jobs
    save_job_score_cache(score_cache)
    save_job_history(next_job_history)

    scoring_finished_at = time.perf_counter()

    print(
        "Filtering and scoring finished in "
        f"{scoring_finished_at - scoring_started_at:.1f} seconds."
    )
    print(
        "Incremental scoring cache: "
        f"{score_cache_hits} hits, "
        f"{score_cache_misses} recalculated."
    )
    print("Sorting and writing the unified report...")

    sort_key = lambda job: (
        job["final_score"],
        job["raw_total_score"],
        job["location_priority"],
        job["rule_score"],
        job["updated_at"],
    )

    us_jobs.sort(key=sort_key, reverse=True)
    visible_matches.sort(
        key=sort_key,
        reverse=True,
    )

    if SAVE_ALL_US_JOBS_JSON:
        ALL_US_JOBS_FILE.write_text(
            json.dumps(
                us_jobs,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    MATCHES_FILE.write_text(
        json.dumps(
            visible_matches,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    report_file = create_report(visible_matches)
    output_finished_at = time.perf_counter()

    print(
        "Sorting and report output finished in "
        f"{output_finished_at - scoring_finished_at:.1f} seconds."
    )

    strong_count = sum(
        1
        for job in visible_matches
        if job["match_tier"] == "Strong match"
    )

    possible_count = sum(
        1
        for job in visible_matches
        if job["match_tier"] == "Possible match"
    )

    source_match_counts: dict[str, int] = {}

    for job in visible_matches:
        source_name = safe_text(
            job.get("source"),
            "Unknown",
        )

        source_match_counts[source_name] = (
            source_match_counts.get(source_name, 0) + 1
        )

    print()
    print("Finished")
    print("=" * 58)

    if ENABLE_GREENHOUSE:
        print_collection_stats(
            "Greenhouse",
            greenhouse_stats,
        )

    if ENABLE_ASHBY:
        print_collection_stats(
            "Ashby",
            ashby_stats,
        )

    if ENABLE_LEVER:
        print_collection_stats(
            "Lever",
            lever_stats,
        )

    elapsed_seconds = output_finished_at - started_at

    print()
    print("Combined results")
    print("-" * 58)
    print(f"Greenhouse jobs collected: {len(greenhouse_jobs)}")
    print(f"Ashby jobs collected: {len(ashby_jobs)}")
    print(f"Lever jobs collected: {len(lever_jobs)}")
    print(f"Unique jobs downloaded: {collected_job_count}")
    print(
        "Non-U.S. jobs excluded before scoring: "
        f"{non_us_excluded_count}"
    )
    print(
        "U.S. entry-level and internship jobs excluded "
        "before scoring: "
        f"{hard_entry_excluded_count}"
    )
    print(
        "U.S. non-entry jobs sent to scoring: "
        f"{len(scoring_candidates)}"
    )
    print(f"U.S.-eligible jobs retained: {len(us_jobs)}")

    if hard_entry_exclusions:
        top_entry_reasons = sorted(
            hard_entry_exclusions.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:8]

        print(
            "Top entry-level exclusion reasons: "
            + ", ".join(
                f"{reason}={count}"
                for reason, count in top_entry_reasons
            )
        )

    print(
        f"Strong matches ({MIN_STRONG_SCORE}+): "
        f"{strong_count}"
    )

    print(
        "Possible matches "
        f"({MIN_POSSIBLE_SCORE}-{MIN_STRONG_SCORE - 1}): "
        f"{possible_count}"
    )

    for source_name, count in sorted(
        source_match_counts.items()
    ):
        print(f"{source_name} visible matches: {count}")

    print(
        "Score cache: "
        f"{score_cache_hits} reused, "
        f"{score_cache_misses} recalculated"
    )
    print(f"Total elapsed time: {elapsed_seconds:.1f} seconds")

    print(
        "Post-download processing time: "
        f"{output_finished_at - collection_finished_at:.1f} seconds"
    )

    print("AI calls made: 0")
    print(
        "Score normalization: raw 50 -> 50, "
        "raw 65 -> 65, higher scores approach 100"
    )
    print(f"Interactive dashboard: {report_file}")
    print(
        "Force a complete board rediscovery with: "
        "python collect_all_ats.py --discover"
    )

    webbrowser.open(
        report_file.resolve().as_uri()
    )


def main() -> None:
    """
    Run the complete collector quietly.

    Internal discovery, filtering, scoring, cache, and dashboard
    diagnostics are captured. Only the requested final five lines
    are printed.
    """

    captured = io.StringIO()

    try:
        with contextlib.redirect_stdout(captured):
            _run_pipeline_verbose()
    except Exception:
        diagnostic_output = captured.getvalue()

        if diagnostic_output:
            print(diagnostic_output, file=sys.stderr, end="")

        raise

    wanted_prefixes = (
        "Greenhouse jobs collected:",
        "Ashby jobs collected:",
        "Lever jobs collected:",
        "Total elapsed time:",
        "Post-download processing time:",
    )

    found = {}

    for line in captured.getvalue().splitlines():
        stripped = line.strip()

        for prefix in wanted_prefixes:
            if stripped.startswith(prefix):
                found[prefix] = stripped

    missing = [
        prefix
        for prefix in wanted_prefixes
        if prefix not in found
    ]

    if missing:
        raise RuntimeError(
            "Run completed, but the concise summary was missing: "
            + ", ".join(missing)
        )

    print("Combined results")
    print("-" * 58)

    for prefix in wanted_prefixes:
        print(found[prefix])


if __name__ == "__main__":
    main()
