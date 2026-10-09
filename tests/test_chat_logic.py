from src.agent.pricing import estimate_cost
from src.ui.chat_logic import (
    SAMPLE_QUESTIONS,
    SPEECH_LANG_OPTIONS,
    SPEECH_TEXT_CAP,
    detect_speech_lang,
    error_to_message,
    format_trace,
    session_usage,
    speech_lang_code,
    speech_text,
    summarize_envelopes,
)


def test_speech_lang_code_known_labels():
    assert speech_lang_code("English") == "en-US"
    assert speech_lang_code("Hebrew") == "he-IL"


def test_speech_lang_code_unknown_label_falls_back_to_english():
    assert speech_lang_code("Klingon") == "en-US"
    assert speech_lang_code(None) == "en-US"


def test_speech_lang_options_cover_english_and_hebrew():
    labels = [label for label, _code in SPEECH_LANG_OPTIONS]
    assert labels == ["English", "Hebrew"]


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


def _envelope_result(method, caveats, source, confidence):
    return {"result": {}, "method": method, "caveats": caveats, "source": source, "confidence": confidence}


def test_summarize_envelopes_dedupes_sources_and_caveats():
    trace = [
        {
            "tool": "get_airport_traffic",
            "args": {"airport": "SFO"},
            "result": _envelope_result(
                "TTM totals ending 2024-06", ["shared caveat", "traffic-only caveat"], "BTS T-100", "high",
            ),
        },
        {
            "tool": "score_airport",
            "args": {"airport": "SFO"},
            "result": _envelope_result(
                "composite score ending 2024-06", ["shared caveat"], "BTS T-100", "medium",
            ),
        },
    ]
    summary = summarize_envelopes(trace)
    assert summary["sources"] == ["BTS T-100"]
    assert summary["caveats"] == ["shared caveat", "traffic-only caveat"]
    assert summary["confidence_by_tool"] == {"get_airport_traffic": "high", "score_airport": "medium"}
    assert summary["methods_by_tool"] == {
        "get_airport_traffic": "TTM totals ending 2024-06",
        "score_airport": "composite score ending 2024-06",
    }
    assert summary["as_of_windows"] == ["ending 2024-06"]
    assert summary["caveats_truncated"] is False
    assert summary["failed_tools"] == []


def test_summarize_envelopes_caps_caveats_and_flags_truncation():
    trace = [
        {
            "tool": "t",
            "args": {},
            "result": _envelope_result("m", [f"caveat {i}" for i in range(15)], "src", "low"),
        },
    ]
    summary = summarize_envelopes(trace)
    assert len(summary["caveats"]) == 12
    assert summary["caveats"] == [f"caveat {i}" for i in range(12)]
    assert summary["caveats_truncated"] is True


def test_summarize_envelopes_lists_error_result_as_failed_tool():
    trace = [
        {"tool": "good_tool", "args": {}, "result": _envelope_result("m", [], "src", "high")},
        {"tool": "bad_tool", "args": {}, "result": {"error": {"type": "ValueError", "message": "boom"}}},
    ]
    summary = summarize_envelopes(trace)
    assert summary["failed_tools"] == ["bad_tool"]
    assert "bad_tool" not in summary["confidence_by_tool"]
    assert summary["sources"] == ["src"]


def test_summarize_envelopes_empty_trace():
    summary = summarize_envelopes([])
    assert summary == {
        "as_of_windows": [],
        "sources": [],
        "confidence_by_tool": {},
        "methods_by_tool": {},
        "caveats": [],
        "caveats_truncated": False,
        "failed_tools": [],
    }


def test_summarize_envelopes_finds_as_of_nested_in_compare_congestion():
    trace = [
        {
            "tool": "compare_congestion",
            "args": {"airports": ["LAX", "SNA"]},
            "result": {
                "result": {
                    "airports": [
                        {"airport": "LAX", "flights": 1000, "delayed_share_pct": 20.0, "as_of": "2024-06"},
                        {"airport": "SNA", "flights": 500, "delayed_share_pct": 10.0, "as_of": "2024-06"},
                    ]
                },
                "method": "TTM flights and delayed share per airport",
                "caveats": [],
                "source": "BTS OTP",
                "confidence": "high",
            },
        },
    ]
    summary = summarize_envelopes(trace)
    assert summary["as_of_windows"] == ["2024-06"]


def test_summarize_envelopes_finds_as_of_in_rank_airports_style_result():
    trace = [
        {
            "tool": "rank_airports",
            "args": {"region": "New England"},
            "result": {
                "result": {
                    "eligible_codes": ["BOS", "PVD"],
                    "as_of": "2024-05",
                    "count": 2,
                },
                "method": "composite score ranking",
                "caveats": [],
                "source": "BTS T-100",
                "confidence": "medium",
            },
        },
        {
            "tool": "get_airport_traffic",
            "args": {"airport": "SFO"},
            "result": {
                "result": {
                    "growth_pct": 3.2,
                    "current_as_of": "2024-06",
                    "prior_as_of": "2023-06",
                },
                "method": "TTM growth",
                "caveats": [],
                "source": "BTS T-100",
                "confidence": "high",
            },
        },
    ]
    summary = summarize_envelopes(trace)
    assert summary["as_of_windows"] == ["2024-05", "2024-06", "2023-06"]


def test_summarize_envelopes_no_as_of_found_anywhere():
    trace = [
        {
            "tool": "get_buildability",
            "args": {"airport": "LAX"},
            "result": {
                "result": {"slot_constrained": True},
                "method": "curated buildability lookup",
                "caveats": [],
                "source": "curated",
                "confidence": "high",
            },
        },
    ]
    summary = summarize_envelopes(trace)
    assert summary["as_of_windows"] == []


def test_session_usage_sums_turns_and_matches_estimate_cost():
    turns = [
        {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 0},
        {"input_tokens": 200, "output_tokens": 25, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 5},
    ]
    totals = session_usage(turns)
    assert totals["input_tokens"] == 300
    assert totals["output_tokens"] == 75
    assert totals["cache_read_input_tokens"] == 10
    assert totals["cache_creation_input_tokens"] == 5
    expected_cost = estimate_cost({
        "input_tokens": 300, "output_tokens": 75,
        "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5,
    })
    assert totals["estimated_cost_usd"] == round(expected_cost, 6)
    assert "cost_note" in totals


def test_session_usage_empty_turns():
    totals = session_usage([])
    assert totals["input_tokens"] == 0
    assert totals["output_tokens"] == 0
    assert totals["cache_read_input_tokens"] == 0
    assert totals["cache_creation_input_tokens"] == 0
    assert totals["estimated_cost_usd"] == 0


def test_speech_text_removes_markdown_syntax():
    markdown = "# Header\n\n**Bold claim** about _SFO_ with a [link](https://example.com)."
    text = speech_text(markdown)
    assert "#" not in text
    assert "*" not in text
    assert "_" not in text
    assert "[" not in text and "](" not in text
    assert "Header" in text
    assert "Bold claim" in text
    assert "SFO" in text
    assert "link" in text


def test_speech_text_drops_tables():
    markdown = (
        "Here is a summary:\n\n"
        "| Airport | Score |\n"
        "| --- | --- |\n"
        "| SFO | 8.1 |\n"
        "| LAX | 7.4 |\n\n"
        "That's the comparison."
    )
    text = speech_text(markdown)
    assert "|" not in text
    assert "SFO" not in text
    assert "8.1" not in text
    assert "Here is a summary" in text
    assert "That's the comparison" in text


def test_speech_text_caps_length_at_sentence_boundary():
    sentence = "This is a filler sentence about airport capacity. "
    markdown = sentence * 40
    text = speech_text(markdown)
    assert len(text) <= SPEECH_TEXT_CAP
    assert text.endswith(".")


def test_speech_text_preserves_hebrew():
    markdown = "שדה התעופה בן גוריון הוא השדה המרכזי בישראל."
    text = speech_text(markdown)
    assert "שדה התעופה" in text


def test_speech_text_empty():
    assert speech_text("") == ""
    assert speech_text(None) == ""


def test_detect_speech_lang_hebrew():
    assert detect_speech_lang("שלום עולם") == "he-IL"


def test_detect_speech_lang_english():
    assert detect_speech_lang("Hello world") == "en-US"


def test_detect_speech_lang_mixed_defaults_hebrew():
    assert detect_speech_lang("SFO is שדה תעופה") == "he-IL"


def test_detect_speech_lang_empty():
    assert detect_speech_lang("") == "en-US"
