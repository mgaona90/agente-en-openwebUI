import html
import anthropic
import streamlit as st

st.set_page_config(page_title="Chat", page_icon="💬", layout="centered")

st.markdown("""
<style>
#MainMenu, footer, header {visibility: hidden;}

.stApp {
    background-color: #0B141A;
}

.wa-header {
    background-color: #202C33;
    padding: 12px 16px;
    border-radius: 10px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 12px;
}
.wa-avatar {
    width: 42px;
    height: 42px;
    border-radius: 50%;
    background-color: #6B7C85;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 22px;
}
.wa-name {
    color: #E9EDEF;
    font-size: 17px;
    font-weight: 500;
    font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
    margin: 0;
}
.wa-status {
    color: #8696A0;
    font-size: 13px;
    font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
    margin: 0;
}

.msg-row {
    display: flex;
    margin: 2px 0 4px 0;
    padding: 0 8px;
}
.msg-row-right { justify-content: flex-end; }
.msg-row-left  { justify-content: flex-start; }

.bubble {
    max-width: 72%;
    padding: 7px 12px 8px 12px;
    font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 14.5px;
    line-height: 1.45;
    white-space: pre-wrap;
    word-wrap: break-word;
}
.bubble-user {
    background-color: #005C4B;
    color: #E9EDEF;
    border-radius: 8px 0 8px 8px;
}
.bubble-bot {
    background-color: #202C33;
    color: #E9EDEF;
    border-radius: 0 8px 8px 8px;
}

[data-testid="stChatInputTextArea"] {
    background-color: #2A3942 !important;
    color: #D1D7DB !important;
}
div[data-testid="stChatInput"] > div {
    background-color: #1F2C34 !important;
    border: none !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="wa-header">
    <div class="wa-avatar">🧑</div>
    <div>
        <p class="wa-name">Matias</p>
        <p class="wa-status">en línea</p>
    </div>
</div>
""", unsafe_allow_html=True)

client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

if "messages" not in st.session_state:
    st.session_state.messages = []


def render_bubble(role: str, text: str):
    escaped = html.escape(text)
    if role == "user":
        st.markdown(
            f'<div class="msg-row msg-row-right"><div class="bubble bubble-user">{escaped}</div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="msg-row msg-row-left"><div class="bubble bubble-bot">{escaped}</div></div>',
            unsafe_allow_html=True,
        )


for msg in st.session_state.messages:
    render_bubble(msg["role"], msg["content"])

if prompt := st.chat_input("Mensaje..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    render_bubble("user", prompt)

    placeholder = st.empty()
    response_text = ""

    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system="You are a helpful assistant.",
        messages=st.session_state.messages,
    ) as stream:
        for text in stream.text_stream:
            response_text += text
            escaped = html.escape(response_text)
            placeholder.markdown(
                f'<div class="msg-row msg-row-left"><div class="bubble bubble-bot">{escaped}▋</div></div>',
                unsafe_allow_html=True,
            )

    escaped = html.escape(response_text)
    placeholder.markdown(
        f'<div class="msg-row msg-row-left"><div class="bubble bubble-bot">{escaped}</div></div>',
        unsafe_allow_html=True,
    )

    st.session_state.messages.append({"role": "assistant", "content": response_text})
