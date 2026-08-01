"""Bounded, secret-safe reporting client for the Langfuse legacy Metrics API."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.client import HTTPSConnection
import ipaddress
import json
import math
import os
import re
import socket
import ssl
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_APPROVED_HTTP_ORIGIN = "http://symgov-langfuse:3000"
_MAX_RESPONSE_BYTES = 262_144
_MAX_ROWS = 100
_MAX_NUMBER = 1_000_000_000_000_000
_DECIMAL_RE = re.compile(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,18})?\Z")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,127}\Z", re.ASCII)
_MODEL_SECRET_RE = re.compile(
    r"(?:bearer|api[_-]?key|secret|password|token|(?:sk|pk)-|"
    r"akia[0-9a-z]{12,}|gh[pousr]_|xox[baprs]-|eyj[a-z0-9_-]{8,}\.|https?://|data:)",
    re.IGNORECASE,
)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(ProxyHandler({}), _NoRedirectHandler())


def urlopen(request: Request, *, timeout: float):
    return _OPENER.open(request, timeout=timeout)


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, hostname: str, port: int, address: str, timeout: float):
        self._pinned_address = address
        self._ssl_context = ssl.create_default_context()
        super().__init__(hostname, port=port, timeout=timeout, context=self._ssl_context)

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            None,
        )
        self.sock = self._ssl_context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPSResponse:
    def __init__(self, response, connection: HTTPSConnection):
        self._response = response
        self._connection = connection
        self.status = response.status

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self._response.close()
        self._connection.close()


def _open_pinned_https(
    request: Request,
    *,
    timeout: float,
    addresses: frozenset[str],
):
    parsed = urlparse(request.full_url)
    if parsed.scheme != "https" or not parsed.hostname or not addresses:
        raise RuntimeError("Langfuse destination could not be safely opened")
    connection = _PinnedHTTPSConnection(
        parsed.hostname,
        parsed.port or 443,
        sorted(addresses)[0],
        timeout,
    )
    selector = parsed.path or "/"
    if parsed.query:
        selector += "?" + parsed.query
    try:
        connection.request(
            request.get_method(),
            selector,
            body=request.data,
            headers=dict(request.header_items()),
        )
        return _PinnedHTTPSResponse(connection.getresponse(), connection)
    except Exception:
        connection.close()
        raise


def _valid_base_url(value: Any) -> str | None:
    if type(value) is not str or not value or len(value) > 2048 or any(ord(c) < 0x21 or ord(c) > 0x7E for c in value):
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        return None
    origin = value.rstrip("/")
    if origin == _APPROVED_HTTP_ORIGIN:
        return origin
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    try:
        address = ipaddress.ip_address(parsed.hostname)
        if address.is_private or address.is_loopback or address.is_link_local:
            return None
    except ValueError:
        if parsed.hostname == "localhost" or "." not in parsed.hostname:
            return None
    if port not in {None, 443}:
        return None
    return origin


def _credential(value: Any) -> bool:
    return type(value) is str and 1 <= len(value) <= 512 and ":" not in value and all(0x21 <= ord(c) <= 0x7E for c in value)


def _resolved_global_addresses(hostname: str, port: int) -> frozenset[str]:
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        addresses = frozenset(answer[4][0] for answer in answers)
    except (OSError, ValueError):
        raise RuntimeError("Langfuse destination could not be safely resolved") from None
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise RuntimeError("Langfuse destination is not globally routable")
    return addresses


@dataclass(frozen=True)
class LangfuseQueryConfig:
    enabled: bool = False
    configuration_error: bool = field(default=False, repr=False)
    base_url: str | None = None
    public_key: str | None = field(default=None, repr=False)
    secret_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a bool")
        if type(self.configuration_error) is not bool or (self.enabled and self.configuration_error):
            raise ValueError("configuration state is invalid")
        if type(self.timeout_seconds) not in {int, float} or isinstance(self.timeout_seconds, bool) or not math.isfinite(self.timeout_seconds) or not 0.05 <= self.timeout_seconds <= 30:
            raise ValueError("timeout must be finite and bounded")
        if self.enabled and (_valid_base_url(self.base_url) is None or not _credential(self.public_key) or not _credential(self.secret_key)):
            raise ValueError("enabled Langfuse query requires complete safe configuration")

    @classmethod
    def from_env(cls) -> "LangfuseQueryConfig":
        if os.getenv("SYMGOV_LANGFUSE_QUERY_ENABLED") != "true":
            return cls()
        try:
            timeout = float(os.getenv("SYMGOV_LLM_TELEMETRY_TIMEOUT_SECONDS", "3"))
            return cls(
                enabled=True,
                base_url=os.getenv("SYMGOV_LANGFUSE_QUERY_BASE_URL"),
                public_key=os.getenv("SYMGOV_LLM_TELEMETRY_PUBLIC_KEY"),
                secret_key=os.getenv("SYMGOV_LLM_TELEMETRY_SECRET_KEY"),
                timeout_seconds=timeout,
            )
        except (TypeError, ValueError):
            return cls(configuration_error=True)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _number(row: dict[str, Any], key: str, *, integer: bool) -> int | float:
    value = row.get(key)
    if type(value) is str:
        if len(value) > 35 or _DECIMAL_RE.fullmatch(value) is None:
            raise ValueError("Langfuse returned malformed bounded metrics")
        try:
            value = Decimal(value)
        except InvalidOperation:
            raise ValueError("Langfuse returned malformed bounded metrics") from None
    if type(value) not in {int, float, Decimal} or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= _MAX_NUMBER:
        raise ValueError("Langfuse returned malformed bounded metrics")
    if integer and value != int(value):
        raise ValueError("Langfuse returned malformed bounded metrics")
    return int(value) if integer else float(value)


def _parse_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) - {"data"} or not isinstance(payload.get("data"), list):
        raise ValueError("Langfuse returned a malformed metrics response")
    rows = payload["data"]
    if len(rows) > _MAX_ROWS:
        raise ValueError("Langfuse returned too many metrics rows")
    parsed = []
    for row in rows:
        expected = {
            "providedModelName", "count_count", "sum_inputTokens",
            "sum_outputTokens", "sum_totalTokens", "sum_totalCost",
        }
        if not isinstance(row, dict) or set(row) != expected:
            raise ValueError("Langfuse returned a malformed metrics response")
        model = row["providedModelName"]
        if type(model) is not str or _MODEL_RE.fullmatch(model) is None or _MODEL_SECRET_RE.search(model):
            raise ValueError("Langfuse returned a malformed model label")
        parsed.append({
            "model": model,
            "observations": _number(row, "count_count", integer=True),
            "inputTokens": _number(row, "sum_inputTokens", integer=True),
            "outputTokens": _number(row, "sum_outputTokens", integer=True),
            "totalTokens": _number(row, "sum_totalTokens", integer=True),
            "totalCostUsd": _number(row, "sum_totalCost", integer=False),
        })
    return sorted(parsed, key=lambda item: (-item["observations"], item["model"]))


def query_langfuse_usage(config: LangfuseQueryConfig, start: datetime, end: datetime) -> dict[str, Any]:
    if config.configuration_error:
        message = "Langfuse reporting is misconfigured and unavailable."
        return {"status": "unavailable", "message": message, "totals": None, "byModel": None}
    if not config.enabled:
        return {"status": "disabled", "message": "Langfuse reporting is disabled.", "totals": None, "byModel": None}
    base_url = _valid_base_url(config.base_url)
    if base_url is None or config.public_key is None or config.secret_key is None:
        raise RuntimeError("Langfuse reporting configuration is unavailable")
    parsed_base = urlparse(base_url)
    resolved_addresses = None
    if base_url != _APPROVED_HTTP_ORIGIN:
        resolved_addresses = _resolved_global_addresses(parsed_base.hostname or "", parsed_base.port or 443)
    query = {
        "view": "observations",
        "metrics": [{"measure": name, "aggregation": "sum" if name != "count" else "count"} for name in ("count", "inputTokens", "outputTokens", "totalTokens", "totalCost")],
        "dimensions": [{"field": "providedModelName"}],
        "filters": [{
            "column": "type",
            "operator": "=",
            "value": "GENERATION",
            "type": "string",
        }],
        "fromTimestamp": _utc_iso(start),
        "toTimestamp": _utc_iso(end),
        "config": {"row_limit": _MAX_ROWS},
    }
    credentials = base64.b64encode(f"{config.public_key}:{config.secret_key}".encode("utf-8")).decode("ascii")
    request = Request(
        f"{base_url}/api/public/metrics?query={quote(json.dumps(query, separators=(',', ':')))}",
        headers={"Authorization": f"Basic {credentials}", "Accept": "application/json"},
        method="GET",
    )
    if resolved_addresses is None:
        response_context = urlopen(request, timeout=float(config.timeout_seconds))
    else:
        response_context = _open_pinned_https(
            request,
            timeout=float(config.timeout_seconds),
            addresses=resolved_addresses,
        )
    with response_context as response:
        if getattr(response, "status", 0) != 200:
            raise RuntimeError("Langfuse metrics request failed")
        body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise RuntimeError("Langfuse metrics response exceeded the safe limit")
    try:
        rows = _parse_rows(json.loads(body.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("Langfuse metrics response was malformed") from exc
    totals = {
        "observations": sum(item["observations"] for item in rows),
        "inputTokens": sum(item["inputTokens"] for item in rows),
        "outputTokens": sum(item["outputTokens"] for item in rows),
        "totalTokens": sum(item["totalTokens"] for item in rows),
        "totalCostUsd": sum(item["totalCostUsd"] for item in rows),
    }
    if any(
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not 0 <= value <= _MAX_NUMBER
        for value in totals.values()
    ):
        raise RuntimeError("Langfuse aggregate metrics exceeded safe bounds")
    return {"status": "available", "message": "Langfuse metrics are available.", "totals": totals, "byModel": rows}


def safe_langfuse_usage(config: LangfuseQueryConfig, start: datetime, end: datetime) -> tuple[dict[str, Any], list[str]]:
    try:
        result = query_langfuse_usage(config, start, end)
        return result, ([] if result["status"] == "available" else [result["message"]])
    except Exception:
        message = "Langfuse metrics are temporarily unavailable."
        return {"status": "unavailable", "message": message, "totals": None, "byModel": None}, [message]
