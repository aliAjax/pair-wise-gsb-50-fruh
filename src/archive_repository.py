"""归档保存：档案盒、卷宗、借阅和修补待办的SQLite表与事务。"""
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from .archive_domain import (
    DOSSIER_IN_REPAIR,
    DOSSIER_ON_LOAN,
    DOSSIER_SHELVED,
    LOAN_ACTIVE,
    LOAN_RETURNED,
    REPAIR_DONE,
    REPAIR_PENDING,
)
from .domain import Conflict, NotFound


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                    box_code TEXT NOT NULL UNIQUE,
                    shelf_location TEXT NOT NULL,
                    capacity_pages INTEGER NOT NULL,
                    used_pages INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dossiers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id INTEGER NOT NULL UNIQUE,
                    box_id INTEGER NOT NULL REFERENCES archive_boxes(id),
                    title TEXT NOT NULL,
                    pages INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'shelved',
                    archived_by TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS loans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
                    borrower_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    borrowed_on TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    returned_on TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    return_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS repair_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
                    loan_id INTEGER REFERENCES loans(id),
                    status TEXT NOT NULL DEFAULT 'pending',
                    note TEXT NOT NULL DEFAULT '',
                    repair_note TEXT NOT NULL DEFAULT '',
                    opened_by TEXT NOT NULL,
                    closed_by TEXT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_dossiers_box ON dossiers(box_id);
                CREATE INDEX IF NOT EXISTS idx_dossiers_status ON dossiers(status);
                CREATE INDEX IF NOT EXISTS idx_loans_dossier ON loans(dossier_id, id);
                CREATE INDEX IF NOT EXISTS idx_loans_status ON loans(status);
                CREATE INDEX IF NOT EXISTS idx_repair_status ON repair_tasks(status);
                """
            )

    @staticmethod
    def _box(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["remaining_pages"] = int(item["capacity_pages"]) - int(item["used_pages"])
        return item

    @staticmethod
    def _dossier(row: sqlite3.Row) -> Dict[str, Any]:
        return dict(row)

    @staticmethod
    def _loan(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["borrowed_on"] = date.fromisoformat(item["borrowed_on"])
        item["due_date"] = date.fromisoformat(item["due_date"])
        item["returned_on"] = date.fromisoformat(item["returned_on"]) if item.get("returned_on") else None
        item["overdue"] = False
        return item

    @staticmethod
    def _repair(row: sqlite3.Row) -> Dict[str, Any]:
        return dict(row)

    # -- 档案盒 ----------------------------------------------------------

    def create_box(self, box_code: str, shelf_location: str, capacity_pages: int, actor_id: str) -> Dict[str, Any]:
        now = _now()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO archive_boxes(box_code,shelf_location,capacity_pages,used_pages,created_by,created_at) VALUES(?,?,?,0,?,?)",
                    (box_code, shelf_location, capacity_pages, actor_id, now),
                )
                row = connection.execute("SELECT * FROM archive_boxes WHERE id=?", (cursor.lastrowid,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise Conflict("盒号已存在") from exc
        return self._box(row)

    def get_box(self, box_code: str) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM archive_boxes WHERE box_code=?", (box_code,)).fetchone()
        if row is None:
            raise NotFound("档案盒不存在")
        return self._box(row)

    def list_boxes(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM archive_boxes ORDER BY id DESC").fetchall()
        return [self._box(row) for row in rows]

    # -- 卷宗装盒 --------------------------------------------------------

    def get_dossier_by_record(self, record_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM dossiers WHERE record_id=?", (record_id,)).fetchone()
        return self._dossier(row) if row is not None else None

    def get_dossier(self, dossier_id: int) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT d.*, b.box_code, b.shelf_location FROM dossiers d JOIN archive_boxes b ON b.id=d.box_id WHERE d.id=?",
                (dossier_id,),
            ).fetchone()
        if row is None:
            raise NotFound("卷宗不存在")
        return dict(row)

    def list_dossiers(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            if status:
                rows = connection.execute(
                    "SELECT d.*, b.box_code, b.shelf_location FROM dossiers d JOIN archive_boxes b ON b.id=d.box_id WHERE d.status=? ORDER BY d.id DESC",
                    (status,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT d.*, b.box_code, b.shelf_location FROM dossiers d JOIN archive_boxes b ON b.id=d.box_id ORDER BY d.id DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def pack_dossier(
        self,
        record_id: int,
        box: Dict[str, Any],
        title: str,
        pages: int,
        actor_id: str,
    ) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            box_row = connection.execute("SELECT * FROM archive_boxes WHERE id=?", (box["id"],)).fetchone()
            if box_row is None:
                connection.rollback()
                raise NotFound("档案盒不存在")
            duplicate = connection.execute("SELECT id FROM dossiers WHERE record_id=?", (record_id,)).fetchone()
            if duplicate is not None:
                connection.rollback()
                raise Conflict("该案件已装入档案盒，一案只能对应一个唯一档案盒")
            used = int(box_row["used_pages"])
            if used + pages > int(box_row["capacity_pages"]):
                connection.rollback()
                raise Conflict(
                    "盒内卷宗页数超过容量：已用%s页，本次%s页，容量%s页"
                    % (used, pages, box_row["capacity_pages"])
                )
            cursor = connection.execute(
                "INSERT INTO dossiers(record_id,box_id,title,pages,status,archived_by,archived_at) VALUES(?,?,?,?,'shelved',?,?)",
                (record_id, box_row["id"], title, pages, actor_id, now),
            )
            connection.execute(
                "UPDATE archive_boxes SET used_pages=? WHERE id=?", (used + pages, box_row["id"])
            )
            dossier = connection.execute(
                "SELECT d.*, b.box_code, b.shelf_location FROM dossiers d JOIN archive_boxes b ON b.id=d.box_id WHERE d.id=?",
                (cursor.lastrowid,),
            ).fetchone()
            connection.commit()
        return dict(dossier)

    # -- 借阅 ------------------------------------------------------------

    def get_active_loan(self, dossier_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM loans WHERE dossier_id=? AND status='active' ORDER BY id DESC",
                (dossier_id,),
            ).fetchone()
        return self._loan(row) if row is not None else None

    def add_loan(
        self,
        dossier_id: int,
        borrower_id: str,
        purpose: str,
        borrowed_on: date,
        due_date: date,
    ) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            dossier = connection.execute("SELECT * FROM dossiers WHERE id=?", (dossier_id,)).fetchone()
            if dossier is None:
                connection.rollback()
                raise NotFound("卷宗不存在")
            if dossier["status"] != DOSSIER_SHELVED:
                connection.rollback()
                raise Conflict("卷宗当前不在架，不能借阅")
            active = connection.execute(
                "SELECT id FROM loans WHERE dossier_id=? AND status='active'", (dossier_id,)
            ).fetchone()
            if active is not None:
                connection.rollback()
                raise Conflict("该卷宗尚未归还，归还前不能再次借阅")
            cursor = connection.execute(
                "INSERT INTO loans(dossier_id,borrower_id,purpose,borrowed_on,due_date,status,created_at) VALUES(?,?,?,?,?,'active',?)",
                (dossier_id, borrower_id, purpose, borrowed_on.isoformat(), due_date.isoformat(), now),
            )
            connection.execute("UPDATE dossiers SET status=? WHERE id=?", (DOSSIER_ON_LOAN, dossier_id))
            row = connection.execute("SELECT * FROM loans WHERE id=?", (cursor.lastrowid,)).fetchone()
            connection.commit()
        return self._loan(row)

    def return_loan(
        self,
        dossier_id: int,
        returned_on: date,
        missing_pages: bool,
        note: str,
        actor_id: str,
    ) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            dossier = connection.execute("SELECT * FROM dossiers WHERE id=?", (dossier_id,)).fetchone()
            if dossier is None:
                connection.rollback()
                raise NotFound("卷宗不存在")
            loan_row = connection.execute(
                "SELECT * FROM loans WHERE dossier_id=? AND status='active' ORDER BY id DESC",
                (dossier_id,),
            ).fetchone()
            if loan_row is None:
                connection.rollback()
                raise Conflict("该卷宗没有未完成的借阅记录")
            connection.execute(
                "UPDATE loans SET status='returned',returned_on=?,return_note=? WHERE id=?",
                (returned_on.isoformat(), note, loan_row["id"]),
            )
            if missing_pages:
                new_status = DOSSIER_IN_REPAIR
                connection.execute(
                    "INSERT INTO repair_tasks(dossier_id,loan_id,status,note,opened_by,opened_at) VALUES(?,?,'pending',?,?,?)",
                    (dossier_id, loan_row["id"], note, actor_id, now),
                )
            else:
                new_status = DOSSIER_SHELVED
            connection.execute("UPDATE dossiers SET status=? WHERE id=?", (new_status, dossier_id))
            connection.commit()
        return self.get_dossier(dossier_id)

    def list_loans(self, active_only: bool = False) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            sql = (
                "SELECT l.*, d.title, d.record_id, b.box_code, b.shelf_location "
                "FROM loans l JOIN dossiers d ON d.id=l.dossier_id "
                "JOIN archive_boxes b ON b.id=d.box_id "
            )
            if active_only:
                sql += "WHERE l.status='active' "
            sql += "ORDER BY l.id DESC"
            rows = connection.execute(sql).fetchall()
        result = []
        for row in rows:
            item = self._loan(row)
            item["title"] = row["title"]
            item["record_id"] = row["record_id"]
            item["box_code"] = row["box_code"]
            item["shelf_location"] = row["shelf_location"]
            result.append(item)
        return result

    # -- 修补待办 --------------------------------------------------------

    def list_repair_tasks(self, pending_only: bool = False) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            sql = (
                "SELECT r.*, d.title, d.record_id, b.box_code "
                "FROM repair_tasks r JOIN dossiers d ON d.id=r.dossier_id "
                "JOIN archive_boxes b ON b.id=d.box_id "
            )
            if pending_only:
                sql += "WHERE r.status='pending' "
            sql += "ORDER BY r.id DESC"
            rows = connection.execute(sql).fetchall()
        return [self._repair(row) for row in rows]

    def complete_repair(self, dossier_id: int, repair_note: str, actor_id: str) -> Dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            dossier = connection.execute("SELECT * FROM dossiers WHERE id=?", (dossier_id,)).fetchone()
            if dossier is None:
                connection.rollback()
                raise NotFound("卷宗不存在")
            task_row = connection.execute(
                "SELECT * FROM repair_tasks WHERE dossier_id=? AND status='pending' ORDER BY id DESC",
                (dossier_id,),
            ).fetchone()
            if task_row is None:
                connection.rollback()
                raise Conflict("卷宗没有待处理的修补待办")
            connection.execute(
                "UPDATE repair_tasks SET status='done',repair_note=?,closed_by=?,closed_at=? WHERE id=?",
                (repair_note, actor_id, now, task_row["id"]),
            )
            connection.execute("UPDATE dossiers SET status=? WHERE id=?", (DOSSIER_SHELVED, dossier_id))
            connection.commit()
        return self.get_dossier(dossier_id)

    # -- 统计 ------------------------------------------------------------

    def archive_stats(self) -> Dict[str, int]:
        with self._connect() as connection:
            boxes = connection.execute("SELECT COUNT(*) AS total, COALESCE(SUM(used_pages),0) AS used, COALESCE(SUM(capacity_pages),0) AS capacity FROM archive_boxes").fetchone()
            dossiers = connection.execute("SELECT status, COUNT(*) AS total FROM dossiers GROUP BY status").fetchall()
            loans = connection.execute("SELECT COUNT(*) AS total FROM loans WHERE status='active'").fetchone()
            repairs = connection.execute("SELECT COUNT(*) AS total FROM repair_tasks WHERE status='pending'").fetchone()
        return {
            "boxes": int(boxes["total"]),
            "used_pages": int(boxes["used"]),
            "capacity_pages": int(boxes["capacity"]),
            "dossiers_total": sum(int(row["total"]) for row in dossiers),
            "dossiers_shelved": sum(int(row["total"]) for row in dossiers if row["status"] == DOSSIER_SHELVED),
            "dossiers_on_loan": sum(int(row["total"]) for row in dossiers if row["status"] == DOSSIER_ON_LOAN),
            "dossiers_in_repair": sum(int(row["total"]) for row in dossiers if row["status"] == DOSSIER_IN_REPAIR),
            "active_loans": int(loans["total"]),
            "pending_repairs": int(repairs["total"]),
        }
