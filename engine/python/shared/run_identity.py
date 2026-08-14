from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path


RUN_TIMESTAMP_FORMAT = "%Y-%m-%d_%H%M%S"
MAX_RUN_NAME_SLUG_LENGTH = 24
RUN_NAME_HASH_LENGTH = 8


def sanitize_run_name(run_name: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]+', "_", run_name.strip())
    sanitized = re.sub(r"\s+", "_", sanitized)
    sanitized = sanitized.strip("._")
    if not sanitized:
        raise ValueError("run_name must contain at least one usable character.")
    return sanitized


def shorten_run_name_slug(run_name: str, max_length: int = MAX_RUN_NAME_SLUG_LENGTH) -> str:
    sanitized = sanitize_run_name(run_name)
    if len(sanitized) <= max_length:
        return sanitized
    digest = hashlib.sha1(sanitized.encode("utf-8")).hexdigest()[:RUN_NAME_HASH_LENGTH]
    prefix_length = max_length - RUN_NAME_HASH_LENGTH - 2
    prefix = sanitized[:prefix_length].rstrip("._")
    if not prefix:
        prefix = "run"
    return f"{prefix}__{digest}"


def format_run_timestamp(created_at: datetime) -> str:
    return created_at.strftime(RUN_TIMESTAMP_FORMAT)


def build_run_id(
    run_name: str | None,
    created_at: datetime,
    *,
    default_suffix: str | None = None,
    include_timestamp_for_named: bool = True,
) -> str:
    timestamp = format_run_timestamp(created_at)
    if run_name:
        slug = shorten_run_name_slug(run_name)
        if include_timestamp_for_named:
            return f"{timestamp}__{slug}"
        return slug
    if default_suffix:
        return f"{timestamp}_{default_suffix}"
    return timestamp


def unique_run_dir(
    base_dir: Path,
    run_name: str | None,
    *,
    created_at: datetime | None = None,
    default_suffix: str | None = None,
    include_timestamp_for_named: bool = False,
) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    stem = build_run_id(
        run_name,
        created_at or datetime.now(),
        default_suffix=default_suffix,
        include_timestamp_for_named=include_timestamp_for_named,
    )
    candidate = base_dir / stem
    suffix = 2
    while candidate.exists():
        candidate = base_dir / f"{stem}_{suffix:02d}"
        suffix += 1
    return candidate
