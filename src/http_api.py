"""HTTP 路由与统一错误输出。"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from .domain import Actor, DomainError, PermissionDenied, ValidationError


RECORD_RE = re.compile(r"^/api/records/(\d+)$")
ACTION_RE = re.compile(r"^/api/records/(\d+)/actions/([a-z_]+)$")
AUDIT_RE = re.compile(r"^/api/records/(\d+)/audit$")
ARCHIVE_DOSSIER_RE = re.compile(r"^/api/archive/dossiers/(\d+)$")
ARCHIVE_DOSSIER_ACTION_RE = re.compile(r"^/api/archive/dossiers/(\d+)/actions/(borrow|return)$")
ARCHIVE_REPAIR_ACTION_RE = re.compile(r"^/api/archive/repairs/(\d+)/actions/complete$")


def _flag(query: Dict[str, Any], key: str) -> bool:
    return query.get(key, ["0"])[0] in {"1", "true"}


def make_handler(service: Any, static_dir: Path, archive: Any = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tax-audit/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _actor(self) -> Actor:
            user_id = self.headers.get("X-User-Id", "").strip()
            role = self.headers.get("X-Role", "").strip()
            if not user_id or not role:
                raise PermissionDenied("缺少X-User-Id或X-Role")
            return Actor(user_id=user_id, role=role, organization=self.headers.get("X-Org", ""))

        def _body(self) -> Dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValidationError("Content-Length无效") from exc
            if length > 1024 * 1024:
                raise ValidationError("请求体过大")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError("请求体必须是JSON") from exc
            if not isinstance(data, dict):
                raise ValidationError("JSON顶层必须是对象")
            return data

        def _send(self, status: int, payload: Any, content_type: str = "application/json; charset=utf-8") -> None:
            if content_type.startswith("application/json"):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            else:
                body = payload
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_error(self, exc: Exception) -> None:
            if isinstance(exc, DomainError):
                self._send(exc.status, {"error": exc.code, "message": str(exc)})
            else:
                self._send(500, {"error": "internal_error", "message": "服务内部错误"})

        def do_GET(self) -> None:
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._send(200, {"status": "ok", "service": "tax-audit", "database": service.repository.health()})
                    return
                if parsed.path == "/":
                    page = (static_dir / "index.html").read_bytes()
                    self._send(200, page, "text/html; charset=utf-8")
                    return
                if parsed.path == "/archive":
                    page = (static_dir / "archive.html").read_bytes()
                    self._send(200, page, "text/html; charset=utf-8")
                    return
                if parsed.path == "/api/records":
                    query = parse_qs(parsed.query)
                    records = service.list_records(self._actor(), state=query.get("state", [None])[0], limit=int(query.get("limit", ["100"])[0]))
                    self._send(200, {"items": records})
                    return
                match = RECORD_RE.match(parsed.path)
                if match:
                    self._send(200, service.get_record(self._actor(), int(match.group(1))))
                    return
                match = AUDIT_RE.match(parsed.path)
                if match:
                    self._send(200, {"items": service.timeline(self._actor(), int(match.group(1)))})
                    return
                if parsed.path == "/api/stats":
                    self._send(200, service.stats(self._actor()))
                    return
                if archive is not None:
                    query = parse_qs(parsed.query)
                    if parsed.path == "/api/archive/boxes":
                        self._send(200, {"items": archive.list_boxes(self._actor())})
                        return
                    if parsed.path == "/api/archive/dossiers":
                        self._send(200, {"items": archive.list_dossiers(self._actor(), status=query.get("status", [None])[0], overdue_only=_flag(query, "overdue"))})
                        return
                    match = ARCHIVE_DOSSIER_RE.match(parsed.path)
                    if match:
                        self._send(200, archive.get_dossier(self._actor(), int(match.group(1))))
                        return
                    if parsed.path == "/api/archive/loans":
                        self._send(200, {"items": archive.list_loans(self._actor(), active_only=_flag(query, "active"), overdue_only=_flag(query, "overdue"))})
                        return
                    if parsed.path == "/api/archive/repairs":
                        self._send(200, {"items": archive.list_repairs(self._actor(), status=query.get("status", [None])[0])})
                        return
                self._send(404, {"error": "not_found", "message": "路径不存在"})
            except Exception as exc:
                self._handle_error(exc)

        def do_POST(self) -> None:
            try:
                parsed = urlparse(self.path)
                body = self._body()
                if parsed.path == "/api/records":
                    record = service.create(self._actor(), body.get("reference", ""), body.get("data", {}))
                    self._send(201, record)
                    return
                match = ACTION_RE.match(parsed.path)
                if match:
                    version = body.get("expected_version")
                    if not isinstance(version, int):
                        raise ValidationError("expected_version必须是整数")
                    record = service.act(self._actor(), int(match.group(1)), version, match.group(2), body.get("data", {}))
                    self._send(200, record)
                    return
                if archive is not None:
                    if parsed.path == "/api/archive/boxes":
                        self._send(201, archive.create_box(self._actor(), body.get("data", {})))
                        return
                    if parsed.path == "/api/archive/dossiers":
                        self._send(201, archive.archive_record(self._actor(), body.get("data", {})))
                        return
                    match = ARCHIVE_DOSSIER_ACTION_RE.match(parsed.path)
                    if match:
                        dossier_id = int(match.group(1))
                        if match.group(2) == "borrow":
                            self._send(201, archive.borrow(self._actor(), dossier_id, body.get("data", {})))
                        else:
                            self._send(200, archive.return_dossier(self._actor(), dossier_id, body.get("data", {})))
                        return
                    match = ARCHIVE_REPAIR_ACTION_RE.match(parsed.path)
                    if match:
                        self._send(200, archive.complete_repair(self._actor(), int(match.group(1)), body.get("data", {})))
                        return
                self._send(404, {"error": "not_found", "message": "路径不存在"})
            except Exception as exc:
                self._handle_error(exc)

    return Handler


def create_server(host: str, port: int, service: Any, static_dir: Path, archive: Any = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(service, static_dir, archive))
