from src.ui.chat_logic import SAMPLE_QUESTIONS, error_to_message, format_trace


def test_format_trace_shape():
    trace = [
        {"tool": "score_airport", "args": {"airport": "SFO"}, "result": {"result": {}}, "ms": 12.345},
    ]
    formatted = format_trace(trace)
    assert formatted == [
        {"tool": "score_airport", "args_text": '{"airport": "SFO"}', "ms": 12.3, "result": {"result": {}}}
    ]


def test_format_trace_empty():
    assert format_trace([]) == []


def test_format_trace_multiple_entries_preserve_order():
    trace = [
        {"tool": "a", "args": {}, "result": {}, "ms": 1.0},
        {"tool": "b", "args": {"x": 1}, "result": {}, "ms": 2.0},
    ]
    formatted = format_trace(trace)
    assert [f["tool"] for f in formatted] == ["a", "b"]


def test_sample_questions_present():
    assert len(SAMPLE_QUESTIONS) == 4
    assert all(isinstance(q, str) and q for q in SAMPLE_QUESTIONS)


def test_error_to_message_known_categories():
    for category in ("auth", "bad_request", "rate_limit", "timeout", "connection"):
        msg = error_to_message({"type": category, "status_code": None, "message": "raw detail"})
        assert isinstance(msg, str) and msg
        assert "raw detail" not in msg


def test_error_to_message_unknown_category_falls_back():
    msg = error_to_message({"type": "something_new", "status_code": None, "message": "x"})
    assert msg == error_to_message({"type": "other", "status_code": None, "message": "x"})


def test_error_to_message_none():
    assert isinstance(error_to_message(None), str)
