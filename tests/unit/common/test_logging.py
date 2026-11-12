import json
import logging

from common.logging import JsonFormatter


def _make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formats_json_with_extra_fields() -> None:
    payload = json.loads(JsonFormatter().format(_make_record(run_id="abc")))
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["run_id"] == "abc"
    assert "timestamp" in payload


def test_ignores_private_and_reserved_attributes() -> None:
    payload = json.loads(JsonFormatter().format(_make_record(_private="hidden")))
    assert "_private" not in payload
    assert "lineno" not in payload
    assert "filename" not in payload
