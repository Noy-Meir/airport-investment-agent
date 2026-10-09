from src.agent.pricing import estimate_cost
from src.ui.chat_logic import (
    SAMPLE_QUESTIONS,
    SPEECH_TEXT_CAP,
    error_to_message,
    session_usage,
    speech_text,
)


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
