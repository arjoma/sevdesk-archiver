import json
import os
import shutil
import unittest

from click.testing import CliRunner

from sevdesk_archiver import archive as archive_mod
from sevdesk_archiver.cli import cli


class TestVerifyArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(os.path.dirname(__file__), "_tmp_verify")
        if os.path.exists(self.tmp):
            shutil.rmtree(self.tmp)
        os.makedirs(self.tmp)
        self.files = os.path.join(self.tmp, "files")
        os.makedirs(self.files)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write_manifest(self, entries):
        with open(os.path.join(self.tmp, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"count": len(entries), "entries": entries}, f)

    def _touch(self, name, contents=b"x"):
        with open(os.path.join(self.files, name), "wb") as f:
            f.write(contents)

    def _write_sidecar(
        self,
        name,
        sevdesk_id="1",
        doc_type="Invoice",
        pdf_filename=None,
        pdf_hash=None,
        document=None,
    ):
        """Write a shape-valid sidecar JSON file."""
        pdf_filename = pdf_filename or (name[:-5] + ".pdf")
        payload = {
            "archive_version": 1,
            "sevdesk_id": str(sevdesk_id),
            "type": doc_type,
            "archived_at": "2026-01-01T00:00:00+00:00",
            "pdf_filename": pdf_filename,
        }
        if pdf_hash is not None:
            payload["pdf_hash"] = pdf_hash
        payload["document"] = document or {"id": sevdesk_id}
        with open(os.path.join(self.files, name), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_consistent_archive(self):
        self._write_manifest(
            [
                {
                    "id": "1",
                    "pdf": "files/inv-1.pdf",
                    "json": "files/inv-1.json",
                    "no_pdf": False,
                }
            ]
        )
        self._touch("inv-1.pdf")
        self._touch("inv-1.json")

        report = archive_mod.verify_archive(self.tmp)
        self.assertEqual(report["missing_pdf"], [])
        self.assertEqual(report["missing_json"], [])
        self.assertEqual(report["orphan_pdf"], [])
        self.assertEqual(report["orphan_json"], [])
        self.assertEqual(report["manifest_count"], 1)

    def test_detects_missing_pdf(self):
        self._write_manifest(
            [
                {
                    "id": "1",
                    "pdf": "files/inv-1.pdf",
                    "json": "files/inv-1.json",
                    "no_pdf": False,
                }
            ]
        )
        self._touch("inv-1.json")

        report = archive_mod.verify_archive(self.tmp)
        self.assertIn("files/inv-1.pdf", report["missing_pdf"])
        self.assertEqual(report["missing_json"], [])

    def test_no_pdf_entries_are_not_flagged_missing(self):
        self._write_manifest(
            [
                {
                    "id": "1",
                    "pdf": "files/vou-1.pdf",
                    "json": "files/vou-1.json",
                    "no_pdf": True,
                }
            ]
        )
        self._touch("vou-1.json")

        report = archive_mod.verify_archive(self.tmp)
        self.assertEqual(report["missing_pdf"], [])
        self.assertEqual(report["no_pdf_count"], 1)

    def test_detects_orphan_files(self):
        self._write_manifest([])
        self._touch("stray.pdf")
        self._touch("stray.json")

        report = archive_mod.verify_archive(self.tmp)
        self.assertIn("stray.pdf", report["orphan_pdf"])
        self.assertIn("stray.json", report["orphan_json"])

    def test_missing_manifest_reports_error(self):
        report = archive_mod.verify_archive(self.tmp)
        self.assertEqual(report["errors"], ["manifest.json not found"])

    def test_cli_consistent_exits_zero(self):
        self._write_manifest(
            [
                {
                    "id": "1",
                    "type": "Invoice",
                    "pdf": "files/inv-1.pdf",
                    "json": "files/inv-1.json",
                    "no_pdf": False,
                }
            ]
        )
        self._touch("inv-1.pdf")
        self._write_sidecar("inv-1.json", sevdesk_id="1")

        runner = CliRunner()
        result = runner.invoke(cli, ["verify", "--target", self.tmp])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Archive is consistent", result.output)

    def test_cli_inconsistent_exits_nonzero(self):
        self._write_manifest([])
        self._touch("orphan.pdf")

        runner = CliRunner()
        result = runner.invoke(cli, ["verify", "--target", self.tmp])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Orphan PDF", result.output)

    def test_cli_delete_orphans_with_yes(self):
        self._write_manifest([])
        self._touch("orphan.pdf")
        self._touch("orphan.json")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["verify", "--target", self.tmp, "--delete-orphans", "--yes"]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.files, "orphan.pdf")))
        self.assertFalse(os.path.exists(os.path.join(self.files, "orphan.json")))

    def test_cli_json_format(self):
        self._write_manifest([])
        self._touch("stray.pdf")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["verify", "--target", self.tmp, "--format", "json"]
        )
        self.assertEqual(result.exit_code, 0)
        data = json.loads(result.output)
        self.assertIn("stray.pdf", data["orphan_pdf"])

    def test_detects_unpaired_pdf(self):
        self._write_manifest([])
        self._touch("lonely.pdf")

        report = archive_mod.verify_archive(self.tmp)
        self.assertIn("lonely.pdf", report["unpaired_pdf"])

    def test_detects_unpaired_json(self):
        self._write_manifest([])
        self._write_sidecar("lonely.json", sevdesk_id="99")

        report = archive_mod.verify_archive(self.tmp)
        self.assertIn("lonely.json", report["unpaired_json"])

    def test_no_pdf_sidecar_is_not_unpaired(self):
        self._write_manifest([])
        self._write_sidecar(
            "nopdf.json",
            sevdesk_id="42",
            document={"id": "42", "_no_pdf": True},
        )

        report = archive_mod.verify_archive(self.tmp)
        self.assertEqual(report["unpaired_json"], [])

    def test_detects_malformed_sidecar(self):
        self._write_manifest([])
        self._touch("malformed.json", contents=b"not json")

        report = archive_mod.verify_archive(self.tmp)
        self.assertTrue(any("malformed.json" in e for e in report["sidecar_errors"]))

    def test_detects_sidecar_missing_required_keys(self):
        self._write_manifest([])
        with open(os.path.join(self.files, "short.json"), "w", encoding="utf-8") as f:
            json.dump({"sevdesk_id": "1"}, f)

        report = archive_mod.verify_archive(self.tmp)
        self.assertTrue(
            any("missing key" in e for e in report["sidecar_errors"]),
            report["sidecar_errors"],
        )

    def test_detects_pdf_filename_stem_mismatch(self):
        self._write_manifest([])
        self._write_sidecar("sidecar-one.json", pdf_filename="other-name.pdf")

        report = archive_mod.verify_archive(self.tmp)
        self.assertTrue(
            any("does not match" in e for e in report["sidecar_errors"]),
            report["sidecar_errors"],
        )

    def test_detects_duplicate_sevdesk_ids(self):
        self._write_manifest([])
        self._write_sidecar("a.json", sevdesk_id="same")
        self._write_sidecar("b.json", sevdesk_id="same")

        report = archive_mod.verify_archive(self.tmp)
        self.assertEqual(len(report["duplicate_sevdesk_ids"]), 1)
        dup = report["duplicate_sevdesk_ids"][0]
        self.assertEqual(dup["id"], "same")
        self.assertEqual(sorted(dup["files"]), ["a.json", "b.json"])

    def test_detects_manifest_sidecar_id_mismatch(self):
        self._write_manifest(
            [
                {
                    "id": "manifest-id",
                    "type": "Invoice",
                    "pdf": "files/inv-1.pdf",
                    "json": "files/inv-1.json",
                    "no_pdf": False,
                }
            ]
        )
        self._touch("inv-1.pdf")
        self._write_sidecar("inv-1.json", sevdesk_id="sidecar-id")

        report = archive_mod.verify_archive(self.tmp)
        mismatches = report["manifest_sidecar_mismatches"]
        id_mismatches = [m for m in mismatches if m["field"] == "id"]
        self.assertEqual(len(id_mismatches), 1)
        self.assertEqual(id_mismatches[0]["manifest"], "manifest-id")
        self.assertEqual(id_mismatches[0]["sidecar"], "sidecar-id")

    def test_detects_hash_mismatch(self):
        self._write_manifest(
            [
                {
                    "id": "1",
                    "type": "Invoice",
                    "pdf": "files/inv-1.pdf",
                    "json": "files/inv-1.json",
                    "no_pdf": False,
                }
            ]
        )
        self._touch("inv-1.pdf", contents=b"actual content")
        self._write_sidecar(
            "inv-1.json",
            sevdesk_id="1",
            pdf_hash="sha256:0" * 8 + "deadbeef" + "0" * 48,
        )

        report = archive_mod.verify_archive(self.tmp)
        self.assertEqual(len(report["hash_mismatches"]), 1)
        self.assertEqual(report["hash_verified"], 0)

    def test_hash_verifies_when_matching(self):
        import hashlib

        pdf_bytes = b"real pdf data"
        expected = f"sha256:{hashlib.sha256(pdf_bytes).hexdigest()}"
        self._write_manifest(
            [
                {
                    "id": "1",
                    "type": "Invoice",
                    "pdf": "files/inv-1.pdf",
                    "json": "files/inv-1.json",
                    "no_pdf": False,
                }
            ]
        )
        self._touch("inv-1.pdf", contents=pdf_bytes)
        self._write_sidecar("inv-1.json", sevdesk_id="1", pdf_hash=expected)

        report = archive_mod.verify_archive(self.tmp)
        self.assertEqual(report["hash_verified"], 1)
        self.assertEqual(report["hash_mismatches"], [])
        self.assertEqual(report["hash_unverified"], 0)

    def test_no_hashes_flag_skips_hashing(self):
        self._write_manifest(
            [
                {
                    "id": "1",
                    "type": "Invoice",
                    "pdf": "files/inv-1.pdf",
                    "json": "files/inv-1.json",
                    "no_pdf": False,
                }
            ]
        )
        self._touch("inv-1.pdf", contents=b"whatever")
        self._write_sidecar(
            "inv-1.json",
            sevdesk_id="1",
            pdf_hash="sha256:" + "0" * 64,
        )

        report = archive_mod.verify_archive(self.tmp, check_hashes=False)
        self.assertEqual(report["hash_mismatches"], [])
        self.assertEqual(report["hash_verified"], 0)
        self.assertEqual(report["hash_unverified"], 0)

    def test_backfill_adds_missing_hashes(self):
        self._touch("inv-1.pdf", contents=b"pdf bytes")
        self._write_sidecar("inv-1.json", sevdesk_id="1")

        result = archive_mod.backfill_sidecar_hashes(self.tmp)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped"], 0)

        with open(os.path.join(self.files, "inv-1.json"), encoding="utf-8") as f:
            meta = json.load(f)
        import hashlib

        expected = f"sha256:{hashlib.sha256(b'pdf bytes').hexdigest()}"
        self.assertEqual(meta["pdf_hash"], expected)

    def test_backfill_skips_existing_hashes(self):
        self._touch("inv-1.pdf", contents=b"pdf bytes")
        self._write_sidecar("inv-1.json", sevdesk_id="1", pdf_hash="sha256:existing")

        result = archive_mod.backfill_sidecar_hashes(self.tmp)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 1)

        with open(os.path.join(self.files, "inv-1.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["pdf_hash"], "sha256:existing")

    def test_backfill_skips_no_pdf_sidecar(self):
        self._write_sidecar(
            "nopdf.json",
            sevdesk_id="7",
            document={"id": "7", "_no_pdf": True},
        )

        result = archive_mod.backfill_sidecar_hashes(self.tmp)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["missing_pdf"], 0)


if __name__ == "__main__":
    unittest.main()
