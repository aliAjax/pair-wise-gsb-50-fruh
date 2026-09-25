"""归档与借阅规则：装盒条件、盒容量、借阅冲突、逾期与缺页修补流转。"""
from datetime import date
from typing import Any, Dict

from .archive_domain import DOSSIER_IN_REPAIR, DOSSIER_ON_LOAN, DOSSIER_SHELVED
from .domain import Conflict, ValidationError


# 归档资料管理与复核借阅均由复核人员完成，admin可代办
ARCHIVE_ROLES = {"reviewer", "admin"}


class ArchiveRules:
    def role_can_archive(self, role: str) -> bool:
        return role in ARCHIVE_ROLES

    def require_archive_role(self, role: str) -> None:
        if not self.role_can_archive(role):
            from .domain import PermissionDenied

            raise PermissionDenied("角色无权操作归档资料")

    def require_closed(self, record: Dict[str, Any]) -> None:
        if record.get("state") != "closed":
            raise Conflict("案件结案后才能装入档案盒")

    def ensure_case_not_packed(self, dossier: Any) -> None:
        if dossier is not None:
            raise Conflict("该案件已装入档案盒，一案只能对应一个唯一档案盒")

    def ensure_box_fits(self, box: Dict[str, Any], used_pages: int, dossier_pages: int) -> None:
        if used_pages + dossier_pages > int(box["capacity_pages"]):
            raise Conflict(
                "盒内卷宗页数超过容量：已用%s页，本次%s页，容量%s页"
                % (used_pages, dossier_pages, box["capacity_pages"])
            )

    def require_borrowable(self, dossier: Dict[str, Any]) -> None:
        if dossier["status"] == DOSSIER_ON_LOAN:
            raise Conflict("该卷宗尚未归还，归还前不能再次借阅")
        if dossier["status"] == DOSSIER_IN_REPAIR:
            raise Conflict("该卷宗正在修补，补齐并完成修补待办后才能重新借阅")

    def require_active_loan(self, loan: Dict[str, Any]) -> None:
        if loan is None or loan["status"] != "active":
            raise Conflict("该卷宗没有未完成的借阅记录")

    def require_in_repair(self, dossier: Dict[str, Any]) -> None:
        if dossier["status"] != DOSSIER_IN_REPAIR:
            raise Conflict("卷宗不在修补中，无需登记补齐")

    def is_overdue(self, due_date: date, today: date) -> bool:
        return today > due_date

    def mark_loan_overdue(self, loan: Dict[str, Any], today: date) -> Dict[str, Any]:
        loan = dict(loan)
        loan["overdue"] = loan["status"] == "active" and self.is_overdue(loan["due_date"], today)
        return loan
