"""归档资料：档案盒、卷宗、借阅和修补待办的数据结构与输入校验。"""
from datetime import date
from typing import Any, Dict

from .domain import ValidationError, boolean, integer, optional_text, text


# 卷宗状态：在架、借出、修补中
DOSSIER_SHELVED = "shelved"
DOSSIER_ON_LOAN = "on_loan"
DOSSIER_IN_REPAIR = "in_repair"

# 借阅状态
LOAN_ACTIVE = "active"
LOAN_RETURNED = "returned"

# 修补待办状态
REPAIR_PENDING = "pending"
REPAIR_DONE = "done"


def parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ValidationError("%s必须是YYYY-MM-DD日期" % field)
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValidationError("%s必须是YYYY-MM-DD日期" % field) from exc


def box_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    data = data or {}
    return {
        "box_code": text(data, "box_code"),
        "shelf_location": text(data, "shelf_location"),
        "capacity_pages": integer(data, "capacity_pages", 1),
    }


def pack_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    data = data or {}
    return {
        "box_code": text(data, "box_code"),
        "title": text(data, "title"),
        "pages": integer(data, "pages", 1),
    }


def borrow_payload(data: Dict[str, Any], today: date) -> Dict[str, Any]:
    data = data or {}
    purpose = text(data, "purpose")
    due_date = parse_date(data.get("due_date"), "due_date")
    if due_date < today:
        raise ValidationError("归还日不能早于今天")
    return {"purpose": purpose, "due_date": due_date}


def return_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    data = data or {}
    return {
        "missing_pages": boolean(data, "missing_pages", False),
        "note": optional_text(data, "note"),
    }


def repair_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"note": optional_text(data or {}, "note")}
