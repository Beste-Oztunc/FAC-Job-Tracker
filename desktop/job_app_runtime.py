#!/usr/bin/env python3
"""Runtime configuration bridge for the local Job Finder application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    seen: set[str] = set()
    cleaned: list[str] = []

    for item in value:
        text = str(item).strip()

        if not text:
            continue

        key = text.casefold()

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(text)

    return cleaned


def _clean_weighted_terms(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}

    cleaned: dict[str, int] = {}

    for raw_term, raw_points in value.items():
        term = str(raw_term).strip()

        if not term:
            continue

        try:
            points = int(raw_points)
        except (TypeError, ValueError):
            continue

        cleaned[term] = points

    return cleaned


def _slugify(name: str) -> str:
    try:
        from companies import slugify_company_name

        return slugify_company_name(name)
    except Exception:
        import re
        import unicodedata

        ascii_name = (
            unicodedata.normalize("NFKD", name)
            .encode("ascii", "ignore")
            .decode("ascii")
            .lower()
        )
        return re.sub(r"[^a-z0-9]+", "", ascii_name)


def _known_company_token(
    company_name: str,
    ats: str,
) -> str:
    try:
        from companies import get_ats_token

        return get_ats_token(company_name, ats)
    except Exception:
        return _slugify(company_name)


def _build_company_dicts(
    namespace: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, dict[str, str]]:
    company_config = config.get("companies", {})
    enabled_names = _clean_string_list(
        company_config.get("enabled", [])
    )

    current_names = set()

    for key in (
        "GREENHOUSE_COMPANIES",
        "ASHBY_COMPANIES",
        "LEVER_COMPANIES",
    ):
        current = namespace.get(key, {})

        if isinstance(current, dict):
            current_names.update(current.keys())

    # A missing selection means preserve the collector's current
    # company universe rather than accidentally disabling everything.
    if not enabled_names:
        enabled_names = sorted(current_names)

    selected = set(enabled_names)

    result = {
        "greenhouse": {},
        "ashby": {},
        "lever": {},
    }

    for company_name in enabled_names:
        for ats in result:
            result[ats][company_name] = _known_company_token(
                company_name,
                ats,
            )

    custom_companies = company_config.get("custom", [])

    if isinstance(custom_companies, list):
        for item in custom_companies:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            ats = str(item.get("ats", "auto")).strip().lower()
            token = str(item.get("token", "")).strip()

            if not name:
                continue

            selected.add(name)
            token = token or _slugify(name)

            targets = (
                ("greenhouse", "ashby", "lever")
                if ats == "auto"
                else (ats,)
            )

            for target in targets:
                if target in result:
                    result[target][name] = token

    # Preserve confirmed collector-specific token overrides.
    for company_name, token in namespace.get(
        "ASHBY_TOKEN_OVERRIDES",
        {},
    ).items():
        if company_name in result["ashby"]:
            result["ashby"][company_name] = token

    for company_name, token in namespace.get(
        "LEVER_TOKEN_OVERRIDES",
        {},
    ).items():
        if company_name in result["lever"]:
            result["lever"][company_name] = token

    return result


def apply_collector_config(
    namespace: dict[str, Any],
    config_path: Path,
) -> None:
    config = load_json(config_path, {})

    if not isinstance(config, dict):
        return

    rules = config.get("rules", {})

    if isinstance(rules, dict):
        weighted_mappings = {
            "target_role_phrases": "TARGET_ROLE_PHRASES",
            "title_specialty_terms": "TITLE_SPECIALTY_TERMS",
            "domain_terms": "DOMAIN_TERMS",
            "customer_facing_terms": "CUSTOMER_FACING_TERMS",
        }

        for config_key, global_name in weighted_mappings.items():
            if config_key not in rules:
                continue

            cleaned = _clean_weighted_terms(
                rules.get(config_key)
            )

            if cleaned:
                namespace[global_name] = cleaned

        list_mappings = {
            "excluded_title_terms": "EXCLUDED_TITLE_TERMS",
            "hard_entry_title_terms": "HARD_ENTRY_TITLE_TERMS",
            "local_area_terms": "LOCAL_AREA_TERMS",
            "remote_terms": "REMOTE_TERMS",
            "hybrid_terms": "HYBRID_TERMS",
            "onsite_terms": "ONSITE_TERMS",
        }

        for config_key, global_name in list_mappings.items():
            if config_key not in rules:
                continue

            namespace[global_name] = _clean_string_list(
                rules.get(config_key)
            )

        if "excluded_experience_levels" in rules:
            namespace["EXCLUDED_EXPERIENCE_LEVELS"] = set(
                _clean_string_list(
                    rules.get("excluded_experience_levels")
                )
            )

        for config_key, global_name in (
            ("min_possible_score", "MIN_POSSIBLE_SCORE"),
            ("min_strong_score", "MIN_STRONG_SCORE"),
        ):
            if config_key not in rules:
                continue

            try:
                namespace[global_name] = int(
                    rules.get(config_key)
                )
            except (TypeError, ValueError):
                pass

    sources = config.get("sources", {})

    if isinstance(sources, dict):
        namespace["ENABLE_GREENHOUSE"] = bool(
            sources.get("greenhouse", True)
        )
        namespace["ENABLE_ASHBY"] = bool(
            sources.get("ashby", True)
        )
        namespace["ENABLE_LEVER"] = bool(
            sources.get("lever", True)
        )

    company_dicts = _build_company_dicts(
        namespace,
        config,
    )

    namespace["GREENHOUSE_COMPANIES"] = company_dicts[
        "greenhouse"
    ]
    namespace["ASHBY_COMPANIES"] = company_dicts["ashby"]
    namespace["LEVER_COMPANIES"] = company_dicts["lever"]


def apply_agent_config(
    namespace: dict[str, Any],
    config_path: Path,
) -> None:
    """
    Replace the bundled example profile with the profile entered in
    the local application. No personal example data is retained.
    """

    config = load_json(config_path, {})

    if not isinstance(config, dict):
        config = {}

    profile_config = config.get("profile", {})

    if not isinstance(profile_config, dict):
        profile_config = {}

    resume_text = str(
        profile_config.get("resume_text", "")
    ).strip()
    desired_job_text = str(
        profile_config.get("desired_job_text", "")
    ).strip()
    location_preference = str(
        profile_config.get("location_preference", "")
    ).strip()
    additional_notes = str(
        profile_config.get("additional_notes", "")
    ).strip()

    desired_roles = [
        item.strip()
        for item in desired_job_text.replace(
            "\n",
            ",",
        ).split(",")
        if item.strip()
    ]

    preferences = [
        value
        for value in (
            location_preference,
            additional_notes,
        )
        if value
    ]

    namespace["USER_PROFILE"] = {
        "headline": "Candidate profile supplied through the local application",
        "resume_text": resume_text,
        "desired_role_description": desired_job_text,
        "target_roles": desired_roles,
        "preferences": preferences,
    }

    agent_config = config.get("agent", {})

    if isinstance(agent_config, dict):
        try:
            namespace["MARKET_MIN_SCORE"] = float(
                agent_config.get(
                    "market_min_score",
                    namespace.get("MARKET_MIN_SCORE", 60),
                )
            )
        except (TypeError, ValueError):
            pass

