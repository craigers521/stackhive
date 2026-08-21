"""Structured JSON logging (plan audit + logging NFR)."""
import json
import logging
import sys

from app import JsonLogFormatter


def _record(message, level=logging.INFO, name="app.audit", exc_info=None):
    """Build a minimal LogRecord for formatter tests."""
    return logging.LogRecord(name, level, __file__, 1, message, None, exc_info)


class TestJsonLogFormatter:
    """JSON formatter output shape and the app log handler."""
    def test_record_becomes_single_line_json(self):
        """Records format to single-line JSON with ts, level, logger, message."""
        formatter = JsonLogFormatter()
        record = logging.LogRecord(
            "app.audit", logging.INFO, __file__, 1, "api_login_success user=%s ip=%s", ("admin", "10.0.0.1"), None
        )
        payload = json.loads(formatter.format(record))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.audit"
        assert payload["message"] == "api_login_success user=admin ip=10.0.0.1"
        assert payload["ts"].endswith("Z")
        assert "exception" not in payload

    def test_exception_is_structured_field(self):
        """Exceptions land in a dedicated exception field."""
        formatter = JsonLogFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = _record("failed", level=logging.ERROR, name="app", exc_info=sys.exc_info())
        payload = json.loads(formatter.format(record))
        assert "ValueError: boom" in payload["exception"]

    def test_app_handler_emits_json_to_stdout(self, app, capfd):
        # point the app logger's stdout handler at the live stdout for the capture
        """The app's stdout handler emits the JSON line."""
        for handler in logging.getLogger("app").handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.stream = sys.stdout
        capfd.readouterr()  # discard anything captured before this test
        logging.getLogger("app").info("json_probe")
        out = capfd.readouterr().out
        lines = [l for l in out.splitlines() if "json_probe" in l]
        assert lines, "no JSON log line captured on stdout"
        payload = json.loads(lines[0])
        assert payload["message"] == "json_probe"
        assert payload["logger"] == "app"
