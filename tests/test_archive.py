import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import build_archive_service, build_service
from src.domain import Actor, Conflict, NotFound, PermissionDenied, ValidationError


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'evidence_count': 4, 'days_late': 90, 'appeal_deadline_day': 60}
FLOW = [('investigate', 'inspector', {'plan': '核对账簿'}, 'investigating'), ('propose', 'inspector', {'proposal': '补税并处罚'}, 'proposed'), ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}, 'reviewed'), ('close', 'reviewer', {'final_decision': '维持处理'}, 'closed')]


def close_case(service, reference, taxpayer):
    data = dict(CREATE_DATA)
    data['taxpayer'] = taxpayer
    record = service.create(Actor('creator', 'inspector'), reference, data)
    for action, role, payload, _ in FLOW:
        record = service.act(Actor('operator', role), record['id'], record['version'], action, payload)
    return record


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db = str(Path(self.temp.name) / 'test.db')
        self.cases = build_service(db)
        self.archive = build_archive_service(db)
        self.archivist = Actor('amy', 'archivist')
        self.reviewer = Actor('ray', 'reviewer')

    def tearDown(self):
        self.temp.cleanup()

    def make_box(self, box_no='BOX-001', capacity=500):
        return self.archive.create_box(self.archivist, {'box_no': box_no, 'shelf_location': 'A-3-2', 'capacity_pages': capacity})

    def make_dossier(self, reference='TAX-26001', taxpayer='Star Ltd', box_no='BOX-001', pages=120):
        record = close_case(self.cases, reference, taxpayer)
        box = self.make_box(box_no)
        dossier = self.archive.archive_record(self.archivist, {'record_id': record['id'], 'box_id': box['id'], 'page_count': pages})
        return record, dossier

    def borrow(self, dossier_id, days=7):
        due = (date.today() + timedelta(days=days)).isoformat()
        return self.archive.borrow(self.reviewer, dossier_id, {'purpose': '复核抽查', 'due_date': due})

    def test_archive_borrow_return_reborrow(self):
        record, dossier = self.make_dossier()
        self.assertEqual(dossier['status'], 'available')
        self.assertEqual(dossier['box_no'], 'BOX-001')
        loan = self.borrow(dossier['id'])
        self.assertEqual(loan['dossier_id'], dossier['id'])
        dossier = self.archive.get_dossier(self.archivist, dossier['id'])
        self.assertEqual(dossier['status'], 'borrowed')
        self.assertEqual(dossier['active_loan']['purpose'], '复核抽查')
        result = self.archive.return_dossier(self.reviewer, dossier['id'], {'missing_pages': 0})
        self.assertIsNone(result['repair_id'])
        self.assertEqual(result['dossier_status'], 'available')
        self.borrow(dossier['id'])

    def test_archive_requires_closed_case(self):
        record = self.cases.create(Actor('creator', 'inspector'), 'TAX-26002', dict(CREATE_DATA))
        box = self.make_box()
        with self.assertRaises(Conflict):
            self.archive.archive_record(self.archivist, {'record_id': record['id'], 'box_id': box['id'], 'page_count': 100})

    def test_unique_box_no_and_single_dossier_per_case(self):
        record, dossier = self.make_dossier()
        with self.assertRaises(Conflict):
            self.make_box()
        other_box = self.make_box('BOX-002')
        with self.assertRaises(Conflict):
            self.archive.archive_record(self.archivist, {'record_id': record['id'], 'box_id': other_box['id'], 'page_count': 50})

    def test_box_capacity(self):
        box = self.make_box(capacity=150)
        first = close_case(self.cases, 'TAX-26003', 'Alpha Ltd')
        self.archive.archive_record(self.archivist, {'record_id': first['id'], 'box_id': box['id'], 'page_count': 120})
        second = close_case(self.cases, 'TAX-26004', 'Beta Ltd')
        with self.assertRaises(Conflict):
            self.archive.archive_record(self.archivist, {'record_id': second['id'], 'box_id': box['id'], 'page_count': 40})
        self.archive.archive_record(self.archivist, {'record_id': second['id'], 'box_id': box['id'], 'page_count': 30})
        boxes = self.archive.list_boxes(self.archivist)
        self.assertEqual(boxes[0]['used_pages'], 150)
        self.assertEqual(boxes[0]['dossier_count'], 2)

    def test_borrow_rules_and_permissions(self):
        record, dossier = self.make_dossier()
        with self.assertRaises(PermissionDenied):
            self.archive.borrow(Actor('ivy', 'inspector'), dossier['id'], {'purpose': '查看', 'due_date': date.today().isoformat()})
        self.borrow(dossier['id'])
        with self.assertRaises(Conflict):
            self.borrow(dossier['id'])
        with self.assertRaises(NotFound):
            self.archive.return_dossier(self.reviewer, 9999, {'missing_pages': 0})
        _, fresh = self.make_dossier(reference='TAX-26009', taxpayer='Gamma Ltd', box_no='BOX-009')
        with self.assertRaises(Conflict):
            self.archive.return_dossier(self.reviewer, fresh['id'], {'missing_pages': 0})

    def test_borrow_validation(self):
        record, dossier = self.make_dossier()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with self.assertRaises(ValidationError):
            self.archive.borrow(self.reviewer, dossier['id'], {'purpose': '复核', 'due_date': yesterday})
        with self.assertRaises(ValidationError):
            self.archive.borrow(self.reviewer, dossier['id'], {'purpose': '复核', 'due_date': 'not-a-date'})
        with self.assertRaises(ValidationError):
            self.archive.borrow(self.reviewer, dossier['id'], {'purpose': ' ', 'due_date': date.today().isoformat()})

    def test_overdue_marked(self):
        record, dossier = self.make_dossier()
        self.borrow(dossier['id'], days=1)
        self.archive.clock = lambda: date.today() + timedelta(days=5)
        overdue = self.archive.list_dossiers(self.archivist, overdue_only=True)
        self.assertEqual(len(overdue), 1)
        self.assertTrue(overdue[0]['overdue'])
        loans = self.archive.list_loans(self.reviewer, active_only=True, overdue_only=True)
        self.assertEqual(len(loans), 1)
        self.archive.return_dossier(self.reviewer, dossier['id'], {'missing_pages': 0})
        self.assertEqual(self.archive.list_dossiers(self.archivist, overdue_only=True), [])

    def test_missing_pages_repair_flow(self):
        record, dossier = self.make_dossier(pages=100)
        self.borrow(dossier['id'])
        with self.assertRaises(ValidationError):
            self.archive.return_dossier(self.reviewer, dossier['id'], {'missing_pages': 101})
        result = self.archive.return_dossier(self.reviewer, dossier['id'], {'missing_pages': 3})
        self.assertIsNotNone(result['repair_id'])
        self.assertEqual(result['dossier_status'], 'repair')
        with self.assertRaises(Conflict):
            self.borrow(dossier['id'])
        repairs = self.archive.list_repairs(self.archivist, status='pending')
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]['missing_pages'], 3)
        with self.assertRaises(PermissionDenied):
            self.archive.complete_repair(self.reviewer, repairs[0]['id'], {'note': '补齐'})
        done = self.archive.complete_repair(self.archivist, repairs[0]['id'], {'note': '缺页已补齐'})
        self.assertEqual(done['status'], 'done')
        self.assertEqual(done['dossier_status'], 'available')
        self.borrow(dossier['id'])

    def test_archive_events_in_case_timeline(self):
        record, dossier = self.make_dossier()
        self.borrow(dossier['id'])
        self.archive.return_dossier(self.reviewer, dossier['id'], {'missing_pages': 0})
        timeline = self.cases.timeline(self.archivist, record['id'])
        actions = [event['action'] for event in timeline]
        self.assertIn('archive_boxed', actions)
        self.assertIn('archive_borrowed', actions)
        self.assertIn('archive_returned', actions)

    def test_unknown_role_denied(self):
        with self.assertRaises(PermissionDenied):
            self.archive.list_boxes(Actor('outsider', 'outsider'))


if __name__ == '__main__':
    unittest.main()
