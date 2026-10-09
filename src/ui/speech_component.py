"""
Streamlit component rendering "Read aloud" / "Stop" buttons backed by the
browser's Web Speech API (speechSynthesis). No external service, no API key.
"""

import json

import streamlit.components.v1 as components

_COMPONENT_HEIGHT = 50


def render_speech_controls(text, lang, key):
    """
    Renders a compact HTML component with "Read aloud" and "Stop" buttons.
    `text` and `lang` are json-encoded, never string-concatenated into the
    script. Clicking "Read aloud" cancels any ongoing speech then speaks;
    "Stop" cancels. Falls back to a short message if speechSynthesis is
    unavailable. `key` must be unique per message.
    """
    text_json = json.dumps(text or "")
    lang_json = json.dumps(lang or "en-US")

    html = f"""
    <div style="font-family: sans-serif;">
      <button id="read-btn-{key}">Read aloud</button>
      <button id="stop-btn-{key}">Stop</button>
      <span id="status-{key}" style="margin-left: 8px; font-size: 0.85em; color: #888;"></span>
    </div>
    <script>
      (function() {{
        const text = {text_json};
        const lang = {lang_json};
        const readBtn = document.getElementById("read-btn-{key}");
        const stopBtn = document.getElementById("stop-btn-{key}");
        const status = document.getElementById("status-{key}");

        if (!("speechSynthesis" in window)) {{
          status.textContent = "Speech is not supported in this browser.";
          readBtn.disabled = true;
          stopBtn.disabled = true;
          return;
        }}

        readBtn.addEventListener("click", function() {{
          window.speechSynthesis.cancel();
          const utterance = new SpeechSynthesisUtterance(text);
          utterance.lang = lang;
          window.speechSynthesis.speak(utterance);
        }});

        stopBtn.addEventListener("click", function() {{
          window.speechSynthesis.cancel();
        }});
      }})();
    </script>
    """
    components.html(html, height=_COMPONENT_HEIGHT)
