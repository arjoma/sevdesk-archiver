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
                    "pdf": "files/inv-1.pdf",
                    "json": "files/inv-1.json",
                    "no_pdf": False,
                }
            ]
        )
        self._touch("inv-1.pdf")
        self._touch("inv-1.json")

        runner = CliRunner()
        result = runner.invoke(cli, ["verify", "--target", self.tmp])
        self.assertEqual(result.exit_code, 0)
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


if __name__ == "__main__":
    unittest.main()
