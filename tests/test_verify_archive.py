import json
import os
import shutil
import unittest
from unittest.mock import patch

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
        self.assertEqual(result.exit_code, 1)
        data = json.loads(result.output)
        self.assertIn("stray.pdf", data["orphan_pdf"])
        self.assertEqual(data["issue_count"], 2)  # orphan + unpaired

    def test_cli_json_format_consistent_exits_zero(self):
        self._write_manifest([])

        result = CliRunner().invoke(
            cli, ["verify", "--target", self.tmp, "--format", "json"]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)["issue_count"], 0)

    def test_cli_json_format_missing_manifest_exits_nonzero(self):
        result = CliRunner().invoke(
            cli, ["verify", "--target", self.tmp, "--format", "json"]
        )
        self.assertEqual(result.exit_code, 1)

    def test_valid_sidecar_missing_from_manifest_is_unindexed(self):
        """A run killed before write_manifest leaves valid pairs that the
        manifest doesn't list — they must be reported, not deleted."""
        self._write_manifest([])
        self._write_sidecar("inv-1.json", sevdesk_id="1")
        self._touch("inv-1.pdf")
        self._touch("junk.pdf")

        report = archive_mod.verify_archive(self.tmp)
        self.assertEqual(report["unindexed_sidecars"], ["inv-1.json"])
        self.assertEqual(archive_mod.deletable_orphans(report), ["junk.pdf"])

        result = CliRunner().invoke(
            cli, ["verify", "--target", self.tmp, "--delete-orphans", "--yes"]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Re-run `archive`", result.output)
        self.assertTrue(os.path.exists(os.path.join(self.files, "inv-1.json")))
        self.assertTrue(os.path.exists(os.path.join(self.files, "inv-1.pdf")))
        self.assertFalse(os.path.exists(os.path.join(self.files, "junk.pdf")))

    def test_count_issues(self):
        self._write_manifest([{"id": "9", "pdf": "files/gone.pdf", "json": "files/gone.json"}])
        self._touch("stray.pdf")
        report = archive_mod.verify_archive(self.tmp)
        # missing_pdf + missing_json + orphan_pdf + unpaired_pdf
        self.assertEqual(archive_mod.count_issues(report), 4)

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

    def test_cli_archive_accepts_api_token_flag(self):
        """--api-token bypasses the SEVDESK_API_TOKEN env requirement."""
        runner = CliRunner()
        with patch("sevdesk_archiver.cli.SevDeskClient") as MockClient:
            MockClient.return_value.get_invoices.return_value = []
            MockClient.return_value.get_credit_notes.return_value = []
            MockClient.return_value.get_vouchers.return_value = []
            env = {k: v for k, v in os.environ.items() if k != "SEVDESK_API_TOKEN"}
            with patch.dict(os.environ, env, clear=True), patch(
                "sevdesk_archiver.cli.find_dotenv", return_value=""
            ):
                result = runner.invoke(
                    cli,
                    [
                        "archive",
                        "--target",
                        self.tmp,
                        "--api-token",
                        "test-token",
                        "--after",
                        "2026-02-01",
                        "--end",
                        "2026-02-28",
                        "--dry-run",
                    ],
                )
        self.assertEqual(result.exit_code, 0, result.output)
        MockClient.assert_called_once_with(api_token="test-token")

    def test_cli_backfill_with_yes(self):
        self._write_sidecar("inv-1.json", sevdesk_id="1")
        self._touch("inv-1.pdf", b"%PDF")
        self._write_manifest(
            [{"id": "1", "type": "Invoice", "pdf": "files/inv-1.pdf", "json": "files/inv-1.json"}]
        )

        result = CliRunner().invoke(
            cli, ["verify", "--target", self.tmp, "--backfill-hashes", "--yes"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("updated=1", result.output)
        self.assertIn("1 verified", result.output)

    def test_cli_backfill_cancelled_without_confirmation(self):
        self._write_manifest([])
        result = CliRunner().invoke(
            cli, ["verify", "--target", self.tmp, "--backfill-hashes"], input="no\n"
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cancelled", result.output)

    def test_cli_archive_exits_nonzero_on_error_events(self):
        runner = CliRunner()
        with patch("sevdesk_archiver.cli.SevDeskClient") as MockClient:
            MockClient.return_value.get_invoices.side_effect = RuntimeError("down")
            MockClient.return_value.get_credit_notes.return_value = []
            result = runner.invoke(
                cli,
                [
                    "archive", "--target", self.tmp, "--api-token", "t",
                    "--after", "2026-02-01", "--end", "2026-02-28",
                ],
            )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Fetch failed", result.output)

    def test_cli_serve_rejects_missing_dir(self):
        result = CliRunner().invoke(
            cli, ["serve", "--target", os.path.join(self.tmp, "nope")]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("is not a directory", result.output)

    def _serve(self, *args):
        with patch(
            "socketserver.BaseServer.serve_forever", side_effect=KeyboardInterrupt
        ):
            return CliRunner().invoke(
                cli, ["serve", "--target", self.tmp, "--no-browser", "--port", "0", *args]
            )

    def test_cli_serve_rejects_invalid_port(self):
        result = self._serve()
        self.assertEqual(result.exit_code, 2)

    def test_cli_serve_warns_when_exposed(self):
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = str(s.getsockname()[1])
        with patch(
            "socketserver.BaseServer.serve_forever", side_effect=KeyboardInterrupt
        ):
            local = CliRunner().invoke(
                cli, ["serve", "--target", self.tmp, "--no-browser", "--port", port]
            )
            exposed = CliRunner().invoke(
                cli,
                ["serve", "--target", self.tmp, "--no-browser", "--port", port,
                 "--host", "0.0.0.0"],
            )
        self.assertEqual(local.exit_code, 0, local.output)
        self.assertNotIn("WARNING", local.output)
        self.assertEqual(exposed.exit_code, 0, exposed.output)
        self.assertIn("WARNING", exposed.output)
        self.assertIn(f"http://127.0.0.1:{port}/index.html", exposed.output)

    def test_standalone_serve_py_rejects_invalid_port(self):
        import subprocess
        import sys

        script = os.path.join(
            os.path.dirname(archive_mod.__file__), "templates", "serve.py"
        )
        proc = subprocess.run(
            [sys.executable, script, "notaport"], capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Invalid port", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_duplicate_ids_are_per_type(self):
        self._write_sidecar("a.json", sevdesk_id="1", doc_type="Invoice")
        self._write_sidecar("b.json", sevdesk_id="1", doc_type="Voucher")
        self._write_sidecar("c.json", sevdesk_id="1", doc_type="Invoice")
        self._write_manifest([])

        dups = archive_mod.verify_archive(self.tmp)["duplicate_sevdesk_ids"]
        self.assertEqual(dups, [{"id": "1", "type": "Invoice", "files": ["a.json", "c.json"]}])

    def test_cli_archive_errors_without_token(self):
        runner = CliRunner()
        env = {k: v for k, v in os.environ.items() if k != "SEVDESK_API_TOKEN"}
        with patch.dict(os.environ, env, clear=True), patch(
            "sevdesk_archiver.cli.find_dotenv", return_value=""
        ):
            result = runner.invoke(cli, ["archive", "--target", self.tmp])
        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn("SEVDESK_API_TOKEN", result.output)

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
