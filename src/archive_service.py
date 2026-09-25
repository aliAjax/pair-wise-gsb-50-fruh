"""归档用例编排：装盒、借阅、归还、修补待办与只读查询。"""
from datetime import date
from typing import Any, Dict, List, Optional

from .archive_domain import (
    borrow_payload,
    box_payload,
    pack_payload,
    repair_payload,
    return_payload,
)
from .archive_rules import ArchiveRules
from .archive_repository import ArchiveRepository
from .audit import AuditRecorder
from .domain import Actor, PermissionDenied


READ_ROLES = {"inspector", "reviewer", "admin"}


class ArchiveService:
    def __init__(
        self,
        records_repository: Any,
        archive_repository: ArchiveRepository,
        rules: ArchiveRules = None,
        audit: AuditRecorder = None,
    ) -> None:
        self.records_repository = records_repository
        self.repository = archive_repository
        self.rules = rules or ArchiveRules()
        self.audit = audit or AuditRecorder(records_repository)

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: ArchiveService._jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [ArchiveService._jsonable(item) for item in value]
        return value

    def _actor(self, actor: Actor) -> Actor:
        if actor is None or not actor.user_id.strip() or not actor.role.strip():
            raise PermissionDenied("缺少调用身份")
        return actor

    def _require_read(self, actor: Actor) -> None:
        if actor.role not in READ_ROLES:
            raise PermissionDenied("角色无权查看归档资料")

    def _today(self, today: Optional[date]) -> date:
        return today or date.today()

    # -- 档案盒与卷宗 ----------------------------------------------------

    def create_box(self, actor: Actor, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self.rules.require_archive_role(actor.role)
        payload = box_payload(data)
        box = self.repository.create_box(
            payload["box_code"], payload["shelf_location"], payload["capacity_pages"], actor.user_id
        )
        return self._jsonable(box)

    def list_boxes(self, actor: Actor) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._require_read(actor)
        return self._jsonable(self.repository.list_boxes())

    def pack(self, actor: Actor, record_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self.rules.require_archive_role(actor.role)
        payload = pack_payload(data)
        record = self.records_repository.get(record_id)
        self.rules.require_closed(record)
        self.rules.ensure_case_not_packed(self.repository.get_dossier_by_record(record_id))
        box = self.repository.get_box(payload["box_code"])
        self.rules.ensure_box_fits(box, int(box["used_pages"]), payload["pages"])
        dossier = self.repository.pack_dossier(
            record_id=record_id,
            box=box,
            title=payload["title"],
            pages=payload["pages"],
            actor_id=actor.user_id,
        )
        self.audit.note(
            record_id,
            actor.user_id,
            "archived",
            {"box_code": payload["box_code"], "shelf_location": box["shelf_location"], "pages": payload["pages"], "title": payload["title"]},
        )
        return self._jsonable(dossier)

    def list_dossiers(self, actor: Actor, status: Optional[str] = None) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._require_read(actor)
        return self._jsonable(self.repository.list_dossiers(status=status))

    # -- 借阅与归还 ------------------------------------------------------

    def borrow(self, actor: Actor, dossier_id: int, data: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
        actor = self._actor(actor)
        self.rules.require_archive_role(actor.role)
        today = self._today(today)
        payload = borrow_payload(data, today)
        dossier = self.repository.get_dossier(dossier_id)
        self.rules.require_borrowable(dossier)
        loan = self.repository.add_loan(
            dossier_id=dossier_id,
            borrower_id=actor.user_id,
            purpose=payload["purpose"],
            borrowed_on=today,
            due_date=payload["due_date"],
        )
        return self._jsonable(self.rules.mark_loan_overdue(loan, today))

    def list_loans(self, actor: Actor, active_only: bool = False, today: Optional[date] = None) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._require_read(actor)
        today = self._today(today)
        loans = [self.rules.mark_loan_overdue(loan, today) for loan in self.repository.list_loans(active_only=active_only)]
        return self._jsonable(loans)

    def return_dossier(self, actor: Actor, dossier_id: int, data: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
        actor = self._actor(actor)
        self.rules.require_archive_role(actor.role)
        today = self._today(today)
        payload = return_payload(data)
        dossier = self.repository.get_dossier(dossier_id)
        self.rules.require_active_loan(self.repository.get_active_loan(dossier_id))
        result = self.repository.return_loan(
            dossier_id=dossier_id,
            returned_on=today,
            missing_pages=payload["missing_pages"],
            note=payload["note"],
            actor_id=actor.user_id,
        )
        result["transferred_to_repair"] = bool(payload["missing_pages"])
        return self._jsonable(result)

    # -- 修补待办 --------------------------------------------------------

    def list_repairs(self, actor: Actor, pending_only: bool = False) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._require_read(actor)
        return self._jsonable(self.repository.list_repair_tasks(pending_only=pending_only))

    def complete_repair(self, actor: Actor, dossier_id: int, data: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
        actor = self._actor(actor)
        self.rules.require_archive_role(actor.role)
        payload = repair_payload(data)
        dossier = self.repository.get_dossier(dossier_id)
        self.rules.require_in_repair(dossier)
        result = self.repository.complete_repair(dossier_id, payload["note"], actor.user_id)
        return self._jsonable(result)

    # -- 统计 ------------------------------------------------------------

    def stats(self, actor: Actor) -> Dict[str, int]:
        actor = self._actor(actor)
        self._require_read(actor)
        return self.repository.archive_stats()
