import logging
import os
import shutil
import stat
import unittest
from datetime import date

from sevdesk_archiver.utils import (
    SecureRotatingFileHandler,
    format_date,
    sanitize_filename,
)


class TestFormatDate(unittest.TestCase):
    def test_none(self):
        self.assertEqual(format_date(None), "")

    def test_datetime_object(self):
        self.assertEqual(format_date(date(2026, 2, 3)), "2026-02-03")

    def test_iso_string_is_truncated(self):
        self.assertEqual(format_date("2026-02-03T00:00:00+01:00"), "2026-02-03")

    def test_non_date_string_passes_through(self):
        self.assertEqual(format_date("garbage"), "garbage")


class TestSanitizeFilename(unittest.TestCase):
    def test_strips_separators_and_control_chars(self):
        self.assertEqual(sanitize_filename("../a/b\\c\x00.pdf"), "_a_b_c.pdf")

    def test_empty_falls_back(self):
        self.assertEqual(sanitize_filename(""), "unknown_file")
        self.assertEqual(sanitize_filename(" . "), "unknown_file")


class TestSecureRotatingFileHandler(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(os.path.dirname(__file__), "_tmp_utils")
        os.makedirs(self.tmp, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_log_file_is_owner_only(self):
        path = os.path.join(self.tmp, "a.log")
        handler = SecureRotatingFileHandler(path, maxBytes=1024, backupCount=1)
        try:
            handler.emit(logging.makeLogRecord({"msg": "hello"}))
        finally:
            handler.close()
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
