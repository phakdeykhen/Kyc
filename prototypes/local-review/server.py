#!/usr/bin/env python3
"""A single-operator KYC prototype. Python 3.10+, standard library only."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parent
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_BODY_SIZE = 7 * 1024 * 1024 + 4096
KINDS = {"identity": "Identity document", "address": "Proof of address"}
STATUSES = {"under_review", "needs_information", "approved", "rejected"}
FINAL_STATUSES = {"approved", "rejected"}
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reference(record: dict) -> str:
    return f"KYC-{record['created_at'][:4]}-{record['id']:04d}"


class APIError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def text_field(body: dict, key: str, maximum: int, required: bool = False) -> str:
    value = body.get(key, "")
    if not isinstance(value, str):
        raise APIError(f"{key.replace('_', ' ').capitalize()} must be text.")
    value = value.strip()
    if required and not value:
        raise APIError(f"{key.replace('_', ' ').capitalize()} is required.")
    if len(value) > maximum or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise APIError(f"{key.replace('_', ' ').capitalize()} is invalid or too long.")
    return value


def option_field(body: dict, key: str, choices: set[str], default: str = "") -> str:
    value = body.get(key, default)
    if not isinstance(value, str) or value not in choices:
        raise APIError(f"Select a valid {key.replace('_', ' ')}.")
    return value


def demo_pdf(name: str, kind: str) -> bytes:
    """Create a real, downloadable PDF clearly labeled as fictional demo data."""
    lines = ["KYC WORKSPACE - FICTIONAL SAMPLE", kind, name, "For testing the local review workflow only."]
    commands = ["BT /F1 16 Tf 54 740 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -36 Td")
        line = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


class Workspace:
    def __init__(self, data_directory: Path, seed: bool = True):
        self.data_directory = data_directory.resolve()
        self.upload_directory = self.data_directory / "uploads"
        self.data_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.upload_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database_path = self.data_directory / "workspace.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript((ROOT / "schema.sql").read_text())
            if seed and not db.execute("SELECT 1 FROM cases LIMIT 1").fetchone():
                self.seed(db)
            db.execute("PRAGMA optimize")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def audit(self, db: sqlite3.Connection, case_id: int, action: str, detail: str, at: str | None = None) -> None:
        db.execute(
            "INSERT INTO activity (case_id, action, detail, actor, created_at) VALUES (?, ?, ?, ?, ?)",
            (case_id, action, detail, "Local reviewer", at or timestamp()),
        )

    def seed(self, db: sqlite3.Connection) -> None:
        samples = [
            ("Sok Dara", "Cambodia", "individual", "medium", "under_review", 2, 1),
            ("Maya Chen", "Singapore", "individual", "low", "approved", 2, 2),
            ("Lina Vann", "Cambodia", "individual", "low", "under_review", 1, 0),
            ("Riverbend Trading", "Thailand", "business", "high", "needs_information", 1, 0),
            ("Alex Nguyen", "Vietnam", "individual", "medium", "under_review", 2, 0),
            ("Nora Lim", "Malaysia", "individual", "low", "approved", 2, 2),
            ("Morgan Ellis", "United States", "individual", "high", "under_review", 0, 0),
            ("Kai Tan", "Singapore", "individual", "medium", "rejected", 1, 0),
        ]
        now = datetime.now(timezone.utc)
        for index, (name, country, customer_type, risk, status, doc_count, verified_count) in enumerate(samples):
            created = (now - timedelta(days=index + 1, hours=2)).isoformat(timespec="seconds")
            updated = (now - timedelta(hours=index * 3 + 1)).isoformat(timespec="seconds")
            email = name.lower().replace(" ", ".") + "@example.com"
            result = db.execute(
                """INSERT INTO cases (full_name, email, country, customer_type, purpose, risk_level, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, email, country, customer_type, "Demo customer onboarding", risk, status, created, updated),
            )
            case_id = result.lastrowid
            self.audit(db, case_id, "created", "Fictional sample case created.", created)
            for doc_index, kind in enumerate(list(KINDS)[:doc_count]):
                data = demo_pdf(name, KINDS[kind])
                storage_name = secrets.token_hex(16) + ".pdf"
                (self.upload_directory / storage_name).write_bytes(data)
                db.execute(
                    """INSERT INTO documents
                       (case_id, kind, original_name, storage_name, mime_type, size, verified, is_sample, uploaded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (case_id, kind, f"sample-{kind}.pdf", storage_name, "application/pdf", len(data), int(doc_index < verified_count), created),
                )
            details = {
                "approved": "Sample approval: both documents checked.",
                "needs_information": "Sample request: upload the proof of address.",
                "rejected": "Sample rejection: submitted identity details did not match.",
                "under_review": "Sample case queued for manual review.",
            }
            self.audit(db, case_id, status, details[status], updated)

    @staticmethod
    def require_case(db: sqlite3.Connection, case_id: int) -> dict:
        row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not row:
            raise APIError("Case not found.", 404)
        return dict(row)

    @staticmethod
    def require_open(record: dict) -> None:
        if record["status"] in FINAL_STATUSES:
            raise APIError("Reopen this case before changing its documents.", 409)

    @staticmethod
    def serialise_document(row: sqlite3.Row) -> dict:
        doc = dict(row)
        doc.pop("storage_name", None)
        doc["verified"] = bool(doc["verified"])
        doc["is_sample"] = bool(doc["is_sample"])
        doc["download_url"] = f"/api/documents/{doc['id']}/file"
        return doc

    def list_cases(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT c.*, COUNT(d.id) AS document_count, COALESCE(SUM(d.verified), 0) AS verified_count
                   FROM cases c LEFT JOIN documents d ON d.case_id = c.id
                   GROUP BY c.id ORDER BY c.created_at DESC, c.id DESC"""
            ).fetchall()
            return [dict(row) | {"reference": reference(dict(row))} for row in rows]

    def case_detail(self, case_id: int) -> dict:
        with self.connect() as db:
            record = self.require_case(db, case_id)
            record["reference"] = reference(record)
            record["documents"] = [self.serialise_document(row) for row in db.execute(
                "SELECT * FROM documents WHERE case_id = ? ORDER BY kind DESC", (case_id,)
            )]
            record["activity"] = [dict(row) for row in db.execute(
                "SELECT * FROM activity WHERE case_id = ? ORDER BY id DESC", (case_id,)
            )]
            return record

    def recent_activity(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) | {"reference": f"KYC-{row['case_created'][:4]}-{row['case_id']:04d}"} for row in db.execute(
                """SELECT a.*, c.full_name, c.created_at AS case_created FROM activity a
                   JOIN cases c ON c.id = a.case_id ORDER BY a.id DESC LIMIT 100"""
            )]

    def create_case(self, body: dict) -> dict:
        name = text_field(body, "full_name", 120, True)
        email = text_field(body, "email", 254, True).lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise APIError("Enter a valid email address.")
        country = text_field(body, "country", 80, True)
        phone = text_field(body, "phone", 40)
        purpose = text_field(body, "purpose", 500)
        customer_type = option_field(body, "customer_type", {"individual", "business"}, "individual")
        risk = option_field(body, "risk_level", {"low", "medium", "high"}, "medium")
        now = timestamp()
        with self.connect() as db:
            result = db.execute(
                """INSERT INTO cases (full_name, email, phone, country, customer_type, purpose, risk_level, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'under_review', ?, ?)""",
                (name, email, phone, country, customer_type, purpose, risk, now, now),
            )
            case_id = result.lastrowid
            self.audit(db, case_id, "created", "Customer case created and added to the review queue.")
        return self.case_detail(case_id)

    def upload(self, case_id: int, kind: str, body: dict) -> dict:
        if kind not in KINDS:
            raise APIError("Select a valid document category.")
        filename = text_field(body, "filename", 160, True)
        if "/" in filename or "\\" in filename or any(ord(c) < 32 for c in filename):
            raise APIError("Use a filename without path separators or control characters.")
        encoded = body.get("content_base64")
        if not isinstance(encoded, str) or len(encoded) > ((MAX_FILE_SIZE + 2) // 3) * 4:
            raise APIError("The document must be no larger than 5 MB.", 413)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise APIError("The file could not be read. Choose it again.") from None
        if not data or len(data) > MAX_FILE_SIZE:
            raise APIError("Choose a non-empty document no larger than 5 MB.", 413)
        extension = Path(filename).suffix.lower()
        if data.startswith(b"%PDF-") and extension == ".pdf":
            mime, suffix = "application/pdf", ".pdf"
        elif data.startswith(b"\x89PNG\r\n\x1a\n") and extension == ".png":
            mime, suffix = "image/png", ".png"
        elif data.startswith(b"\xff\xd8\xff") and extension in {".jpg", ".jpeg"}:
            mime, suffix = "image/jpeg", ".jpg"
        else:
            raise APIError("Choose a PDF, PNG, or JPEG file with a matching extension.")
        storage_name = secrets.token_hex(16) + suffix
        path = self.upload_directory / storage_name
        old_path = None
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                record = self.require_case(db, case_id)
                self.require_open(record)
                old = db.execute("SELECT storage_name FROM documents WHERE case_id = ? AND kind = ?", (case_id, kind)).fetchone()
                if old:
                    old_path = self.upload_directory / old["storage_name"]
                path.write_bytes(data)
                now = timestamp()
                db.execute(
                    """INSERT INTO documents (case_id, kind, original_name, storage_name, mime_type, size, uploaded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(case_id, kind) DO UPDATE SET original_name=excluded.original_name,
                       storage_name=excluded.storage_name, mime_type=excluded.mime_type, size=excluded.size,
                       verified=0, is_sample=0, uploaded_at=excluded.uploaded_at""",
                    (case_id, kind, filename, storage_name, mime, len(data), now),
                )
                db.execute("UPDATE cases SET status = 'under_review', updated_at = ? WHERE id = ?", (now, case_id))
                self.audit(db, case_id, "document_uploaded", f"{KINDS[kind]} uploaded: {filename}. Verification is required.")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if old_path:
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                logging.warning("An obsolete upload could not be removed.")
        return self.case_detail(case_id)

    def verify(self, case_id: int, kind: str, body: dict) -> dict:
        if kind not in KINDS or not isinstance(body.get("verified"), bool):
            raise APIError("Select a valid document and verification state.")
        verified = body["verified"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self.require_case(db, case_id)
            self.require_open(record)
            doc = db.execute("SELECT id, verified FROM documents WHERE case_id = ? AND kind = ?", (case_id, kind)).fetchone()
            if not doc:
                raise APIError("Upload the document before verifying it.", 409)
            if bool(doc["verified"]) != verified:
                db.execute("UPDATE documents SET verified = ? WHERE id = ?", (int(verified), doc["id"]))
                db.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (timestamp(), case_id))
                self.audit(db, case_id, "document_verified" if verified else "verification_removed",
                           f"{KINDS[kind]} {'marked as checked' if verified else 'returned to unchecked'}.")
        return self.case_detail(case_id)

    def review(self, case_id: int, body: dict) -> dict:
        status = option_field(body, "status", STATUSES)
        note = text_field(body, "note", 2000, status in {"needs_information", "rejected", "under_review"})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self.require_case(db, case_id)
            if status == record["status"]:
                raise APIError("This case already has that status.", 409)
            if record["status"] in FINAL_STATUSES and status != "under_review":
                raise APIError("Reopen this case before making another decision.", 409)
            if status == "approved":
                counts = db.execute("SELECT COUNT(*) AS total, COALESCE(SUM(verified), 0) AS checked FROM documents WHERE case_id = ?", (case_id,)).fetchone()
                if counts["total"] != 2 or counts["checked"] != 2:
                    raise APIError("Upload and verify both required documents before approving.", 409)
            db.execute("UPDATE cases SET status = ?, updated_at = ? WHERE id = ?", (status, timestamp(), case_id))
            labels = {"approved": "Case approved.", "rejected": "Case rejected.", "needs_information": "More information requested.", "under_review": "Case returned to the review queue."}
            self.audit(db, case_id, status, labels[status] + (f" {note}" if note else ""))
        return self.case_detail(case_id)

    def add_note(self, case_id: int, body: dict) -> dict:
        note = text_field(body, "note", 2000, True)
        with self.connect() as db:
            self.require_case(db, case_id)
            self.audit(db, case_id, "note_added", note)
            db.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (timestamp(), case_id))
        return self.case_detail(case_id)

    def download(self, document_id: int) -> tuple[bytes, str, str]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
            if not row:
                raise APIError("Document not found.", 404)
            path = (self.upload_directory / row["storage_name"]).resolve()
            if path.parent != self.upload_directory or not path.is_file():
                raise APIError("The document file is unavailable.", 404)
            return path.read_bytes(), row["mime_type"], row["original_name"]


class Handler(BaseHTTPRequestHandler):
    server_version = "KYCWorkspace/1.0"

    @property
    def workspace(self) -> Workspace:
        return self.server.workspace

    def log_message(self, fmt: str, *args) -> None:
        # Log response codes, without customer fields or document contents.
        logging.info("%s %s", self.command, str(args[1]) if len(args) > 1 else "request")

    def respond(self, status: int, data: bytes, content_type: str = "application/json; charset=utf-8", extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload, status: int = 200) -> None:
        self.respond(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def check_host(self) -> None:
        # Host validation also prevents DNS rebinding to the loopback service.
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise APIError("This workspace is available only through its local address.", 403)

    def read_json(self) -> dict:
        expected_origins = {f"http://127.0.0.1:{self.server.server_address[1]}", f"http://localhost:{self.server.server_address[1]}"}
        if self.headers.get("Origin") and self.headers["Origin"] not in expected_origins:
            raise APIError("Requests must come from this local workspace.", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise APIError("Cross-site changes are not allowed.", 403)
        if self.headers.get_content_type() != "application/json":
            raise APIError("Send JSON data.", 415)
        if self.headers.get("Transfer-Encoding"):
            raise APIError("Streaming request bodies are not supported.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError("Invalid request length.") from None
        if length <= 0:
            raise APIError("A JSON request body is required.")
        if length > MAX_BODY_SIZE:
            raise APIError("The request is too large. Documents must be no larger than 5 MB.", 413)
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise APIError("The request contains invalid JSON.") from None
        if not isinstance(body, dict):
            raise APIError("Send a JSON object.")
        return body

    def dispatch(self, mutation: bool = False) -> None:
        try:
            self.connection.settimeout(20)
            self.check_host()
            path = urlsplit(self.path).path
            if not mutation:
                if path in STATIC_FILES:
                    filename, content_type = STATIC_FILES[path]
                    self.respond(200, (ROOT / "static" / filename).read_bytes(), content_type)
                elif path == "/api/health":
                    self.send_json({"status": "ok", "mode": "local_demo", "version": "1.0.0"})
                elif path == "/api/cases":
                    self.send_json({"cases": self.workspace.list_cases()})
                elif path == "/api/activity":
                    self.send_json({"activity": self.workspace.recent_activity()})
                elif match := re.fullmatch(r"/api/cases/(\d+)", path):
                    self.send_json(self.workspace.case_detail(int(match[1])))
                elif match := re.fullmatch(r"/api/documents/(\d+)/file", path):
                    data, mime, filename = self.workspace.download(int(match[1]))
                    self.respond(200, data, mime, {"Content-Disposition": "attachment; filename=\"document" + Path(filename).suffix.lower() + "\"; filename*=UTF-8''" + quote(filename, safe="")})
                else:
                    raise APIError("Page not found.", 404)
            else:
                body = self.read_json()
                if path == "/api/cases":
                    self.send_json(self.workspace.create_case(body), 201)
                elif match := re.fullmatch(r"/api/cases/(\d+)/documents/(identity|address)(/verify)?", path):
                    case_id, kind = int(match[1]), match[2]
                    result = self.workspace.verify(case_id, kind, body) if match[3] else self.workspace.upload(case_id, kind, body)
                    self.send_json(result)
                elif match := re.fullmatch(r"/api/cases/(\d+)/(review|notes)", path):
                    result = self.workspace.review(int(match[1]), body) if match[2] == "review" else self.workspace.add_note(int(match[1]), body)
                    self.send_json(result)
                else:
                    raise APIError("Action not found.", 404)
        except APIError as error:
            self.send_json({"error": str(error)}, error.status)
        except sqlite3.IntegrityError:
            self.send_json({"error": "A customer with this email already exists."}, 409)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            logging.warning("Client disconnected or request timed out.")
        except Exception:
            logging.exception("Workspace request failed")
            self.send_json({"error": "The workspace could not complete this request. Try again."}, 500)

    def do_GET(self) -> None:
        self.dispatch()

    def do_POST(self) -> None:
        self.dispatch(mutation=True)


class WorkspaceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], workspace: Workspace):
        self.workspace = workspace
        super().__init__(address, Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local KYC review workspace.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--no-demo", action="store_true", help="Start a new database without sample records.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Choose a port between 1 and 65535.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    os.umask(0o077)
    workspace = Workspace(args.data_dir, seed=not args.no_demo)
    try:
        server = WorkspaceServer(("127.0.0.1", args.port), workspace)
    except OSError as error:
        parser.exit(1, f"Could not start on port {args.port}: {error}. Try --port 8001.\n")
    print(f"\nKYC Workspace running at http://127.0.0.1:{args.port}\nLocal prototype · stop with Ctrl+C\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWorkspace stopped. Your records are saved.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
