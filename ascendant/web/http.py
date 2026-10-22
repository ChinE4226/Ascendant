"""JSON request parsing and response encoding."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
from typing import Any


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > 64 * 1024:
        raise ValueError("Request body is too large.")
    try:
        body = handler.rfile.read(length).decode("utf-8")
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    return data
