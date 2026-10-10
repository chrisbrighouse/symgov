"""Operator diagnostics must reach the log; nothing else gets noisier."""

from __future__ import annotations

import io
import logging

import pytest

from symgov_backend import logging_setup


@pytest.fixture(autouse=True)
def _restore():
    root = logging.getLogger(logging_setup.APPLICATION_LOGGER)
    handlers, level = list(root.handlers), root.level
    saved = {name: logging.getLogger(name).level for name in logging_setup.INFO_LOGGERS}
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, value in saved.items():
        logging.getLogger(name).setLevel(value)


def _application_handlers():
    root = logging.getLogger(logging_setup.APPLICATION_LOGGER)
    return [h for h in root.handlers if getattr(h, logging_setup._HANDLER_MARK, False)]


def test_configuring_twice_adds_one_handler():
    logging_setup.configure_application_logging()
    logging_setup.configure_application_logging()

    assert len(_application_handlers()) == 1


def test_the_ed_and_worker_loggers_log_at_info_and_other_loggers_do_not():
    logging_setup.configure_application_logging()

    for name in logging_setup.INFO_LOGGERS:
        assert logging.getLogger(name).isEnabledFor(logging.INFO)
    assert not logging.getLogger("symgov_backend.routes.catalog").isEnabledFor(logging.INFO)
    assert logging.getLogger("symgov_backend.routes.catalog").isEnabledFor(logging.WARNING)


def test_an_ed_info_line_is_written_with_a_timestamp_and_logger_name():
    logging_setup.configure_application_logging()
    [handler] = _application_handlers()
    stream = io.StringIO()
    handler.setStream(stream)

    logging.getLogger("symgov_backend.services.ed_orchestration").info("ed_chat status=answered reason=answered")
    logging.getLogger("symgov_backend.routes.catalog").info("not wanted")

    written = stream.getvalue()
    assert "INFO symgov_backend.services.ed_orchestration: ed_chat status=answered reason=answered" in written
    assert "not wanted" not in written
    assert written.split()[0].count("-") == 2  # an ISO date leads the line


def test_creating_the_app_configures_logging():
    from symgov_backend.app import create_app

    for handler in _application_handlers():
        logging.getLogger(logging_setup.APPLICATION_LOGGER).removeHandler(handler)
    create_app()

    assert len(_application_handlers()) == 1
