"""归档资料保存：档案盒、卷宗、借阅与修补的SQLite表和事务。"""
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .domain import Conflict, NotFound


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


DOSSIER_SQL = """
    SELECT d.*, b.box_no, b.shelf_location,
           l.id AS loan_id, l.borrower_id, l.purpose, l.due_date, l.borrowed_at
    FROM archive_dossiers d
    JOIN archive_boxes b ON b.id = d.box_id
    LEFT JOIN archive_loans l ON l.dossier_id = d.id AND l.returned_at IS NULL
"""


class ArchiveRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS archive_boxes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    box_no TEXT NOT NULL UNIQUE,
                    shelf_location TEXT NOT NULL,
                    capacity_pages INTEGER NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS archive_dossiers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id INTEGER NOT NULL UNIQUE REFERENCES records(id) ON DELETE CASCADE,
                    box_id INTEGER NOT NULL REFERENCES archive_boxes(id),
                    page_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'available',
                    archived_by TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS archive_loans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dossier_id INTEGER NOT NULL REFERENCES archive_dossiers(id) ON DELETE CASCADE,
                    borrower_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    borrowed_at TEXT NOT NULL,
                    returned_at TEXT,
                    missing_pages INTEGER
                );
                CREATE TABLE IF NOT EXISTS archive_repairs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dossier_id INTEGER NOT NULL REFERENCES archive_dossiers(id) ON DELETE CASCADE,
                    loan_id INTEGER NOT NULL REFERENCES archive_loans(id) ON DELETE CASCADE,
                    missing_pages INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    resolved_by TEXT,
                    resolved_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_loans_active ON archive_loans(dossier_id) WHERE returned_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_dossiers_box ON archive_dossiers(box_id);
                CREATE INDEX IF NOT EXISTS idx_dossiers_status ON archive_dossiers(status);
                CREATE INDEX IF NOT EXISTS idx_repairs_dossier ON archive_repairs(dossier_id, status);
                """
            )

    @staticmethod
    def _dossier_row(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        loan = None
        if item.get("loan_id") is not None:
            loan = {
                "id": item["loan_id"],
                "borrower_id": item["borrower_id"],
                "purpose": item["purpose"],
                "due_date": item["due_date"],
                "borrowed_at": item["borrowed_at"],
                "returned_at": None,
            }
        for key in ("loan_id", "borrower_id", "purpose", "due_date", "borrowed_at"):
            item.pop(key, None)
        item["active_loan"] = loan
        return item

    def create_box(self, box_no: str, shelf_location: str, capacity_pages: int, actor_id: str) -> Dict[str, Any]:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO archive_boxes(box_no,shelf_location,capacity_pages,created_by,created_at) VALUES(?,?,?,?,?)",
                    (box_no, shelf_location, capacity_pages, actor_id, _now()),
                )
                row = connection.execute("SELECT * FROM archive_boxes WHERE id=?", (int(cursor.lastrowid),)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise Conflict("盒号已存在") from exc
        return dict(row)

    def get_box(self, box_id: int) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM archive_boxes WHERE id=?", (box_id,)).fetchone()
        if row is None:
            raise NotFound("档案盒不存在")
        return dict(row)

    def box_used_pages(self, box_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COALESCE(SUM(page_count),0) AS used FROM archive_dossiers WHERE box_id=?", (box_id,)).fetchone()
        return int(row["used"])

    def list_boxes(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT b.*, COALESCE(SUM(d.page_count),0) AS used_pages, COUNT(d.id) AS dossier_count
                FROM archive_boxes b LEFT JOIN archive_dossiers d ON d.box_id = b.id
                GROUP BY b.id ORDER BY b.id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_dossier(self, record_id: int, box_id: int, page_count: int, actor_id: str, ensure_capacity: Callable[[Dict[str, Any], int, int], None]) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                box = connection.execute("SELECT * FROM archive_boxes WHERE id=?", (box_id,)).fetchone()
                if box is None:
                    raise NotFound("档案盒不存在")
                used = connection.execute("SELECT COALESCE(SUM(page_count),0) AS used FROM archive_dossiers WHERE box_id=?", (box_id,)).fetchone()
                ensure_capacity(dict(box), int(used["used"]), page_count)
                try:
                    cursor = connection.execute(
                        "INSERT INTO archive_dossiers(record_id,box_id,page_count,status,archived_by,archived_at) VALUES(?,?,?,'available',?,?)",
                        (record_id, box_id, page_count, actor_id, now),
                    )
                except sqlite3.IntegrityError as exc:
                    raise Conflict("案件已归档，不能重复装盒") from exc
                row = connection.execute("SELECT * FROM archive_dossiers WHERE id=?", (int(cursor.lastrowid),)).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return dict(row)

    def get_dossier(self, dossier_id: int) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(DOSSIER_SQL + " WHERE d.id=?", (dossier_id,)).fetchone()
        if row is None:
            raise NotFound("卷宗不存在")
        return self._dossier_row(row)

    def list_dossiers(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            if status:
                rows = connection.execute(DOSSIER_SQL + " WHERE d.status=? ORDER BY d.id DESC", (status,)).fetchall()
            else:
                rows = connection.execute(DOSSIER_SQL + " ORDER BY d.id DESC").fetchall()
        return [self._dossier_row(row) for row in rows]

    def create_loan(self, dossier_id: int, borrower_id: str, purpose: str, due_date: str) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                dossier = connection.execute("SELECT id FROM archive_dossiers WHERE id=?", (dossier_id,)).fetchone()
                if dossier is None:
                    raise NotFound("卷宗不存在")
                cursor = connection.execute("UPDATE archive_dossiers SET status='borrowed' WHERE id=? AND status='available'", (dossier_id,))
                if cursor.rowcount == 0:
                    raise Conflict("卷宗已借出或修补中，暂不能借阅")
                try:
                    cursor = connection.execute(
                        "INSERT INTO archive_loans(dossier_id,borrower_id,purpose,due_date,borrowed_at) VALUES(?,?,?,?,?)",
                        (dossier_id, borrower_id, purpose, due_date, now),
                    )
                except sqlite3.IntegrityError as exc:
                    raise Conflict("卷宗已有未归还的借阅") from exc
                row = connection.execute("SELECT * FROM archive_loans WHERE id=?", (int(cursor.lastrowid),)).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return dict(row)

    def return_loan(self, dossier_id: int, missing_pages: int) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                loan = connection.execute("SELECT * FROM archive_loans WHERE dossier_id=? AND returned_at IS NULL", (dossier_id,)).fetchone()
                if loan is None:
                    raise Conflict("卷宗未在借出状态")
                connection.execute("UPDATE archive_loans SET returned_at=?, missing_pages=? WHERE id=?", (now, missing_pages, int(loan["id"])))
                status = "repair" if missing_pages > 0 else "available"
                connection.execute("UPDATE archive_dossiers SET status=? WHERE id=?", (status, dossier_id))
                repair_id = None
                if missing_pages > 0:
                    cursor = connection.execute(
                        "INSERT INTO archive_repairs(dossier_id,loan_id,missing_pages,status,created_at) VALUES(?,?,?,'pending',?)",
                        (dossier_id, int(loan["id"]), missing_pages, now),
                    )
                    repair_id = int(cursor.lastrowid)
                row = connection.execute("SELECT * FROM archive_loans WHERE id=?", (int(loan["id"]),)).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        result = dict(row)
        result["repair_id"] = repair_id
        result["dossier_status"] = status
        return result

    def list_loans(self, active_only: bool = False) -> List[Dict[str, Any]]:
        sql = """
            SELECT l.*, d.record_id, d.page_count, d.status AS dossier_status, b.box_no, b.shelf_location
            FROM archive_loans l
            JOIN archive_dossiers d ON d.id = l.dossier_id
            JOIN archive_boxes b ON b.id = d.box_id
        """
        if active_only:
            sql += " WHERE l.returned_at IS NULL"
        sql += " ORDER BY l.id DESC"
        with self._connect() as connection:
            rows = connection.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def list_repairs(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = """
            SELECT r.*, d.record_id, d.page_count, b.box_no, b.shelf_location
            FROM archive_repairs r
            JOIN archive_dossiers d ON d.id = r.dossier_id
            JOIN archive_boxes b ON b.id = d.box_id
        """
        params: tuple = ()
        if status:
            sql += " WHERE r.status=?"
            params = (status,)
        sql += " ORDER BY r.id DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def complete_repair(self, repair_id: int, actor_id: str, note: str) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                repair = connection.execute("SELECT * FROM archive_repairs WHERE id=?", (repair_id,)).fetchone()
                if repair is None:
                    raise NotFound("修补待办不存在")
                cursor = connection.execute(
                    "UPDATE archive_repairs SET status='done', note=?, resolved_by=?, resolved_at=? WHERE id=? AND status='pending'",
                    (note, actor_id, now, repair_id),
                )
                if cursor.rowcount == 0:
                    raise Conflict("修补待办已处理")
                pending = connection.execute("SELECT COUNT(*) AS total FROM archive_repairs WHERE dossier_id=? AND status='pending'", (repair["dossier_id"],)).fetchone()
                if int(pending["total"]) == 0:
                    connection.execute("UPDATE archive_dossiers SET status='available' WHERE id=? AND status='repair'", (repair["dossier_id"],))
                row = connection.execute("SELECT * FROM archive_repairs WHERE id=?", (repair_id,)).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return dict(row)
