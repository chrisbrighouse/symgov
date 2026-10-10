"""Makes the application's own diagnostic log lines reach the container log.

Nothing configured Python logging, so everything below WARNING from
`symgov_backend` was dropped: Ed's one-line `ed_chat` outcome (status, mode,
reason, retrieval, tools) and the Catalog embedding worker's progress line were
being written and never seen. A real symptom hid for days behind that
(2026-10-10: two suggested questions refused on a citation format, with no log
line to say so).

Only the loggers that carry deliberate operator diagnostics are raised to
INFO. They log counts, outcomes and error types, never questions, answers or
symbol text. Every other logger keeps its level, so the log does not become
noisier anywhere else.
"""

from __future__ import annotations

import logging

APPLICATION_LOGGER = "symgov_backend"
INFO_LOGGERS = (
    "symgov_backend.services.ed_orchestration",
    "symgov_backend.catalog_embedding_worker",
)
_HANDLER_MARK = "_symgov_application_handler"


def configure_application_logging() -> None:
    """Idempotent: safe to call from every `create_app()`."""
    root = logging.getLogger(APPLICATION_LOGGER)
    if not any(getattr(handler, _HANDLER_MARK, False) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)
    root.setLevel(logging.WARNING)
    for name in INFO_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
