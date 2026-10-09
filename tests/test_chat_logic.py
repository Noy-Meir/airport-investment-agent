from src.ui.chat_logic import (
    SAMPLE_QUESTIONS,
    SPEECH_TEXT_CAP,
    _format_vintage_for_caption,
    answer_metadata_badges,
    data_sources_caption,
    error_to_message,
    render_assistant_message_with_badge,
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


def test_data_sources_caption_present():
    caption = data_sources_caption()
    assert isinstance(caption, str)
    assert "Data:" in caption
    assert "BTS T-100" in caption
    assert "FAA" in caption
    assert "OurAirports" in caption


def test_format_vintage_with_range():
    vintage = "monthly, 2023-01..2026-04"
    result = _format_vintage_for_caption("BTS T-100", vintage)
    assert result == "BTS T-100 (monthly, through 2026-04)"


def test_format_vintage_without_range():
    vintage = "CY2025"
    result = _format_vintage_for_caption("BTS Route", vintage)
    assert result == "BTS Route (CY2025)"


def test_format_vintage_faa_special_case():
    vintage = "TAF 2025 final"
    result = _format_vintage_for_caption("FAA", vintage, is_faa=True)
    assert result == "FAA Terminal Area Forecast (actuals through 2024)"


def test_format_vintage_missing_data():
    result = _format_vintage_for_caption("Test Source", "")
    assert result == "Test Source"


def test_answer_metadata_badges_rules_mode_with_confidence():
    trace = [{"result": {"confidence": "high"}}]
    badges = answer_metadata_badges("rules", 2.345, trace)
    assert "rules-based" in badges
    assert "2.3 s" in badges
    assert "confidence: high" in badges
    assert " · " in badges


def test_answer_metadata_badges_ai_mode_without_confidence():
    trace = [{"result": {}}]
    badges = answer_metadata_badges("llm", 1.234, trace)
    assert "AI model" in badges
    assert "1.2 s" in badges
    assert "confidence" not in badges
    assert " · " in badges


def test_answer_metadata_badges_multiple_envelopes_lowest_confidence():
    trace = [
        {"result": {"confidence": "high"}},
        {"result": {"confidence": "low"}},
        {"result": {"confidence": "medium"}},
    ]
    badges = answer_metadata_badges("rules", 3.5, trace)
    assert "confidence: low" in badges


def test_answer_metadata_badges_empty_trace():
    badges = answer_metadata_badges("rules", 0.5, [])
    assert "rules-based" in badges
    assert "0.5 s" in badges
    assert "confidence" not in badges


def test_render_assistant_message_with_badge_present():
    message = {
        "role": "assistant",
        "content": "Here is the answer.",
        "badge_line": "rules-based · 2.3 s · confidence: high",
    }
    rendered = render_assistant_message_with_badge(message)
    assert rendered["content"] == "Here is the answer."
    assert rendered["badge_line"] == "rules-based · 2.3 s · confidence: high"


def test_render_assistant_message_without_badge():
    message = {
        "role": "assistant",
        "content": "Here is the answer.",
    }
    rendered = render_assistant_message_with_badge(message)
    assert rendered["content"] == "Here is the answer."
    assert rendered.get("badge_line") is None
