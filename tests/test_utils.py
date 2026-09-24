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


class TestFormatDateStrict(unittest.TestCase):
    def test_longer_digit_run_is_not_a_date(self):
        self.assertEqual(format_date("2024-01-0001"), "2024-01-0001")

    def test_date_followed_by_space_is_truncated(self):
        self.assertEqual(format_date("2024-01-05 10:00"), "2024-01-05")


class TestRetrySessionMethods(unittest.TestCase):
    def test_post_is_not_retried_by_default(self):
        from sevdesk_archiver.utils import create_retry_session

        retry = create_retry_session().get_adapter("https://x").max_retries
        self.assertNotIn("POST", retry.allowed_methods)
        self.assertIn("GET", retry.allowed_methods)

    def test_allowed_methods_override(self):
        from sevdesk_archiver.utils import create_retry_session

        retry = create_retry_session(allowed_methods=["POST"]).get_adapter(
            "https://x"
        ).max_retries
        self.assertEqual(list(retry.allowed_methods), ["POST"])


class TestParseRetryAfterAnyResponse(unittest.TestCase):
    class _Resp:
        def __init__(self, value):
            self.headers = {"Retry-After": value}

    def test_duck_typed_response(self):
        from sevdesk_archiver.utils import parse_retry_after

        self.assertEqual(parse_retry_after(self._Resp("12")), 12)

    def test_http_date(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime

        from sevdesk_archiver.utils import parse_retry_after

        when = datetime.now(timezone.utc) + timedelta(seconds=120)
        secs = parse_retry_after(self._Resp(format_datetime(when, usegmt=True)))
        self.assertIsNotNone(secs)
        self.assertTrue(100 <= secs <= 120, secs)

    def test_past_http_date_is_zero(self):
        from sevdesk_archiver.utils import parse_retry_after

        self.assertEqual(
            parse_retry_after(self._Resp("Wed, 21 Oct 2015 07:28:00 GMT")), 0
        )
