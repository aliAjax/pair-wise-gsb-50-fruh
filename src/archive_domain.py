"""归档资料数据类型与输入校验。"""
from datetime import date
from typing import Any, Dict

from .domain import ValidationError, integer, optional_text, text


DOSSIER_STATUSES = ("available", "borrowed", "repair")
REPAIR_STATUSES = ("pending", "done")
MAX_PAGES = 100000


def parse_day(data: Dict[str, Any], key: str) -> date:
    raw = text(data, key)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError("%s必须是YYYY-MM-DD格式的日期" % key) from exc


def optional_integer(data: Dict[str, Any], key: str, default: int = 0, minimum: int = None, maximum: int = None) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("%s必须是整数" % key)
    if minimum is not None and value < minimum:
        raise ValidationError("%s不能小于%s" % (key, minimum))
    if maximum is not None and value > maximum:
        raise ValidationError("%s不能大于%s" % (key, maximum))
    return value


def validate_box(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "box_no": text(data, "box_no"),
        "shelf_location": text(data, "shelf_location"),
        "capacity_pages": integer(data, "capacity_pages", 1, MAX_PAGES),
    }


def validate_dossier(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "record_id": integer(data, "record_id", 1),
        "box_id": integer(data, "box_id", 1),
        "page_count": integer(data, "page_count", 1, MAX_PAGES),
    }


def validate_borrow(data: Dict[str, Any], today: date) -> Dict[str, Any]:
    purpose = text(data, "purpose")
    due = parse_day(data, "due_date")
    if due < today:
        raise ValidationError("归还日不能早于今天")
    return {"purpose": purpose, "due_date": due.isoformat()}


def validate_return(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"missing_pages": optional_integer(data, "missing_pages", 0, 0)}


def validate_repair_note(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"note": optional_text(data, "note")}
