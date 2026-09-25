import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import build_archive_service, build_service
from src.archive_domain import DOSSIER_IN_REPAIR, DOSSIER_ON_LOAN, DOSSIER_SHELVED
from src.domain import Actor, Conflict, PermissionDenied, ValidationError


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'evidence_count': 4, 'days_late': 90, 'appeal_deadline_day': 60}
FLOW = [('investigate', 'inspector', {'plan': '核对账簿'}, 'investigating'), ('propose', 'inspector', {'proposal': '补税并处罚'}, 'proposed'), ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}, 'reviewed'), ('close', 'reviewer', {'final_decision': '维持处理'}, 'closed')]
REVIEWER = Actor("rv1", "reviewer")
INSPECTOR = Actor("in1", "inspector")


class ArchiveWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db = str(Path(self.temp.name) / "test.db")
        self.service = build_service(db)
        self.archive = build_archive_service(db)
        self.today = date(2026, 9, 25)

    def tearDown(self):
        self.temp.cleanup()

    def _closed_record(self, reference="TAX-26001"):
        record = self.service.create(Actor("creator", "inspector"), reference, CREATE_DATA)
        for action, role, data, _ in FLOW:
            record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
        self.assertEqual(record["state"], "closed")
        return record

    def test_pack_borrow_return_flow(self):
        record = self._closed_record()
        box = self.archive.create_box(REVIEWER, {"box_code": "BOX-001", "shelf_location": "A-3-2", "capacity_pages": 500})
        self.assertEqual(box["remaining_pages"], 500)
        dossier = self.archive.pack(REVIEWER, record["id"], {"box_code": "BOX-001", "title": "Star稽查卷", "pages": 120})
        self.assertEqual(dossier["status"], DOSSIER_SHELVED)

        due = self.today + timedelta(days=7)
        loan = self.archive.borrow(REVIEWER, dossier["id"], {"purpose": "复议调卷", "due_date": due.isoformat()}, today=self.today)
        self.assertFalse(loan["overdue"])
        self.assertEqual(loan["borrowed_on"], self.today.isoformat())
        self.assertEqual(self.archive.repository.get_dossier(dossier["id"])["status"], DOSSIER_ON_LOAN)

        # 未归还前不能再借
        with self.assertRaises(Conflict):
            self.archive.borrow(REVIEWER, dossier["id"], {"purpose": "再次调卷", "due_date": due.isoformat()}, today=self.today)

        result = self.archive.return_dossier(REVIEWER, dossier["id"], {"note": "核对无误"}, today=self.today)
        self.assertEqual(result["status"], DOSSIER_SHELVED)
        self.assertFalse(result["transferred_to_repair"])
        # 归还后可以再次借阅
        loan2 = self.archive.borrow(REVIEWER, dossier["id"], {"purpose": "复检", "due_date": due.isoformat()}, today=self.today)
        self.assertFalse(loan2["overdue"])
        self.archive.return_dossier(REVIEWER, dossier["id"], {}, today=self.today)

        # 装盒事件进入既有稽查审计时间线
        timeline = self.service.timeline(INSPECTOR, record["id"])
        actions = [event["action"] for event in timeline]
        self.assertIn("archived", actions)

    def test_overdue_marked_in_loan_list(self):
        record = self._closed_record()
        self.archive.create_box(REVIEWER, {"box_code": "BOX-001", "shelf_location": "A-3-2", "capacity_pages": 500})
        dossier = self.archive.pack(REVIEWER, record["id"], {"box_code": "BOX-001", "title": "Star稽查卷", "pages": 10})
        due = self.today - timedelta(days=1)
        self.archive.borrow(REVIEWER, dossier["id"], {"purpose": "调卷", "due_date": due.isoformat()}, today=self.today - timedelta(days=10))
        loans = self.archive.list_loans(REVIEWER, active_only=True, today=self.today)
        self.assertEqual(len(loans), 1)
        self.assertTrue(loans[0]["overdue"])
        # 归还后不再显示逾期
        self.archive.return_dossier(REVIEWER, dossier["id"], {}, today=self.today)
        loans = self.archive.list_loans(REVIEWER, today=self.today)
        self.assertFalse(any(loan["overdue"] for loan in loans))

    def test_missing_pages_goes_to_repair_until_filled(self):
        record = self._closed_record()
        self.archive.create_box(REVIEWER, {"box_code": "BOX-001", "shelf_location": "A-3-2", "capacity_pages": 500})
        dossier = self.archive.pack(REVIEWER, record["id"], {"box_code": "BOX-001", "title": "Star稽查卷", "pages": 10})
        due = (self.today + timedelta(days=5)).isoformat()
        self.archive.borrow(REVIEWER, dossier["id"], {"purpose": "调卷", "due_date": due}, today=self.today)
        returned = self.archive.return_dossier(REVIEWER, dossier["id"], {"missing_pages": True, "note": "缺12-15页"}, today=self.today)
        self.assertEqual(returned["status"], DOSSIER_IN_REPAIR)
        self.assertTrue(returned["transferred_to_repair"])

        pending = self.archive.list_repairs(REVIEWER, pending_only=True)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["note"], "缺12-15页")

        # 修补中不能借阅
        with self.assertRaises(Conflict):
            self.archive.borrow(REVIEWER, dossier["id"], {"purpose": "急用", "due_date": due}, today=self.today)

        fixed = self.archive.complete_repair(REVIEWER, dossier["id"], {"note": "已补齐四页"})
        self.assertEqual(fixed["status"], DOSSIER_SHELVED)
        self.assertEqual(self.archive.list_repairs(REVIEWER, pending_only=True), [])
        # 补齐后可以重新借阅
        loan = self.archive.borrow(REVIEWER, dossier["id"], {"purpose": "复检", "due_date": due}, today=self.today)
        self.assertEqual(loan["status"], "active")


class ArchiveFailureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db = str(Path(self.temp.name) / "test.db")
        self.service = build_service(db)
        self.archive = build_archive_service(db)
        self.REVIEWER = Actor("rv1", "reviewer")
        self.INSPECTOR = Actor("in1", "inspector")

    def tearDown(self):
        self.temp.cleanup()

    def _record(self, reference="TAX-26001", close=False):
        record = self.service.create(Actor("creator", "inspector"), reference, CREATE_DATA)
        if close:
            for action, role, data, _ in FLOW:
                record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
        return record

    def test_only_closed_case_can_pack(self):
        record = self._record(close=False)
        self.archive.create_box(self.REVIEWER, {"box_code": "BOX-001", "shelf_location": "A", "capacity_pages": 100})
        with self.assertRaises(Conflict):
            self.archive.pack(self.REVIEWER, record["id"], {"box_code": "BOX-001", "title": "t", "pages": 10})

    def test_unique_box_per_case(self):
        record = self._record(close=True)
        self.archive.create_box(self.REVIEWER, {"box_code": "BOX-001", "shelf_location": "A", "capacity_pages": 500})
        self.archive.create_box(self.REVIEWER, {"box_code": "BOX-002", "shelf_location": "B", "capacity_pages": 500})
        self.archive.pack(self.REVIEWER, record["id"], {"box_code": "BOX-001", "title": "t", "pages": 10})
        with self.assertRaises(Conflict):
            self.archive.pack(self.REVIEWER, record["id"], {"box_code": "BOX-002", "title": "t2", "pages": 10})

    def test_box_capacity_enforced(self):
        record = self._record(close=True)
        self.archive.create_box(self.REVIEWER, {"box_code": "BOX-001", "shelf_location": "A", "capacity_pages": 100})
        with self.assertRaises(Conflict):
            self.archive.pack(self.REVIEWER, record["id"], {"box_code": "BOX-001", "title": "t", "pages": 101})
        self.archive.pack(self.REVIEWER, record["id"], {"box_code": "BOX-001", "title": "t", "pages": 60})
        record2 = self._record("TAX-26002", close=True)
        with self.assertRaises(Conflict):
            self.archive.pack(self.REVIEWER, record2["id"], {"box_code": "BOX-001", "title": "t2", "pages": 41})
        # 40页仍可装入
        self.archive.pack(self.REVIEWER, record2["id"], {"box_code": "BOX-001", "title": "t2", "pages": 40})

    def test_duplicate_box_code_rejected(self):
        self.archive.create_box(self.REVIEWER, {"box_code": "BOX-001", "shelf_location": "A", "capacity_pages": 100})
        with self.assertRaises(Conflict):
            self.archive.create_box(self.REVIEWER, {"box_code": "BOX-001", "shelf_location": "B", "capacity_pages": 100})

    def test_permissions(self):
        # 稽查人员不能登记盒/装盒/借阅，但可以只读查看
        with self.assertRaises(PermissionDenied):
            self.archive.create_box(self.INSPECTOR, {"box_code": "B", "shelf_location": "A", "capacity_pages": 10})
        self.assertEqual(self.archive.list_boxes(self.INSPECTOR), [])
        record = self._record(close=True)
        with self.assertRaises(PermissionDenied):
            self.archive.pack(self.INSPECTOR, record["id"], {"box_code": "B", "title": "t", "pages": 1})

    def test_due_date_validation(self):
        record = self._record(close=True)
        self.archive.create_box(self.REVIEWER, {"box_code": "BOX-001", "shelf_location": "A", "capacity_pages": 100})
        dossier = self.archive.pack(self.REVIEWER, record["id"], {"box_code": "BOX-001", "title": "t", "pages": 10})
        yesterday = (date(2026, 9, 25) - timedelta(days=1)).isoformat()
        with self.assertRaises(ValidationError):
            self.archive.borrow(self.REVIEWER, dossier["id"], {"purpose": "调卷", "due_date": yesterday}, today=date(2026, 9, 25))
        with self.assertRaises(ValidationError):
            self.archive.borrow(self.REVIEWER, dossier["id"], {"purpose": "调卷", "due_date": "2026-9-25"}, today=date(2026, 9, 25))

    def test_return_without_active_loan_rejected(self):
        record = self._record(close=True)
        self.archive.create_box(self.REVIEWER, {"box_code": "BOX-001", "shelf_location": "A", "capacity_pages": 100})
        dossier = self.archive.pack(self.REVIEWER, record["id"], {"box_code": "BOX-001", "title": "t", "pages": 10})
        with self.assertRaises(Conflict):
            self.archive.return_dossier(self.REVIEWER, dossier["id"], {})
        with self.assertRaises(Conflict):
            self.archive.complete_repair(self.REVIEWER, dossier["id"], {})


if __name__ == "__main__":
    unittest.main()
