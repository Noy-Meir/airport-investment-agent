"""
Thin Streamlit chat UI over src.agent.agent.run_turn. All formatting logic
lives in src/ui/chat_logic.py so it can be unit-tested without streamlit.
"""

import html
import time

import streamlit as st

from src.agent.config import load_config
from src.agent.respond import Session, respond
from src.cache.db import connect
from src.ui.chat_logic import (
    ALL_STARTER_QUESTIONS,
    EXTRA_STARTER_CARD,
    STARTER_CARDS,
    answer_metadata_badges,
    badge_line_to_pills,
    chip_css_class,
    data_source_chips,
    render_assistant_message_with_badge,
    speech_text,
)
from src.ui.speech_component import render_speech_controls, render_voice_assets

st.set_page_config(
    page_title="Airport Investment Intelligence Agent", page_icon="✈️", layout="centered"
)

_CSS = """
<style>
.block-container { max-width: 900px; padding-top: 2rem; }

.aiia-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  background: linear-gradient(135deg, #0B2545 0%, #0E7C86 100%);
  border-radius: 16px;
  padding: 1.5rem 2rem;
  box-shadow: 0 6px 18px rgba(11, 37, 69, 0.22);
  margin-bottom: 1.25rem;
}
.aiia-hero-text h1 {
  color: #FFFFFF;
  font-size: 1.85rem;
  margin: 0 0 0.25rem 0;
}
.aiia-hero-text p.aiia-subtitle {
  color: #DCEFF2;
  font-size: 0.95rem;
  margin: 0;
}
.aiia-hero-art { flex: none; opacity: 0.95; }
.aiia-hero-art svg { width: 96px; height: 56px; display: block; }

.aiia-chip-row { display: flex; flex-wrap: wrap; gap: 0.45rem; margin-bottom: 0.25rem; }
.aiia-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  border-radius: 999px;
  padding: 0.2rem 0.75rem;
  font-size: 0.75rem;
  border: 1px solid transparent;
}
.aiia-chip .aiia-chip-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}
.chip-bts { background: #E8F1FB; color: #1F4E8C; border-color: #C9DDF5; }
.chip-bts .aiia-chip-dot { background: #3B6EC4; }
.chip-ourairports { background: #E9F7EE; color: #1E7B34; border-color: #C9ECD3; }
.chip-ourairports .aiia-chip-dot { background: #3FA85C; }
.chip-faa { background: #FFF3E0; color: #9A5B00; border-color: #FBDFAE; }
.chip-faa .aiia-chip-dot { background: #E09A2E; }
.chip-default { background: #F0F0EE; color: #444; border-color: #E0E0DC; }
.chip-default .aiia-chip-dot { background: #999; }

.aiia-welcome { color: #0B2545; font-size: 1rem; margin-bottom: 0.75rem; }

.aiia-starter-marker { display: none; }
div[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 12px !important;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.1s ease;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.aiia-starter-marker) {
  border-color: #BFE3E6 !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.aiia-starter-marker):hover {
  border-color: #0E7C86 !important;
  box-shadow: 0 4px 10px rgba(14, 124, 134, 0.18);
  transform: translateY(-2px);
}

.aiia-pill-row { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.4rem; }
.aiia-pill {
  display: inline-block;
  border-radius: 999px;
  padding: 0.1rem 0.6rem;
  font-size: 0.72rem;
  font-weight: 500;
  border: 1px solid transparent;
}
.pill-mode-rules { background: #E8F1FB; color: #1F4E8C; border-color: #C9DDF5; }
.pill-mode-ai { background: #E3F6F7; color: #0E7C86; border-color: #BCEBEE; }
.pill-time { background: #F2F2F0; color: #555; border-color: #E0E0DC; }
.pill-confidence-high { background: #E6F4EA; color: #1E7B34; border-color: #C3E6CB; }
.pill-confidence-medium { background: #FFF4E0; color: #8A6300; border-color: #FBE2B2; }
.pill-confidence-low { background: #F0F0F0; color: #666; border-color: #DADADA; }
.pill-default { background: #F2F2F0; color: #555; border-color: #E0E0DC; }

[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
  background: #E3F3F4;
  border-radius: 12px;
  padding: 0.55rem 0.9rem;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) div[data-testid="stVerticalBlockBorderWrapper"] {
  background: #FFFFFF;
  border: 1px solid #E6EAEE !important;
  border-left: 4px solid #0E7C86 !important;
  box-shadow: 0 1px 3px rgba(11, 37, 69, 0.06);
}

[data-testid="stChatMessageContent"] table { width: 100%; }
[data-testid="stChatMessageContent"] th, [data-testid="stChatMessageContent"] td {
  padding: 0.4rem 0.6rem;
}

[data-testid="stSidebarContent"] {
  background: linear-gradient(180deg, #0B2545 0%, #0E7C86 100%);
}
[data-testid="stSidebarContent"] h1,
[data-testid="stSidebarContent"] h2,
[data-testid="stSidebarContent"] h3,
[data-testid="stSidebarContent"] p,
[data-testid="stSidebarContent"] span,
[data-testid="stSidebarContent"] label,
[data-testid="stSidebarContent"] div {
  color: #F2FAFB;
}
[data-testid="stSidebarContent"] [data-testid="stCaptionContainer"] p {
  color: #CFE9EC;
}
[data-testid="stSidebarContent"] div[data-testid="stVerticalBlockBorderWrapper"] {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.18) !important;
}
[data-testid="stSidebarContent"] hr { border-color: rgba(255, 255, 255, 0.2); }
[data-testid="stSidebarContent"] .stButton > button {
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: #F2FAFB;
}
[data-testid="stSidebarContent"] .stButton > button:hover {
  background: rgba(255, 255, 255, 0.2);
  border-color: rgba(255, 255, 255, 0.5);
}

.stButton > button {
  border-radius: 8px;
  border: 1px solid #0E7C86;
  transition: background 0.15s ease, transform 0.1s ease;
}
.stButton > button:hover {
  border-color: #0B2545;
  transform: translateY(-1px);
}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)

_HERO_SVG = """
<svg viewBox="0 0 120 70" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M6 54 C 30 54, 55 40, 112 14" fill="none" stroke="#DCEFF2"
        stroke-width="1.5" stroke-dasharray="1 7" stroke-linecap="round" opacity="0.8"/>
  <circle cx="18" cy="51" r="2" fill="#DCEFF2" opacity="0.7"/>
  <circle cx="48" cy="40" r="2" fill="#DCEFF2" opacity="0.7"/>
  <circle cx="78" cy="27" r="2" fill="#DCEFF2" opacity="0.7"/>
  <g transform="translate(96,8) rotate(-18)">
    <path d="M0 8 L24 8 L34 4 L37 6 L29 10 L37 14 L34 16 L24 12 L0 12
             L-5 18 L-9 18 L-6 12 L0 10 L-6 8 L-9 8 L-5 2 Z"
          fill="#FFFFFF"/>
  </g>
</svg>
"""

_MODE_LABELS = {
    "Auto": "auto",
    "Built-in rules (no AI)": "rules",
    "AI model": "anthropic",
}

_MODE_HELP = {
    "Auto": "Uses the AI model when a key is configured, otherwise falls back to the built-in rules.",
    "Built-in rules (no AI)": "Deterministic, no API key or cost -- answers come from a fixed interpreter.",
    "AI model": "Routes your question through the configured AI model for tool selection and narration.",
}


def _init_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "session" not in st.session_state:
        st.session_state.session = Session()
    if "pending_question" not in st.session_state:
        st.session_state.pending_question = None
    if "answer_mode" not in st.session_state:
        st.session_state.answer_mode = "Auto"


_init_state()

config = load_config()
has_api_key = config["has_api_key"]

st.markdown(
    '<div class="aiia-hero">'
    '<div class="aiia-hero-text">'
    "<h1>✈️ Airport Investment Intelligence Agent</h1>"
    '<p class="aiia-subtitle">A screening aid for US airports, built on public BTS, FAA and '
    "OurAirports data.</p>"
    "</div>"
    f'<div class="aiia-hero-art">{_HERO_SVG}</div>'
    "</div>",
    unsafe_allow_html=True,
)
chips = data_source_chips()
if chips:
    chip_html = "".join(
        f'<span class="aiia-chip {chip_css_class(chip)}">'
        f'<span class="aiia-chip-dot"></span>{html.escape(chip)}</span>'
        for chip in chips
    )
    st.markdown(f'<div class="aiia-chip-row">{chip_html}</div>', unsafe_allow_html=True)

with st.sidebar:
    st.subheader("Settings")

    mode_options = ["Auto", "Built-in rules (no AI)"] + (["AI model"] if has_api_key else [])
    if st.session_state.answer_mode not in mode_options:
        st.session_state.answer_mode = "Auto"
    st.radio("Answer mode", mode_options, key="answer_mode")
    st.caption(_MODE_HELP[st.session_state.answer_mode])
    if not has_api_key:
        st.caption("No API key configured")

    if st.session_state.answer_mode == "Built-in rules (no AI)":
        active_caption = "Active: built-in rules interpreter"
    elif st.session_state.answer_mode == "AI model":
        active_caption = f"Active: AI model ({config['model_name']})"
    elif has_api_key:
        active_caption = f"Active: AI model ({config['model_name']})"
    else:
        active_caption = "Active: built-in rules interpreter"
    st.caption(active_caption)

    st.divider()
    if st.button("New conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.session = Session()
        st.session_state.pending_question = None
        st.rerun()

    st.divider()
    with st.expander("About"):
        st.caption(
            "This is a screening aid, not investment advice. "
            "See DESIGN.md for methodology and data-source details."
        )

render_voice_assets()


def _render_badge_pills(badge_line):
    pills = badge_line_to_pills(badge_line)
    if not pills:
        return
    pill_html = "".join(
        f'<span class="aiia-pill {pill["css_class"]}">{html.escape(pill["text"])}</span>'
        for pill in pills
    )
    st.markdown(f'<div class="aiia-pill-row">{pill_html}</div>', unsafe_allow_html=True)


def _render_starter_card(col, card):
    with col:
        with st.container(border=True):
            st.markdown('<span class="aiia-starter-marker"></span>', unsafe_allow_html=True)
            st.markdown(f"**{card['icon']} {card['title']}**")
            st.caption(card["description"])
            if st.button("Ask", key=f"starter::{card['question']}", use_container_width=True):
                st.session_state.pending_question = card["question"]


has_messages = bool(st.session_state.messages)

if not has_messages:
    st.markdown(
        '<p class="aiia-welcome">Welcome! Ask a question about airport investment '
        "opportunities, or try one of these:</p>",
        unsafe_allow_html=True,
    )
    row1 = st.columns(2)
    row2 = st.columns(2)
    for col, card in zip(row1 + row2, STARTER_CARDS):
        _render_starter_card(col, card)
    small_col = st.columns([1, 1])[0]
    _render_starter_card(small_col, EXTRA_STARTER_CARD)
else:
    with st.expander("Example questions"):
        for question in ALL_STARTER_QUESTIONS:
            if st.button(question, key=f"example::{question}", use_container_width=True):
                st.session_state.pending_question = question

for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            rendered = render_assistant_message_with_badge(message)
            with st.container(border=True):
                if rendered.get("badge_line"):
                    _render_badge_pills(rendered["badge_line"])
                st.markdown(rendered["content"])
                render_speech_controls(speech_text(rendered["content"]), key=f"speech-{idx}")
        else:
            st.markdown(message["content"])

user_text = st.chat_input("Ask about airport investment opportunities...")
if not user_text and st.session_state.pending_question:
    user_text = st.session_state.pending_question
st.session_state.pending_question = None

if user_text:
    st.session_state.messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    with st.chat_message("assistant"):
        with st.spinner("Analyzing airport data..."):
            start_time = time.perf_counter()
            conn = connect()
            try:
                provider_override = _MODE_LABELS[st.session_state.answer_mode]
                outcome = respond(
                    st.session_state.session, user_text, conn=conn, provider_override=provider_override
                )
            finally:
                conn.close()
            elapsed = time.perf_counter() - start_time

        badges = answer_metadata_badges(outcome["mode"], elapsed, outcome["trace"])
        with st.container(border=True):
            _render_badge_pills(badges)
            new_key = f"speech-{len(st.session_state.messages)}"
            st.markdown(outcome["answer"])
            render_speech_controls(speech_text(outcome["answer"]), key=new_key)
        st.session_state.messages.append({
            "role": "assistant",
            "content": outcome["answer"],
            "badge_line": badges,
        })
        st.rerun()
