import json
import os
import unittest
import unittest.mock
from unittest.mock import MagicMock

from sevdesk_archiver import archive as archive_mod
from sevdesk_archiver.archive import (
    _clean_for_filename,
    _iter_month_ranges,
    generate_archive_filename,
    scan_existing,
    write_manifest,
)


class TestFilenameGeneration(unittest.TestCase):
    def test_invoice_filename_with_receiver(self):
        doc = {
            "id": "42",
            "invoiceNumber": "RE-2026/0001",
            "invoiceDate": "2026-01-15T00:00:00+01:00",
            "contact": {"name": "Mustermann GmbH"},
        }
        fn = generate_archive_filename(doc, "Invoice")
        self.assertEqual(fn, "inv-20260115-RE-2026_0001-Mustermann_GmbH.pdf")

    def test_credit_note_filename(self):
        doc = {
            "id": "7",
            "creditNoteNumber": "GS-2026-003",
            "creditNoteDate": "2026-03-02",
            "contact": {"name": "ACME AG"},
        }
        fn = generate_archive_filename(doc, "CreditNote")
        self.assertEqual(fn, "cn-20260302-GS-2026-003-ACME_AG.pdf")

    def test_filename_falls_back_to_surename_familyname(self):
        doc = {
            "id": "1",
            "invoiceNumber": "RE-2026-001",
            "invoiceDate": "2026-01-15",
            "contact": {
                "objectName": "Contact",
                "name": None,
                "surename": "Alexander",
                "familyname": "Guelmami",
            },
        }
        fn = generate_archive_filename(doc, "Invoice")
        self.assertEqual(fn, "inv-20260115-RE-2026-001-Alexander_Guelmami.pdf")

    def test_filename_ignores_object_name(self):
        doc = {
            "id": "1",
            "invoiceNumber": "RE-2026-001",
            "invoiceDate": "2026-01-15",
            "contact": {"objectName": "Contact", "name": None},
        }
        fn = generate_archive_filename(doc, "Invoice")
        self.assertEqual(fn, "inv-20260115-RE-2026-001.pdf")

    def test_voucher_filename_uses_supplier(self):
        doc = {
            "id": "3",
            "voucherDate": "2026-02-10",
            "description": "Rechnung-Telekom",
            "supplier": {"name": "Deutsche Telekom"},
        }
        fn = generate_archive_filename(doc, "Voucher")
        self.assertEqual(fn, "vou-20260210-Rechnung-Telekom-Deutsche_Telekom.pdf")

    def test_filename_without_receiver(self):
        doc = {"id": "1", "invoiceNumber": "RE-1", "invoiceDate": "2026-01-01"}
        fn = generate_archive_filename(doc, "Invoice")
        self.assertEqual(fn, "inv-20260101-RE-1.pdf")

    def test_filename_missing_date(self):
        doc = {"id": "1", "invoiceNumber": "RE-1", "contact": {"name": "X"}}
        fn = generate_archive_filename(doc, "Invoice")
        self.assertTrue(fn.startswith("inv-00000000-"))

    def test_filename_missing_number_falls_back_to_id(self):
        doc = {"id": "99", "invoiceDate": "2026-01-01", "contact": {"name": "X"}}
        fn = generate_archive_filename(doc, "Invoice")
        self.assertIn("INV-99", fn)

    def test_clean_strips_unsafe_chars(self):
        self.assertEqual(_clean_for_filename('Foo: *Bar*?"'), "Foo_Bar")

    def test_clean_keeps_umlauts(self):
        self.assertEqual(_clean_for_filename("Müller GmbH"), "Müller_GmbH")

    def test_clean_truncates_long(self):
        long = "A" * 100
        self.assertEqual(len(_clean_for_filename(long)), 50)

    def test_clean_empty(self):
        self.assertEqual(_clean_for_filename(""), "")
        self.assertEqual(_clean_for_filename("   "), "")


class TestMonthIteration(unittest.TestCase):
    def test_single_month(self):
        ranges = list(_iter_month_ranges("2026-01-10", "2026-01-25"))
        self.assertEqual(ranges, [("2026-01-10", "2026-01-25")])

    def test_spans_two_months(self):
        ranges = list(_iter_month_ranges("2026-01-15", "2026-02-10"))
        self.assertEqual(
            ranges, [("2026-01-15", "2026-01-31"), ("2026-02-01", "2026-02-10")]
        )

    def test_year_boundary(self):
        ranges = list(_iter_month_ranges("2025-12-20", "2026-01-05"))
        self.assertEqual(
            ranges, [("2025-12-20", "2025-12-31"), ("2026-01-01", "2026-01-05")]
        )

    def test_empty_range(self):
        ranges = list(_iter_month_ranges("2026-03-01", "2026-02-01"))
        self.assertEqual(ranges, [])


class TestScanExisting(unittest.TestCase):
    def setUp(self):
        import shutil

        self.tmp = os.path.join(os.path.dirname(__file__), "_tmp_archive_scan")
        if os.path.exists(self.tmp):
            shutil.rmtree(self.tmp)
        self.files = os.path.join(self.tmp, "files")
        os.makedirs(self.files, exist_ok=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp)

    def _write(self, name, payload, root=False):
        path = self.tmp if root else self.files
        with open(os.path.join(path, name), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_indexes_by_id_and_detects_pdf(self):
        self._write(
            "inv-20260101-RE-1-X.json",
            {"sevdesk_id": "42", "type": "Invoice", "document": {"id": "42"}},
        )
        with open(os.path.join(self.files, "inv-20260101-RE-1-X.pdf"), "wb") as f:
            f.write(b"%PDF")

        index = scan_existing(self.tmp)
        self.assertIn("42", index)
        self.assertIsNotNone(index["42"]["pdf"])

    def test_missing_pdf_is_reflected(self):
        self._write(
            "inv-x.json",
            {"sevdesk_id": "7", "type": "Invoice", "document": {"id": "7"}},
        )
        index = scan_existing(self.tmp)
        self.assertIsNone(index["7"]["pdf"])

    def test_skips_manifest_in_root(self):
        self._write("manifest.json", {"entries": []}, root=True)
        index = scan_existing(self.tmp)
        self.assertEqual(index, {})

    def test_skips_malformed(self):
        with open(os.path.join(self.files, "bad.json"), "w") as f:
            f.write("{not json")
        index = scan_existing(self.tmp)
        self.assertEqual(index, {})

    def test_scan_empty_when_no_files_dir(self):
        import shutil

        shutil.rmtree(self.files)
        index = scan_existing(self.tmp)
        self.assertEqual(index, {})


class TestMigrateFlatToSubdir(unittest.TestCase):
    def setUp(self):
        import shutil

        self.tmp = os.path.join(os.path.dirname(__file__), "_tmp_archive_migrate")
        if os.path.exists(self.tmp):
            shutil.rmtree(self.tmp)
        os.makedirs(self.tmp)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp)

    def test_moves_pdf_and_json_pairs(self):
        from sevdesk_archiver.archive import migrate_flat_to_subdir

        for name in ("inv-1.pdf", "inv-1.json", "inv-2.pdf", "inv-2.json"):
            with open(os.path.join(self.tmp, name), "w") as f:
                f.write("x")
        with open(os.path.join(self.tmp, "index.html"), "w") as f:
            f.write("<html>")
        with open(os.path.join(self.tmp, "manifest.json"), "w") as f:
            f.write("{}")

        moved = migrate_flat_to_subdir(self.tmp)

        self.assertEqual(moved, 4)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "files", "inv-1.pdf")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "files", "inv-2.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "index.html")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "manifest.json")))

    def test_idempotent_no_double_move(self):
        from sevdesk_archiver.archive import migrate_flat_to_subdir

        with open(os.path.join(self.tmp, "inv-1.pdf"), "w") as f:
            f.write("x")
        self.assertEqual(migrate_flat_to_subdir(self.tmp), 1)
        self.assertEqual(migrate_flat_to_subdir(self.tmp), 0)


class TestArchiveFlow(unittest.TestCase):
    def setUp(self):
        import shutil

        self.tmp = os.path.join(os.path.dirname(__file__), "_tmp_archive_flow")
        if os.path.exists(self.tmp):
            shutil.rmtree(self.tmp)
        os.makedirs(self.tmp)
        self.files = os.path.join(self.tmp, "files")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp)

    def _mk_client(self, invoices):
        client = MagicMock()
        client.get_invoices.return_value = invoices
        client.get_credit_notes.return_value = []
        client.get_vouchers.return_value = []
        client.download_document.return_value = (b"%PDF-CONTENT", "src.pdf")
        return client

    def _doc(self, _id="1", number="RE-2026-001", date="2026-02-10", name="ACME"):
        return {
            "id": _id,
            "invoiceNumber": number,
            "invoiceDate": date,
            "contact": {"name": name},
            "sumGross": 119.0,
            "sumNet": 100.0,
            "sumTax": 19.0,
            "currency": "EUR",
            "status": "200",
            "invoiceType": "RE",
        }

    def test_manifest_includes_delivery_period(self):
        doc = self._doc()
        doc["deliveryDate"] = "2026-01-01"
        doc["deliveryDateUntil"] = "2026-01-31"
        client = self._mk_client([doc])

        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        with open(os.path.join(self.tmp, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        entry = manifest["entries"][0]
        self.assertEqual(entry["delivery_date"], "2026-01-01")
        self.assertEqual(entry["delivery_date_until"], "2026-01-31")

    def test_first_run_writes_pdf_and_json_and_manifest(self):
        client = self._mk_client([self._doc()])

        events = list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        root_files = set(os.listdir(self.tmp))
        sub_files = set(os.listdir(self.files))
        self.assertIn("inv-20260210-RE-2026-001-ACME.pdf", sub_files)
        self.assertIn("inv-20260210-RE-2026-001-ACME.json", sub_files)
        self.assertIn("manifest.json", root_files)
        self.assertIn("index.html", root_files)

        with open(os.path.join(self.tmp, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["count"], 1)
        entry = manifest["entries"][0]
        self.assertEqual(entry["id"], "1")
        self.assertEqual(entry["year"], 2026)
        self.assertEqual(entry["month"], 2)
        self.assertEqual(entry["status_label"], "Versendet")
        self.assertEqual(entry["invoice_type_label"], "Rechnung")
        self.assertEqual(entry["receiver"], "ACME")
        self.assertTrue(entry["pdf"].startswith("files/"))
        self.assertTrue(entry["json"].startswith("files/"))

        self.assertTrue(any("+ inv-20260210" in e["message"] for e in events))

    def test_second_run_is_idempotent(self):
        client = self._mk_client([self._doc()])

        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )
        client.download_document.reset_mock()

        events = list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        client.download_document.assert_not_called()
        msgs = " ".join(e["message"] for e in events)
        self.assertIn("Skipped=1", msgs)

    def test_missing_pdf_is_redownloaded(self):
        client = self._mk_client([self._doc()])
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )
        os.remove(os.path.join(self.files, "inv-20260210-RE-2026-001-ACME.pdf"))

        client.download_document.reset_mock()
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )
        client.download_document.assert_called_once()

    def test_download_records_pdf_hash_in_sidecar(self):
        import hashlib

        pdf_bytes = b"%PDF-CONTENT"
        client = self._mk_client([self._doc()])
        client.download_document.return_value = (pdf_bytes, "src.pdf")

        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        with open(
            os.path.join(self.files, "inv-20260210-RE-2026-001-ACME.json"),
            encoding="utf-8",
        ) as f:
            meta = json.load(f)
        expected = f"sha256:{hashlib.sha256(pdf_bytes).hexdigest()}"
        self.assertEqual(meta["pdf_hash"], expected)

    def test_fresh_sidecar_hashes_existing_pdf(self):
        """When archive writes a sidecar for a PDF already on disk with no
        prior hash known, it hashes the on-disk PDF. Covers the re-archive
        case after a sidecar was deleted manually."""
        import hashlib

        client = self._mk_client([self._doc()])
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )
        sidecar = os.path.join(self.files, "inv-20260210-RE-2026-001-ACME.json")
        pdf = os.path.join(self.files, "inv-20260210-RE-2026-001-ACME.pdf")
        os.remove(sidecar)
        with open(pdf, "rb") as f:
            pdf_bytes = f.read()
        expected = f"sha256:{hashlib.sha256(pdf_bytes).hexdigest()}"

        client = self._mk_client([self._doc()])
        client.download_document.reset_mock()
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        client.download_document.assert_not_called()
        with open(sidecar, encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["pdf_hash"], expected)

    def test_metadata_refresh_preserves_existing_hash(self):
        client = self._mk_client([self._doc(name="ACME")])
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )
        with open(
            os.path.join(self.files, "inv-20260210-RE-2026-001-ACME.json"),
            encoding="utf-8",
        ) as f:
            original_hash = json.load(f)["pdf_hash"]

        client = self._mk_client([self._doc(name="ACME AG")])
        client.download_document.reset_mock()
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        client.download_document.assert_not_called()
        with open(
            os.path.join(self.files, "inv-20260210-RE-2026-001-ACME.json"),
            encoding="utf-8",
        ) as f:
            refreshed = json.load(f)
        self.assertEqual(refreshed["pdf_hash"], original_hash)
        self.assertEqual(refreshed["document"]["contact"]["name"], "ACME AG")

    def test_metadata_change_refreshes_json_not_pdf(self):
        client = self._mk_client([self._doc(name="ACME")])
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        client = self._mk_client([self._doc(name="ACME AG")])
        client.download_document.reset_mock()
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        client.download_document.assert_not_called()
        self.assertTrue(
            os.path.exists(
                os.path.join(self.files, "inv-20260210-RE-2026-001-ACME.pdf")
            )
        )
        with open(
            os.path.join(self.files, "inv-20260210-RE-2026-001-ACME.json"),
            encoding="utf-8",
        ) as f:
            payload = json.load(f)
        self.assertEqual(payload["document"]["contact"]["name"], "ACME AG")

    def test_dry_run_writes_nothing(self):
        client = self._mk_client([self._doc()])

        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
                dry_run=True,
            )
        )

        client.download_document.assert_not_called()
        root_files = set(os.listdir(self.tmp))
        sub_files = set(os.listdir(self.files)) if os.path.isdir(self.files) else set()
        self.assertNotIn("inv-20260210-RE-2026-001-ACME.pdf", sub_files)
        self.assertNotIn("manifest.json", root_files)

    def test_drafts_are_never_archived(self):
        client = MagicMock()
        client.get_invoices.return_value = [
            self._doc(_id="1", number="RE-1"),
            {**self._doc(_id="2", number="RE-DRAFT"), "status": "100"},
        ]
        client.get_credit_notes.return_value = []
        client.get_vouchers.return_value = []
        client.download_document.return_value = (b"%PDF", "src.pdf")

        events = list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        files = set(os.listdir(self.files))
        self.assertTrue(any("RE-1" in f for f in files))
        self.assertFalse(any("DRAFT" in f for f in files))
        self.assertTrue(any("skipped 1 draft" in e["message"] for e in events))

    def test_voucher_draft_threshold_is_lower(self):
        client = MagicMock()
        client.get_invoices.return_value = []
        client.get_credit_notes.return_value = []
        client.get_vouchers.return_value = [
            {
                "id": "1",
                "voucherDate": "2026-02-10",
                "description": "Valid",
                "supplier": {"name": "Supp"},
                "status": "100",
            },
            {
                "id": "2",
                "voucherDate": "2026-02-11",
                "description": "Draft",
                "supplier": {"name": "Supp"},
                "status": "50",
            },
        ]
        client.download_document.return_value = (b"%PDF", "src.pdf")

        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
                include_vouchers=True,
            )
        )

        files = set(os.listdir(self.files))
        self.assertTrue(any(f.startswith("vou-") and "Valid" in f for f in files))
        self.assertFalse(any("Draft" in f for f in files))

    def test_orphan_out_of_range_is_included_in_manifest(self):
        client = self._mk_client([self._doc(date="2026-02-10")])
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
            )
        )

        client = self._mk_client([])
        list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-03-01",
                end_date="2026-03-31",
            )
        )

        with open(os.path.join(self.tmp, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["count"], 1)
        self.assertEqual(manifest["entries"][0]["id"], "1")


class TestRetryBehavior(unittest.TestCase):
    def setUp(self):
        import shutil

        self.tmp = os.path.join(os.path.dirname(__file__), "_tmp_archive_retry")
        if os.path.exists(self.tmp):
            shutil.rmtree(self.tmp)
        os.makedirs(self.tmp)
        self.files = os.path.join(self.tmp, "files")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp)

    def test_rate_limit_on_fetch_is_retried(self):
        from sevdesk_archiver.exceptions import RateLimitExceededError

        client = MagicMock()
        client.get_invoices.side_effect = [
            RateLimitExceededError(service="SevDesk", retry_after=0),
            [
                {
                    "id": "1",
                    "invoiceNumber": "RE-1",
                    "invoiceDate": "2026-02-10",
                    "contact": {"name": "X"},
                    "status": "200",
                }
            ],
        ]
        client.get_credit_notes.return_value = []
        client.get_vouchers.return_value = []
        client.download_document.return_value = (b"%PDF", "src.pdf")

        with unittest.mock.patch("sevdesk_archiver.archive.time.sleep") as mock_sleep:
            events = list(
                archive_mod.archive(
                    client=client,
                    target_dir=self.tmp,
                    after_date="2026-02-01",
                    end_date="2026-02-28",
                )
            )

        self.assertEqual(client.get_invoices.call_count, 2)
        mock_sleep.assert_called()
        self.assertTrue(any("rate-limited" in e["message"] for e in events))
        self.assertTrue(any(f.endswith(".pdf") for f in os.listdir(self.files)))

    def test_rate_limit_on_download_is_retried(self):
        from sevdesk_archiver.exceptions import RateLimitExceededError

        client = MagicMock()
        client.get_invoices.return_value = [
            {
                "id": "1",
                "invoiceNumber": "RE-1",
                "invoiceDate": "2026-02-10",
                "contact": {"name": "X"},
                "status": "200",
            }
        ]
        client.get_credit_notes.return_value = []
        client.get_vouchers.return_value = []
        client.download_document.side_effect = [
            RateLimitExceededError(service="SevDesk", retry_after=0),
            (b"%PDF", "src.pdf"),
        ]

        with unittest.mock.patch("sevdesk_archiver.archive.time.sleep"):
            events = list(
                archive_mod.archive(
                    client=client,
                    target_dir=self.tmp,
                    after_date="2026-02-01",
                    end_date="2026-02-28",
                )
            )

        self.assertEqual(client.download_document.call_count, 2)
        self.assertTrue(any(f.endswith(".pdf") for f in os.listdir(self.files)))
        msg = " ".join(e["message"] for e in events)
        self.assertIn("Downloaded=1", msg)

    def test_404_classified_as_no_pdf_not_error(self):
        from sevdesk_archiver.exceptions import DocumentNotFoundError

        client = MagicMock()
        client.get_invoices.return_value = []
        client.get_credit_notes.return_value = []
        client.get_vouchers.return_value = [
            {
                "id": "42",
                "voucherDate": "2026-02-10",
                "description": "Manual entry",
                "supplier": {"name": "Acme"},
                "status": "1000",
            }
        ]
        client.download_document.side_effect = DocumentNotFoundError("Voucher", "42")

        events = list(
            archive_mod.archive(
                client=client,
                target_dir=self.tmp,
                after_date="2026-02-01",
                end_date="2026-02-28",
                include_vouchers=True,
            )
        )

        msgs = " ".join(e["message"] for e in events)
        self.assertIn("no PDF on SevDesk", msgs)
        self.assertIn("NoPdf=1", msgs)
        self.assertIn("Errors=0", msgs)
        self.assertFalse(any(e["type"] == "error" for e in events))

        with open(os.path.join(self.tmp, "manifest.json"), encoding="utf-8") as f:
            mf = json.load(f)
        self.assertEqual(mf["entries"][0]["no_pdf"], True)

    def test_rate_limit_gives_up_after_max_retries(self):
        from sevdesk_archiver.exceptions import RateLimitExceededError

        client = MagicMock()
        client.get_invoices.return_value = [
            {
                "id": "1",
                "invoiceNumber": "RE-1",
                "invoiceDate": "2026-02-10",
                "contact": {"name": "X"},
                "status": "200",
            }
        ]
        client.get_credit_notes.return_value = []
        client.get_vouchers.return_value = []
        client.download_document.side_effect = RateLimitExceededError(
            service="SevDesk", retry_after=0
        )

        with unittest.mock.patch("sevdesk_archiver.archive.time.sleep"):
            events = list(
                archive_mod.archive(
                    client=client,
                    target_dir=self.tmp,
                    after_date="2026-02-01",
                    end_date="2026-02-28",
                )
            )

        from sevdesk_archiver.archive import MAX_RETRIES_PER_CALL

        self.assertEqual(client.download_document.call_count, MAX_RETRIES_PER_CALL + 1)
        self.assertTrue(any(e["type"] == "error" for e in events))


class TestWriteManifest(unittest.TestCase):
    def test_writes_valid_json(self):
        tmp = os.path.join(os.path.dirname(__file__), "_tmp_archive_manifest")
        os.makedirs(tmp, exist_ok=True)
        try:
            entries = [{"id": "1", "receiver": "X"}]
            path = write_manifest(tmp, entries)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["entries"], entries)
            self.assertIn("generated_at", data)
        finally:
            for n in os.listdir(tmp):
                os.remove(os.path.join(tmp, n))
            os.rmdir(tmp)


class TestIndexHtmlInstall(unittest.TestCase):
    def test_archive_run_installs_index_html(self):
        import shutil
        from unittest.mock import MagicMock

        tmp = os.path.join(os.path.dirname(__file__), "_tmp_archive_idx")
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
        os.makedirs(tmp)
        try:
            client = MagicMock()
            client.get_invoices.return_value = []
            client.get_credit_notes.return_value = []
            client.get_vouchers.return_value = []
            list(
                archive_mod.archive(
                    client=client,
                    target_dir=tmp,
                    after_date="2026-02-01",
                    end_date="2026-02-28",
                )
            )
            root = os.listdir(tmp)
            self.assertIn("index.html", root)
            self.assertIn("manifest.json", root)
            self.assertIn("files", root)
            with open(os.path.join(tmp, "index.html"), encoding="utf-8") as f:
                html = f.read()
            self.assertIn("manifest.json", html)
        finally:
            shutil.rmtree(tmp)

    def test_install_index_html_direct(self):
        from sevdesk_archiver.archive import install_index_html

        tmp = os.path.join(os.path.dirname(__file__), "_tmp_archive_idx2")
        os.makedirs(tmp, exist_ok=True)
        try:
            path = install_index_html(tmp)
            self.assertIsNotNone(path)
            self.assertTrue(os.path.exists(path))
        finally:
            for n in os.listdir(tmp):
                os.remove(os.path.join(tmp, n))
            os.rmdir(tmp)

    def test_install_serve_scripts(self):
        import shutil
        import stat

        from sevdesk_archiver.archive import install_serve_scripts

        tmp = os.path.join(os.path.dirname(__file__), "_tmp_archive_serve")
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
        os.makedirs(tmp)
        try:
            written = install_serve_scripts(tmp)
            names = {os.path.basename(p) for p in written}
            self.assertIn("serve.py", names)
            self.assertIn("serve-archive.sh", names)
            for p in written:
                mode = os.stat(p).st_mode
                self.assertTrue(mode & stat.S_IXUSR)
            with open(os.path.join(tmp, "serve.py"), encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("import requests", content)
            self.assertNotIn("import click", content)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
