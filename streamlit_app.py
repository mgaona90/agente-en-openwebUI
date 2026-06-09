import anthropic
import streamlit as st

st.set_page_config(page_title="Mi Agente", page_icon="🤖", layout="centered")
st.title("🤖 Mi Agente")

client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Escribí tu mensaje..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        response_text = ""
        placeholder = st.empty()

        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system="You are a helpful assistant. Use the tools available to you to answer questions or complete tasks.",
            messages=st.session_state.messages,
        ) as stream:
            for text in stream.text_stream:
                response_text += text
                placeholder.markdown(response_text + "▋")
        placeholder.markdown(response_text)

    st.session_state.messages.append({"role": "assistant", "content": response_text})
