"""归档用例编排：结案校验、装盒、借阅、归还、修补与审计集成。"""
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from .archive_domain import validate_box, validate_borrow, validate_dossier, validate_repair_note, validate_return
from .archive_repository import ArchiveRepository
from .archive_rules import ArchiveRules
from .audit import AuditRecorder
from .domain import Actor, PermissionDenied
from .repository import Repository


class ArchiveService:
    def __init__(self, records: Repository, archive: ArchiveRepository, rules: ArchiveRules, audit: AuditRecorder = None, clock: Callable[[], date] = None) -> None:
        self.records = records
        self.archive = archive
        self.rules = rules
        self.audit = audit
        self.clock = clock or date.today

    @staticmethod
    def _actor(actor: Actor) -> Actor:
        if actor is None or not actor.user_id.strip() or not actor.role.strip():
            raise PermissionDenied("缺少调用身份")
        return actor

    def _ready(self, actor: Actor, action: str = None) -> Actor:
        actor = self._actor(actor)
        if not self.rules.known_role(actor.role):
            raise PermissionDenied("角色无权访问该服务")
        if action is not None and not self.rules.role_can(actor.role, action):
            raise PermissionDenied("角色无权执行该操作")
        return actor

    def _note(self, record_id: int, actor_id: str, action: str, details: Dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit.note(record_id, actor_id, action, details)

    def create_box(self, actor: Actor, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._ready(actor, "box")
        box = validate_box(data or {})
        return self.archive.create_box(box["box_no"], box["shelf_location"], box["capacity_pages"], actor.user_id)

    def list_boxes(self, actor: Actor) -> List[Dict[str, Any]]:
        self._ready(actor)
        return self.archive.list_boxes()

    def archive_record(self, actor: Actor, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._ready(actor, "archive")
        payload = validate_dossier(data or {})
        record = self.records.get(payload["record_id"])
        self.rules.ensure_case_closed(record)
        box = self.archive.get_box(payload["box_id"])
        self.rules.ensure_capacity(box, self.archive.box_used_pages(box["id"]), payload["page_count"])
        dossier = self.archive.create_dossier(record["id"], box["id"], payload["page_count"], actor.user_id, self.rules.ensure_capacity)
        self._note(record["id"], actor.user_id, "archive_boxed", {"dossier_id": dossier["id"], "box_no": box["box_no"], "shelf_location": box["shelf_location"], "page_count": payload["page_count"]})
        return self.rules.decorate_dossier(self.archive.get_dossier(dossier["id"]), self.clock())

    def get_dossier(self, actor: Actor, dossier_id: int) -> Dict[str, Any]:
        self._ready(actor)
        return self.rules.decorate_dossier(self.archive.get_dossier(dossier_id), self.clock())

    def list_dossiers(self, actor: Actor, status: Optional[str] = None, overdue_only: bool = False) -> List[Dict[str, Any]]:
        self._ready(actor)
        today = self.clock()
        items = [self.rules.decorate_dossier(item, today) for item in self.archive.list_dossiers(status=status)]
        if overdue_only:
            items = [item for item in items if item["overdue"]]
        return items

    def borrow(self, actor: Actor, dossier_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._ready(actor, "borrow")
        payload = validate_borrow(data or {}, self.clock())
        dossier = self.archive.get_dossier(dossier_id)
        self.rules.ensure_borrowable(dossier)
        loan = self.archive.create_loan(dossier_id, actor.user_id, payload["purpose"], payload["due_date"])
        self._note(dossier["record_id"], actor.user_id, "archive_borrowed", {"dossier_id": dossier_id, "purpose": payload["purpose"], "due_date": payload["due_date"]})
        return self.rules.decorate_loan(loan, self.clock())

    def return_dossier(self, actor: Actor, dossier_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._ready(actor, "return")
        payload = validate_return(data or {})
        dossier = self.archive.get_dossier(dossier_id)
        self.rules.ensure_missing_within_pages(dossier, payload["missing_pages"])
        result = self.archive.return_loan(dossier_id, payload["missing_pages"])
        details = {"dossier_id": dossier_id, "missing_pages": payload["missing_pages"], "repair_id": result["repair_id"]}
        self._note(dossier["record_id"], actor.user_id, "archive_returned", details)
        if result["repair_id"] is not None:
            self._note(dossier["record_id"], actor.user_id, "archive_repair_requested", details)
        return result

    def list_loans(self, actor: Actor, active_only: bool = False, overdue_only: bool = False) -> List[Dict[str, Any]]:
        self._ready(actor)
        today = self.clock()
        items = [self.rules.decorate_loan(item, today) for item in self.archive.list_loans(active_only=active_only)]
        if overdue_only:
            items = [item for item in items if item["overdue"]]
        return items

    def list_repairs(self, actor: Actor, status: Optional[str] = None) -> List[Dict[str, Any]]:
        self._ready(actor)
        return self.archive.list_repairs(status=status)

    def complete_repair(self, actor: Actor, repair_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._ready(actor, "repair")
        payload = validate_repair_note(data or {})
        repair = self.archive.complete_repair(repair_id, actor.user_id, payload["note"])
        dossier = self.archive.get_dossier(repair["dossier_id"])
        self._note(dossier["record_id"], actor.user_id, "archive_repair_done", {"dossier_id": repair["dossier_id"], "repair_id": repair_id, "note": payload["note"]})
        result = dict(repair)
        result["dossier_status"] = dossier["status"]
        return result
