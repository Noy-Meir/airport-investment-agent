"""
Thin Streamlit chat UI over src.agent.agent.run_turn. All formatting logic
lives in src/ui/chat_logic.py so it can be unit-tested without streamlit.
"""

import streamlit as st

from src.agent.agent import run_turn
from src.agent.config import ConfigError, load_config
from src.cache.db import connect
from src.ui.chat_logic import (
    SAMPLE_QUESTIONS,
    error_to_message,
    speech_text,
)
from src.ui.speech_component import render_speech_controls, render_voice_assets

st.set_page_config(page_title="Airport Investment Intelligence Agent", page_icon="✈️")
st.title("Airport Investment Intelligence Agent")
st.caption("A screening aid for US airports, built on public BTS and OurAirports data.")


def _init_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "agent_history" not in st.session_state:
        st.session_state.agent_history = []
    if "pending_question" not in st.session_state:
        st.session_state.pending_question = None


_init_state()

try:
    config = load_config()
    config_error = None
except ConfigError as e:
    config = None
    config_error = str(e)

with st.sidebar:
    st.subheader("Sample questions")
    for question in SAMPLE_QUESTIONS:
        if st.button(question, key=f"sample::{question}", use_container_width=True):
            st.session_state.pending_question = question

    st.divider()
    if st.button("New conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.agent_history = []
        st.session_state.pending_question = None
        st.rerun()

    st.divider()
    st.caption(f"Model: {config['model_name']}" if config else "Model: (not configured)")

if config_error:
    st.error(
        "Agent isn't configured yet: "
        f"{config_error} Set ANTHROPIC_API_KEY and MODEL_NAME (shell env or .env) and reload."
    )
    st.stop()

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
                outcome = run_turn(st.session_state.agent_history, user_text, conn=conn)
            finally:
                conn.close()

        new_key = f"speech-{len(st.session_state.messages)}"
        if outcome.get("error"):
            readable = error_to_message(outcome["error"])
            st.error(readable)
            render_speech_controls(speech_text(readable), key=new_key)
            st.session_state.messages.append({"role": "assistant", "content": readable})
        else:
            st.markdown(outcome["answer"])
            st.session_state.agent_history = outcome["history"]
            render_speech_controls(speech_text(outcome["answer"]), key=new_key)
            st.session_state.messages.append({"role": "assistant", "content": outcome["answer"]})
