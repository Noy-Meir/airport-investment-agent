"""
Streamlit component rendering "Read aloud" / "Stop" buttons backed by the
browser's Web Speech API (speechSynthesis). No external service, no API key.
"""

import json

import streamlit.components.v1 as components

from src.ui.chat_logic import SPEECH_LANG_OPTIONS

_COMPONENT_HEIGHT = 50
_MIC_COMPONENT_HEIGHT = 90
_CHAT_INPUT_TESTID = "stChatInputTextArea"


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


def render_mic_input(key):
    """
    Renders a compact HTML component with a microphone button, a language
    toggle (English/Hebrew), and a status line, backed by the browser's
    SpeechRecognition API (webkitSpeechRecognition where needed). No
    external service, no API key.

    Clicking the mic starts listening (button becomes "Stop" and status
    shows "Listening..."); clicking again or getting a final result stops
    it. On a final transcript, the component looks up the Streamlit chat
    input textarea in the parent document (by its data-testid), sets its
    value through the native value setter, dispatches an "input" event,
    and focuses it -- the recognized text is NOT auto-submitted, the user
    reviews it and presses Enter. If the parent document isn't reachable
    (sandboxing, etc.), the recognized text is shown inline in the
    component with a copy hint instead of failing silently.

    `key` must be unique per component instance.
    """
    lang_options_json = json.dumps(SPEECH_LANG_OPTIONS)
    testid_json = json.dumps(_CHAT_INPUT_TESTID)

    options_html = "\n".join(
        f'<option value="{code}">{label}</option>' for label, code in SPEECH_LANG_OPTIONS
    )

    html = f"""
    <div style="font-family: sans-serif;">
      <button id="mic-btn-{key}">🎤 Speak</button>
      <select id="lang-sel-{key}">
        {options_html}
      </select>
      <span id="status-{key}" style="margin-left: 8px; font-size: 0.85em; color: #888;"></span>
      <div id="fallback-{key}" style="display:none; margin-top: 4px; font-size: 0.85em;">
        <span>Recognized text (copy it into the chat box):</span>
        <div id="fallback-text-{key}"
             style="border: 1px solid #ccc; padding: 4px; margin-top: 2px; user-select: all;"></div>
      </div>
      <div style="font-size: 0.75em; color: #999; margin-top: 2px;">
        Voice input is processed by your browser, which may send audio to its vendor's
        speech-recognition service.
      </div>
    </div>
    <script>
      (function() {{
        const langOptions = {lang_options_json};
        const chatTestId = {testid_json};
        const micBtn = document.getElementById("mic-btn-{key}");
        const langSel = document.getElementById("lang-sel-{key}");
        const status = document.getElementById("status-{key}");
        const fallback = document.getElementById("fallback-{key}");
        const fallbackText = document.getElementById("fallback-text-{key}");

        const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognitionImpl) {{
          status.textContent = "Voice input is not supported in this browser, try Chrome or Safari.";
          micBtn.disabled = true;
          langSel.disabled = true;
          return;
        }}

        let recognition = null;
        let listening = false;

        function setListening(isListening) {{
          listening = isListening;
          micBtn.textContent = isListening ? "⏹ Stop" : "🎤 Speak";
          status.textContent = isListening ? "Listening..." : "";
        }}

        function applyTranscript(transcript) {{
          let applied = false;
          try {{
            const parentDoc = window.parent.document;
            const textarea = parentDoc.querySelector('[data-testid="' + chatTestId + '"]');
            if (textarea) {{
              const setter = Object.getOwnPropertyDescriptor(
                window.parent.HTMLTextAreaElement.prototype, "value"
              ).set;
              setter.call(textarea, transcript);
              textarea.dispatchEvent(new Event("input", {{ bubbles: true }}));
              textarea.focus();
              applied = true;
            }}
          }} catch (err) {{
            applied = false;
          }}

          if (!applied) {{
            fallback.style.display = "block";
            fallbackText.textContent = transcript;
          }} else {{
            fallback.style.display = "none";
          }}
        }}

        micBtn.addEventListener("click", function() {{
          if (listening) {{
            if (recognition) {{
              recognition.stop();
            }}
            return;
          }}

          recognition = new SpeechRecognitionImpl();
          recognition.lang = langSel.value;
          recognition.interimResults = false;
          recognition.maxAlternatives = 1;

          recognition.onstart = function() {{
            setListening(true);
          }};

          recognition.onresult = function(event) {{
            const transcript = event.results[0][0].transcript;
            applyTranscript(transcript);
          }};

          recognition.onerror = function(event) {{
            if (event.error === "not-allowed" || event.error === "permission-denied") {{
              status.textContent = "Microphone permission was denied.";
            }} else if (event.error === "no-speech") {{
              status.textContent = "No speech detected, try again.";
            }} else {{
              status.textContent = "Voice input error: " + event.error;
            }}
          }};

          recognition.onend = function() {{
            setListening(false);
          }};

          recognition.start();
        }});
      }})();
    </script>
    """
    components.html(html, height=_MIC_COMPONENT_HEIGHT)
