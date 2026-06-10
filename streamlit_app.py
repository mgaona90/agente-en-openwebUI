import html
import os
from contextlib import nullcontext
import anthropic
import streamlit as st
from datetime import datetime
from system_prompt import MATIAS_PROMPT

MODEL = "claude-haiku-4-5-20251001"


def _secret(key: str, default: str = "") -> str:
    """Lee de st.secrets (local/Streamlit Cloud) o de env vars (Docker)."""
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


def _langfuse():
    # Sin cache: se inicializa en cada sesión para evitar que un None
    # cacheado persista tras corregir las keys.
    if "langfuse_client" not in st.session_state:
        try:
            from langfuse import Langfuse
            pk = _secret("LANGFUSE_PUBLIC_KEY")
            sk = _secret("LANGFUSE_SECRET_KEY")
            if not pk or not sk:
                st.session_state.langfuse_client = None
            else:
                lf = Langfuse(
                    public_key=pk,
                    secret_key=sk,
                    host=_secret("LANGFUSE_HOST", "https://cloud.langfuse.com"),
                )
                lf.auth_check()
                st.session_state.langfuse_client = lf
        except Exception:
            st.session_state.langfuse_client = None
    return st.session_state.langfuse_client

st.set_page_config(page_title="Chat", page_icon="💬", layout="wide")


def now_time() -> str:
    return datetime.now().strftime("%H:%M")


# ──────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stDecoration"], [data-testid="stStatusWidget"] { display: none; }

/* Remove all default padding/margins */
.block-container { padding: 0 !important; max-width: 100% !important; }
section.main > div { padding: 0 !important; }
div[data-testid="stVerticalBlock"] { gap: 0 !important; }

/* ── Full-page background ── */
html, body, .stApp { background-color: #111B21 !important; }
body { font-family: 'Segoe UI', Helvetica, Arial, sans-serif; margin: 0; }

/* ── Columns: sidebar + chat ── */
div[data-testid="column"]:first-child {
    background-color: #111B21;
    border-right: 1px solid #2A3942;
    padding: 0 !important;
    min-height: 100vh;
}
div[data-testid="column"]:last-child {
    background-color: #0B141A;
    padding: 0 !important;
    min-height: 100vh;
}

/* ── Sidebar ── */
.sb-header {
    background-color: #202C33;
    padding: 10px 16px;
    display: flex; align-items: center; justify-content: space-between;
    height: 60px; box-sizing: border-box;
}
.sb-avatar {
    width: 40px; height: 40px; border-radius: 50%;
    background-color: #6B7C85;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; cursor: pointer;
}
.sb-icons { display: flex; gap: 18px; color: #8696A0; font-size: 20px; }

.sb-search {
    background-color: #111B21;
    padding: 8px 12px;
}
.sb-search-inner {
    background-color: #2A3942;
    border-radius: 8px;
    padding: 9px 14px;
    color: #8696A0; font-size: 14px;
    display: flex; align-items: center; gap: 8px;
}

.sb-contact {
    display: flex; align-items: center;
    padding: 13px 16px; gap: 14px;
    border-bottom: 1px solid #222E35;
    cursor: pointer; background-color: #1F2C34;
}
.sb-contact-avatar {
    width: 49px; height: 49px; border-radius: 50%;
    background-color: #6B7C85; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 24px;
}
.sb-contact-body { flex: 1; min-width: 0; }
.sb-contact-row { display: flex; justify-content: space-between; align-items: baseline; }
.sb-contact-name { color: #E9EDEF; font-size: 16px; margin: 0 0 2px; font-weight: 400; }
.sb-contact-preview { color: #8696A0; font-size: 13.5px; margin: 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sb-contact-time { color: #25D366; font-size: 12px; white-space: nowrap; }
.sb-unread {
    background-color: #25D366; color: #111B21;
    border-radius: 50%; width: 20px; height: 20px; margin-top: 3px;
    display: flex; align-items: center; justify-content: center;
    font-size: 11.5px; font-weight: 600; flex-shrink: 0;
}

/* ── Chat header ── */
.chat-header {
    background-color: #202C33;
    padding: 10px 16px;
    display: flex; align-items: center; gap: 14px;
    height: 60px; box-sizing: border-box;
    position: sticky; top: 0; z-index: 10;
}
.chat-header-avatar {
    width: 40px; height: 40px; border-radius: 50%;
    background-color: #6B7C85; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; cursor: pointer;
}
.chat-header-name { color: #E9EDEF; font-size: 16px; font-weight: 500; margin: 0; line-height: 1.3; }
.chat-header-status { color: #8696A0; font-size: 13px; margin: 0; }
.chat-header-actions {
    margin-left: auto;
    display: flex; gap: 22px; color: #8696A0; font-size: 20px;
}
.chat-header-icon { cursor: pointer; transition: color .15s; }
.chat-header-icon:hover { color: #E9EDEF; }

/* ── Date separator ── */
.date-sep {
    display: flex; justify-content: center;
    margin: 14px 0 10px;
}
.date-sep span {
    background-color: #182229;
    color: #8696A0; font-size: 12.5px;
    padding: 5px 14px; border-radius: 8px;
    box-shadow: 0 1px 2px rgba(0,0,0,.3);
}

/* ── Message rows ── */
.msg-row { display: flex; padding: 1px 8px 3px; }
.msg-right { justify-content: flex-end; }
.msg-left  { justify-content: flex-start; align-items: flex-end; gap: 6px; }

/* Bot mini-avatar */
.bot-mini-av {
    width: 28px; height: 28px; border-radius: 50%;
    background-color: #6B7C85; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; margin-bottom: 2px;
}

/* Bubble */
.bubble {
    max-width: 65%; min-width: 70px;
    padding: 7px 10px 22px;
    font-size: 14.5px; line-height: 1.5;
    white-space: pre-wrap; word-wrap: break-word;
    position: relative;
    box-shadow: 0 1px 1px rgba(0,0,0,.3);
}
.bubble-user {
    background-color: #005C4B; color: #E9EDEF;
    border-radius: 8px 0 8px 8px;
}
.bubble-bot {
    background-color: #1F2C34; color: #E9EDEF;
    border-radius: 0 8px 8px 8px;
}

/* Meta: time + ticks */
.msg-meta {
    position: absolute; bottom: 4px; right: 8px;
    display: flex; align-items: center; gap: 3px;
}
.msg-time { color: rgba(233,237,239,.55); font-size: 11px; }
.msg-ticks { color: #53BDEB; font-size: 12px; letter-spacing: -2px; }

/* ── Typing indicator ── */
.typing-row { display: flex; padding: 2px 8px 6px; align-items: flex-end; gap: 6px; }
.typing-bubble {
    background-color: #1F2C34;
    border-radius: 0 8px 8px 8px;
    padding: 12px 14px;
    display: flex; gap: 5px; align-items: center;
    box-shadow: 0 1px 1px rgba(0,0,0,.3);
}
.dot {
    width: 7px; height: 7px; border-radius: 50%;
    background-color: #8696A0;
    animation: pulse 1.3s infinite ease-in-out;
}
.dot:nth-child(2) { animation-delay: .18s; }
.dot:nth-child(3) { animation-delay: .36s; }
@keyframes pulse {
    0%, 80%, 100% { transform: scale(.7); opacity: .5; }
    40%            { transform: scale(1);  opacity: 1;   }
}

/* ── Streaming bubble cursor ── */
.cursor {
    display: inline-block; width: 2px; height: 14px;
    background-color: #E9EDEF; margin-left: 1px;
    vertical-align: middle; animation: blink .8s step-end infinite;
}
@keyframes blink {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0; }
}

/* ── Input bar ── */
div[data-testid="stChatInput"] > div {
    background-color: #1F2C34 !important;
    border: none !important;
    border-top: 1px solid #2A3942 !important;
    padding: 8px 16px !important;
    border-radius: 0 !important;
}
[data-testid="stChatInputTextArea"] {
    background-color: #2A3942 !important;
    color: #D1D7DB !important;
    border-radius: 8px !important;
    border: none !important;
    font-family: 'Segoe UI', Helvetica, Arial, sans-serif !important;
    font-size: 15px !important;
    caret-color: #25D366 !important;
}
[data-testid="stChatInputTextArea"]::placeholder { color: #8696A0 !important; }
button[data-testid="stChatInputSubmitButton"] {
    background-color: #00A884 !important;
    border-radius: 50% !important;
    color: #111B21 !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #374045; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #4A555B; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def bubble_user(text: str, time: str) -> str:
    escaped = html.escape(text)
    return (
        f'<div class="msg-row msg-right">'
        f'  <div class="bubble bubble-user">{escaped}'
        f'    <div class="msg-meta">'
        f'      <span class="msg-time">{time}</span>'
        f'      <span class="msg-ticks">✓✓</span>'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )


def bubble_bot(text: str, time: str) -> str:
    escaped = html.escape(text)
    return (
        f'<div class="msg-row msg-left">'
        f'  <div class="bot-mini-av">🤖</div>'
        f'  <div class="bubble bubble-bot">{escaped}'
        f'    <div class="msg-meta">'
        f'      <span class="msg-time">{time}</span>'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )


def render_bubble(role: str, text: str, time: str) -> None:
    if role == "user":
        st.markdown(bubble_user(text, time), unsafe_allow_html=True)
    else:
        st.markdown(bubble_bot(text, time), unsafe_allow_html=True)


def render_typing() -> None:
    st.markdown("""
    <div class="typing-row">
      <div class="bot-mini-av">🤖</div>
      <div class="typing-bubble">
        <div class="dot"></div><div class="dot"></div><div class="dot"></div>
      </div>
    </div>
    """, unsafe_allow_html=True)


# ──────────────────────────────────────────────
# State
# ──────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []

# ──────────────────────────────────────────────
# Layout: sidebar | chat
# ──────────────────────────────────────────────
col_sb, col_chat = st.columns([1, 2.6])

# ── LEFT SIDEBAR ──────────────────────────────
with col_sb:
    last_preview = st.session_state.messages[-1]["content"][:32] + "…" if st.session_state.messages else "en línea"

    st.markdown(f"""
    <div class="sb-header">
      <div class="sb-avatar">🧑</div>
      <div class="sb-icons">
        <span title="Comunidades">⊞</span>
        <span title="Estado">◎</span>
        <span title="Nuevo chat">✏</span>
        <span title="Menú">⋮</span>
      </div>
    </div>
    <div class="sb-search">
      <div class="sb-search-inner">🔍&nbsp; Buscar o empezar un chat nuevo</div>
    </div>
    <div class="sb-contact">
      <div class="sb-contact-avatar">🤖</div>
      <div class="sb-contact-body">
        <div class="sb-contact-row">
          <p class="sb-contact-name">Mi amorcito, mi rey</p>
          <span class="sb-contact-time">{now_time()}</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <p class="sb-contact-preview">{html.escape(last_preview)}</p>
          <div class="sb-unread">1</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

# ── MAIN CHAT ─────────────────────────────────
with col_chat:
    # Header
    st.markdown("""
    <div class="chat-header">
      <div class="chat-header-avatar">🤖</div>
      <div>
        <p class="chat-header-name">Mi amorcito, mi rey</p>
        <p class="chat-header-status">en línea</p>
      </div>
      <div class="chat-header-actions">
        <span class="chat-header-icon" title="Videollamada">📹</span>
        <span class="chat-header-icon" title="Llamada">📞</span>
        <span class="chat-header-icon" title="Buscar">🔍</span>
        <span class="chat-header-icon" title="Menú">⋮</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Date chip
    st.markdown(
        '<div class="date-sep"><span>HOY</span></div>',
        unsafe_allow_html=True,
    )

    # History
    for msg in st.session_state.messages:
        render_bubble(msg["role"], msg["content"], msg.get("time", ""))

    # Input
    prompt = st.chat_input("Escribe un mensaje…")

if prompt:
    t = now_time()
    st.session_state.messages.append({"role": "user", "content": prompt, "time": t})

    # Re-render immediately with the new user bubble
    with col_chat:
        st.markdown(bubble_user(prompt, t), unsafe_allow_html=True)

        # Typing dots
        typing_slot = st.empty()
        with typing_slot:
            render_typing()

        # Stream response
        client = anthropic.Anthropic(api_key=_secret("ANTHROPIC_API_KEY"))
        stream_slot = st.empty()
        response_text = ""

        lf = _langfuse()
        messages_input = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.messages
        ]

        # Langfuse v4: context managers anidados (nullcontext si no hay keys)
        trace_ctx = (
            lf.start_as_current_observation(name="whatsapp-chat", as_type="span", input=prompt)
            if lf else nullcontext()
        )
        final_usage = None
        with trace_ctx as trace:
            gen_ctx = (
                lf.start_as_current_observation(
                    name="matias",
                    as_type="generation",
                    model=MODEL,
                    model_parameters={"max_tokens": 2048},
                    input=messages_input,
                ) if lf else nullcontext()
            )
            with gen_ctx as generation:
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=2048,
                    system=MATIAS_PROMPT,
                    messages=messages_input,
                ) as stream:
                    typing_slot.empty()
                    for text in stream.text_stream:
                        response_text += text
                        escaped = html.escape(response_text)
                        stream_slot.markdown(
                            f'<div class="msg-row msg-left">'
                            f'  <div class="bot-mini-av">🤖</div>'
                            f'  <div class="bubble bubble-bot">{escaped}'
                            f'    <span class="cursor"></span>'
                            f'    <div class="msg-meta"><span class="msg-time">{now_time()}</span></div>'
                            f'  </div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    final_usage = stream.get_final_message().usage

                if generation and final_usage:
                    generation.update(
                        output=response_text,
                        usage_details={
                            "input": final_usage.input_tokens,
                            "output": final_usage.output_tokens,
                        },
                    )
            if trace:
                trace.update(output=response_text)
        if lf:
            lf.flush()

        final_t = now_time()
        stream_slot.markdown(bubble_bot(response_text, final_t), unsafe_allow_html=True)
        st.session_state.messages.append(
            {"role": "assistant", "content": response_text, "time": final_t}
        )
