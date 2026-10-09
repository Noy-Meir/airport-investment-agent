"""
Voice controls for the Streamlit chat UI: "Read aloud" / "Stop" buttons
(speechSynthesis) rendered per assistant message, and a mic button
(SpeechRecognition) injected once into Streamlit's chat input. No external
service, no API key. English only (en-US) for both recognition and
synthesis.
"""

import json

import streamlit.components.v1 as components

_COMPONENT_HEIGHT = 46
_CHAT_INPUT_TESTID = "stChatInputTextArea"
_CHAT_INPUT_SUBMIT_TESTID = "stChatInputSubmitButton"

# Shared across the per-message (iframe) buttons and the mic button injected
# into the parent page, so all voice controls look like one system. Uses
# Streamlit's own CSS variables (e.g. var(--text-color)) with light/dark
# fallbacks: those variables resolve automatically for the parent-injected
# mic button (it lives in the real Streamlit page), and fall back to the
# prefers-color-scheme block inside each isolated component iframe.
_VOICE_BUTTON_CSS = """
html, body { background: transparent !important; margin: 0; overflow: visible; }
.aiia-voice-btn {
  font-family: "Source Sans Pro", sans-serif;
  font-size: 14px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px solid var(--gray-30, #d5d6d8);
  background: var(--background-color, #ffffff);
  color: var(--text-color, #31333f);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, transform 0.08s ease;
}
.aiia-voice-btn svg { width: 16px; height: 16px; fill: currentColor; flex: none; }
.aiia-voice-btn:hover { border-color: var(--primary-color, #ff4b4b); }
.aiia-voice-btn:active { transform: scale(0.96); }
.aiia-voice-btn:focus-visible {
  outline: 2px solid var(--primary-color, #ff4b4b);
  outline-offset: 1px;
}
.aiia-voice-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.aiia-mic-btn { padding: 6px; border-radius: 999px; margin: 0 4px; }
.aiia-mic-btn.aiia-listening {
  border-color: var(--primary-color, #ff4b4b);
  color: var(--primary-color, #ff4b4b);
  animation: aiia-pulse 1.1s ease-in-out infinite;
}
@keyframes aiia-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(255, 75, 75, 0.35); }
  50% { box-shadow: 0 0 0 6px rgba(255, 75, 75, 0); }
}
@media (prefers-color-scheme: dark) {
  .aiia-voice-btn {
    background: var(--background-color, #0e1117);
    color: var(--text-color, #fafafa);
    border-color: var(--gray-70, #555b61);
  }
}
"""

_MIC_SVG = (
    '<svg viewBox="0 0 24 24"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z'
    'm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z"/></svg>'
)
_SPEAKER_SVG = (
    '<svg viewBox="0 0 24 24"><path d="M3 10v4h4l5 5V5L7 10H3zm13.5 2A4.5 4.5 0 0 0 14 7.97v8.05'
    'A4.5 4.5 0 0 0 16.5 12zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06'
    'c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>'
)
_STOP_SVG = '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'

# Placeholders (not an f-string) so the JS below needs no Python-brace
# escaping; values are substituted with json.dumps(...).replace(...) in
# render_voice_assets, which keeps every substitution quote/escape-safe.
_MIC_SCRIPT_TEMPLATE = """
(function() {
  if (window.__aiiaMicInit) return;
  window.__aiiaMicInit = true;

  const CHAT_TESTID = __CHAT_TESTID__;
  const SUBMIT_TESTID = __SUBMIT_TESTID__;
  const MIC_SVG = __MIC_SVG__;
  const MIC_ID = "aiia-mic-btn";
  const PRIVACY_TITLE = "Voice input (English). Audio may be sent to your browser's speech-recognition service.";
  const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;

  function findTextarea() {
    return document.querySelector('[data-testid="' + CHAT_TESTID + '"]');
  }

  function buildButton() {
    const btn = document.createElement("button");
    btn.id = MIC_ID;
    btn.type = "button";
    btn.className = "aiia-voice-btn aiia-mic-btn";
    btn.title = PRIVACY_TITLE;
    btn.setAttribute("aria-label", "Voice input");
    btn.innerHTML = MIC_SVG;
    return btn;
  }

  function applyTranscript(textarea, transcript) {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, "value"
    ).set;
    setter.call(textarea, transcript);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.focus();
  }

  function wireButton(btn, textarea) {
    if (!SpeechRecognitionImpl) {
      btn.disabled = true;
      btn.title = "Voice input is not supported in this browser, try Chrome or Safari.";
      return;
    }

    let recognition = null;
    let listening = false;

    function setListening(isListening) {
      listening = isListening;
      btn.classList.toggle("aiia-listening", isListening);
      btn.title = isListening ? "Listening... click to stop" : PRIVACY_TITLE;
    }

    btn.addEventListener("click", function() {
      if (listening) {
        if (recognition) { recognition.stop(); }
        return;
      }

      recognition = new SpeechRecognitionImpl();
      recognition.lang = "en-US";
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;

      recognition.onstart = function() { setListening(true); };
      recognition.onresult = function(event) {
        applyTranscript(textarea, event.results[0][0].transcript);
      };
      recognition.onerror = function(event) {
        setListening(false);
        if (event.error === "not-allowed" || event.error === "permission-denied") {
          btn.title = "Microphone permission was denied.";
        } else if (event.error === "no-speech") {
          btn.title = "No speech detected, try again.";
        } else {
          btn.title = "Voice input error: " + event.error;
        }
      };
      recognition.onend = function() { setListening(false); };

      try {
        recognition.start();
      } catch (err) {
        setListening(false);
      }
    });
  }

  function attach() {
    if (document.getElementById(MIC_ID)) return;
    const textarea = findTextarea();
    if (!textarea) return;

    const submitBtn = document.querySelector('[data-testid="' + SUBMIT_TESTID + '"]');
    const btn = buildButton();
    if (submitBtn && submitBtn.parentElement) {
      submitBtn.parentElement.insertBefore(btn, submitBtn);
    } else {
      textarea.parentElement.appendChild(btn);
    }
    wireButton(btn, textarea);
  }

  attach();
  const observer = new MutationObserver(attach);
  observer.observe(document.body, { childList: true, subtree: true });
})();
"""


def render_speech_controls(text, key):
    """
    Renders a compact HTML component with "Read aloud" and "Stop" buttons
    (icons + labels, shared voice-button styling). `text` is json-encoded,
    never string-concatenated into the script. Clicking "Read aloud"
    cancels any ongoing speech then speaks it (fixed to en-US); "Stop"
    cancels. Falls back to a disabled state with a tooltip if
    speechSynthesis is unavailable. `key` must be unique per message.
    """
    text_json = json.dumps(text or "")

    html = f"""
    <style>{_VOICE_BUTTON_CSS}</style>
    <div style="display:flex; align-items:center; gap:6px;">
      <button id="read-btn-{key}" type="button" class="aiia-voice-btn">
        {_SPEAKER_SVG}<span>Read aloud</span>
      </button>
      <button id="stop-btn-{key}" type="button" class="aiia-voice-btn">
        {_STOP_SVG}<span>Stop</span>
      </button>
    </div>
    <script>
      (function() {{
        const text = {text_json};
        const readBtn = document.getElementById("read-btn-{key}");
        const stopBtn = document.getElementById("stop-btn-{key}");

        if (!("speechSynthesis" in window)) {{
          readBtn.disabled = true;
          stopBtn.disabled = true;
          readBtn.title = "Speech is not supported in this browser.";
          return;
        }}

        readBtn.addEventListener("click", function() {{
          window.speechSynthesis.cancel();
          const utterance = new SpeechSynthesisUtterance(text);
          utterance.lang = "en-US";
          window.speechSynthesis.speak(utterance);
        }});

        stopBtn.addEventListener("click", function() {{
          window.speechSynthesis.cancel();
        }});
      }})();
    </script>
    """
    components.html(html, height=_COMPONENT_HEIGHT)


def render_voice_assets():
    """
    Injects the mic button and shared voice-button CSS into the PARENT
    Streamlit page, once per browser session. Call exactly once in app.py
    (components.html at height 0 -- it renders no visible iframe content of
    its own).

    This component's own script never touches the chat UI directly.
    Instead it builds a <style> and a <script> element and appends them to
    `window.parent.document`, so the mic button and its SpeechRecognition
    listeners live and run in the parent page -- unaffected by this
    component's iframe being torn down and recreated on every Streamlit
    rerun. Idempotent: guarded by the injected elements' ids
    ("aiia-voice-style" / "aiia-mic-script") and a `window.__aiiaMicInit`
    flag inside the injected script, so repeat calls (every rerun) are a
    no-op after the first.

    The injected script places the mic button immediately to the left of
    the chat input's submit button (looked up by data-testid) so it is
    always visible next to the send button, never scrolled away. A
    MutationObserver re-attaches the button if Streamlit re-renders the
    chat input container. Recognized speech is fixed to en-US and is never
    auto-submitted -- it's written into the chat textarea for the user to
    review and press Enter. Visible states: a pulsing ring while
    listening, a disabled button with a tooltip when SpeechRecognition
    isn't supported, and a tooltip message on permission-denied / no-speech
    errors. The privacy note ("audio may be sent to your browser's
    speech-recognition service") is the button's default tooltip.
    """
    mic_script = (
        _MIC_SCRIPT_TEMPLATE.replace("__CHAT_TESTID__", json.dumps(_CHAT_INPUT_TESTID))
        .replace("__SUBMIT_TESTID__", json.dumps(_CHAT_INPUT_SUBMIT_TESTID))
        .replace("__MIC_SVG__", json.dumps(_MIC_SVG))
    )
    mic_script_json = json.dumps(mic_script)
    css_json = json.dumps(_VOICE_BUTTON_CSS)

    html = f"""
    <script>
      (function() {{
        const parentDoc = window.parent.document;
        if (parentDoc.getElementById("aiia-mic-script")) {{
          return;
        }}

        if (!parentDoc.getElementById("aiia-voice-style")) {{
          const style = parentDoc.createElement("style");
          style.id = "aiia-voice-style";
          style.textContent = {css_json};
          parentDoc.head.appendChild(style);
        }}

        const script = parentDoc.createElement("script");
        script.id = "aiia-mic-script";
        script.textContent = {mic_script_json};
        parentDoc.body.appendChild(script);
      }})();
    </script>
    """
    components.html(html, height=0)
