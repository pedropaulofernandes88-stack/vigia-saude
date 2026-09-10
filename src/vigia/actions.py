"""Registro local de ações de gestão, com transações, idempotência e histórico."""
from __future__ import annotations

from datetime import date
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from vigia.sources import utc_now


class ActionStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, municipality_code TEXT NOT NULL, municipality_name TEXT NOT NULL,
                    title TEXT NOT NULL, owner TEXT NOT NULL, due_date TEXT NOT NULL, description TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('aberta', 'concluida')),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    request_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS action_events (
                    id INTEGER PRIMARY KEY, action_id TEXT NOT NULL REFERENCES actions(id),
                    event TEXT NOT NULL, occurred_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def public(row) -> dict:
        return {key: row[key] for key in row.keys() if key not in {"request_key", "request_hash"}}

    def list(self) -> list[dict]:
        with self.connect() as db:
            return [self.public(row) for row in db.execute("SELECT * FROM actions ORDER BY created_at DESC, id")]

    def create(self, payload: dict, municipalities: list[dict], request_key: str) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Envie um objeto JSON.")
        names = {str(item["code"]): item["name"] for item in municipalities}
        code = payload.get("municipality_code")
        if not isinstance(code, str) or code not in names:
            raise ValueError("Selecione um município do território.")
        validated = {"municipality_code": code}
        for key, low, high in [("title", 5, 160), ("owner", 2, 80), ("description", 0, 2000), ("due_date", 10, 10)]:
            value = payload.get(key, "")
            if not isinstance(value, str) or not low <= len(value.strip()) <= high:
                raise ValueError(f"Campo {key}: informe entre {low} e {high} caracteres.")
            validated[key] = value.strip()
        date.fromisoformat(validated["due_date"])
        if not isinstance(request_key, str) or not 16 <= len(request_key) <= 100:
            raise ValueError("Chave de idempotência ausente ou inválida.")
        digest = hashlib.sha256(json.dumps(validated, sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM actions WHERE request_key = ?", (request_key,)).fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise ValueError("Esta chave já foi usada com outro conteúdo. Atualize o formulário.")
                return self.public(existing)
            identifier, now = str(uuid.uuid4()), utc_now()
            db.execute("INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (identifier, code, names[code], validated["title"], validated["owner"], validated["due_date"],
                        validated["description"], "aberta", now, now, request_key, digest))
            db.execute("INSERT INTO action_events(action_id,event,occurred_at) VALUES(?,?,?)", (identifier, "criada", now))
            return self.public(db.execute("SELECT * FROM actions WHERE id = ?", (identifier,)).fetchone())

    def update(self, identifier: str, payload: dict) -> dict:
        if not isinstance(payload, dict) or payload.get("status") not in {"aberta", "concluida"}:
            raise ValueError("Situação inválida.")
        try:
            uuid.UUID(identifier)
        except ValueError as exc:
            raise ValueError("Ação inválida.") from exc
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM actions WHERE id = ?", (identifier,)).fetchone()
            if not existing:
                raise KeyError("Ação não encontrada.")
            if existing["status"] != payload["status"]:
                now = utc_now()
                db.execute("UPDATE actions SET status = ?, updated_at = ? WHERE id = ?", (payload["status"], now, identifier))
                db.execute("INSERT INTO action_events(action_id,event,occurred_at) VALUES(?,?,?)", (identifier, payload["status"], now))
            return self.public(db.execute("SELECT * FROM actions WHERE id = ?", (identifier,)).fetchone())
