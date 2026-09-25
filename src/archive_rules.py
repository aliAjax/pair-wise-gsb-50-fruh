"""档案借阅规则：盒容量、卷宗状态转换、逾期判定与角色权限。"""
from datetime import date
from typing import Any, Dict

from .domain import Conflict, ValidationError


BOX_ROLES = {'archivist'}
ARCHIVE_ROLES = {'archivist'}
BORROW_ROLES = {'reviewer'}
RETURN_ROLES = {'reviewer', 'archivist'}
REPAIR_ROLES = {'archivist'}
VIEW_ROLES = {'inspector', 'reviewer', 'taxpayer_rep', 'archivist'}

ACTION_ROLES = {'box': BOX_ROLES, 'archive': ARCHIVE_ROLES, 'borrow': BORROW_ROLES, 'return': RETURN_ROLES, 'repair': REPAIR_ROLES}


class ArchiveRules:
    def known_role(self, role: str) -> bool:
        return role == "admin" or role in VIEW_ROLES

    def role_can(self, role: str, action: str) -> bool:
        return role == "admin" or role in ACTION_ROLES.get(action, set())

    def ensure_case_closed(self, record: Dict[str, Any]) -> None:
        if record["state"] != "closed":
            raise Conflict("案件未结案，不能装盒归档")

    def ensure_capacity(self, box: Dict[str, Any], used_pages: int, page_count: int) -> None:
        if used_pages + page_count > int(box["capacity_pages"]):
            raise Conflict("档案盒%s容量不足：已用%s页，容量%s页" % (box["box_no"], used_pages, box["capacity_pages"]))

    def ensure_borrowable(self, dossier: Dict[str, Any]) -> None:
        if dossier["status"] == "repair":
            raise Conflict("卷宗修补中，补齐后才能重新借阅")
        if dossier["status"] != "available":
            raise Conflict("卷宗已借出，归还前不能再借")

    def ensure_missing_within_pages(self, dossier: Dict[str, Any], missing_pages: int) -> None:
        if missing_pages > int(dossier["page_count"]):
            raise ValidationError("缺页数不能超过卷宗页数")

    def is_overdue(self, loan: Dict[str, Any], today: date) -> bool:
        return loan.get("returned_at") is None and str(loan["due_date"]) < today.isoformat()

    def decorate_loan(self, loan: Dict[str, Any], today: date) -> Dict[str, Any]:
        item = dict(loan)
        item["overdue"] = self.is_overdue(loan, today)
        return item

    def decorate_dossier(self, dossier: Dict[str, Any], today: date) -> Dict[str, Any]:
        item = dict(dossier)
        loan = item.get("active_loan")
        item["overdue"] = bool(loan) and self.is_overdue(loan, today)
        return item
