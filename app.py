"""
Thin Streamlit chat UI over src.agent.agent.run_turn. All formatting logic
lives in src/ui/chat_logic.py so it can be unit-tested without streamlit.
"""

import streamlit as st

from src.agent.config import load_config
from src.agent.respond import Session, respond
from src.cache.db import connect
from src.ui.chat_logic import (
    SAMPLE_QUESTIONS,
    speech_text,
)
from src.ui.speech_component import render_speech_controls, render_voice_assets

st.set_page_config(page_title="Airport Investment Intelligence Agent", page_icon="✈️")
st.title("Airport Investment Intelligence Agent")
st.caption("A screening aid for US airports, built on public BTS and OurAirports data.")

_MODE_LABELS = {
    "Auto": "auto",
    "Built-in rules (no AI)": "rules",
    "AI model": "anthropic",
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

with st.sidebar:
    st.subheader("Sample questions")
    for question in SAMPLE_QUESTIONS:
        if st.button(question, key=f"sample::{question}", use_container_width=True):
            st.session_state.pending_question = question

    st.divider()
    if st.button("New conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.session = Session()
        st.session_state.pending_question = None
        st.rerun()

    st.divider()
    mode_options = ["Auto", "Built-in rules (no AI)"] + (["AI model"] if has_api_key else [])
    if st.session_state.answer_mode not in mode_options:
        st.session_state.answer_mode = "Auto"
    st.radio("Answer mode", mode_options, key="answer_mode")
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

render_voice_assets()


for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_speech_controls(speech_text(message["content"]), key=f"speech-{idx}")

user_text = st.chat_input("Ask about airport investment opportunities...")
if not user_text and st.session_state.pending_question:
    user_text = st.session_state.pending_question
st.session_state.pending_question = None

if user_text:
    st.session_state.messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    with st.chat_message("assistant"):
        with st.spinner("Working..."):
            conn = connect()
            try:
                provider_override = _MODE_LABELS[st.session_state.answer_mode]
                outcome = respond(
                    st.session_state.session, user_text, conn=conn, provider_override=provider_override
                )
            finally:
                conn.close()

        new_key = f"speech-{len(st.session_state.messages)}"
        st.markdown(outcome["answer"])
        render_speech_controls(speech_text(outcome["answer"]), key=new_key)
        st.session_state.messages.append({"role": "assistant", "content": outcome["answer"]})
