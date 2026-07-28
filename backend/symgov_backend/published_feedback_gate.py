from __future__ import annotations

import os
from pathlib import Path


DEFAULT_PUBLISHED_FEEDBACK_PAUSE_FILE = Path(
    "/data/symgov-runtime/maintenance/published-feedback.pause"
)


def published_feedback_pause_file() -> Path:
    configured = os.getenv("SYMGOV_PUBLISHED_FEEDBACK_PAUSE_FILE")
    return Path(configured) if configured else DEFAULT_PUBLISHED_FEEDBACK_PAUSE_FILE


def published_feedback_claims_paused() -> bool:
    """Read live marker state on every call; never cache governance state."""
    try:
        return published_feedback_pause_file().is_file()
    except OSError:
        return True


def published_feedback_paused_response_body() -> dict:
    return {
        "error": "published_feedback_paused",
        "detail": "Published feedback and review requests are temporarily unavailable.",
        "retryable": True,
    }
