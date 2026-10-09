"""
Thin Streamlit chat UI over src.agent.agent.run_turn. All formatting logic
lives in src/ui/chat_logic.py so it can be unit-tested without streamlit.
"""

import streamlit as st

from src.agent.agent import run_turn
from src.agent.config import ConfigError, load_config
from src.cache.db import connect
from src.ui.chat_logic import SAMPLE_QUESTIONS, error_to_message, format_trace

st.set_page_config(page_title="Airport Investment Intelligence Agent", page_icon="✈️")
st.title("Airport Investment Intelligence Agent")
st.caption("A screening aid for US airports, built on public BTS/FAA/OurAirports data.")


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

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant" and message.get("trace"):
            with st.expander("Tools used"):
                for call in format_trace(message["trace"]):
                    st.markdown(f"**{call['tool']}** ({call['ms']} ms) — `{call['args_text']}`")
                    with st.expander("Raw result", expanded=False):
                        st.json(call["result"])

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

        if outcome.get("error"):
            readable = error_to_message(outcome["error"])
            st.error(readable)
            st.session_state.messages.append({"role": "assistant", "content": readable, "trace": outcome.get("trace", [])})
        else:
            st.markdown(outcome["answer"])
            st.session_state.agent_history = outcome["history"]
            if outcome.get("trace"):
                with st.expander("Tools used"):
                    for call in format_trace(outcome["trace"]):
                        st.markdown(f"**{call['tool']}** ({call['ms']} ms) — `{call['args_text']}`")
                        with st.expander("Raw result", expanded=False):
                            st.json(call["result"])
            st.session_state.messages.append(
                {"role": "assistant", "content": outcome["answer"], "trace": outcome.get("trace", [])}
            )
