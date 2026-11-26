import uuid
import streamlit as st
import pytest

from ui.layout import render_sidebar


def test_new_chat_button_sets_session(monkeypatch):
    # Patch the references actually used by the UI. No legacy database or local
    # inference service should be touched by a button-state unit test.
    monkeypatch.setattr('ui.layout.get_all_sessions', lambda: [])
    monkeypatch.setattr('ui.layout.get_watchlist', lambda: [])
    monkeypatch.setattr('ui.layout.fetch_llm_studio_models', lambda: ['fixture/local-model'])

    # Populate session_state with dummy values
    st.session_state.session_id = 'old-session'
    st.session_state.messages = [{'role': 'user', 'content': 'Hello'}]
    st.session_state['pending_charts'] = ['fake_chart']

    # Fake the button behavior only for the New Chat button
    def fake_button(label, *args, **kwargs):
        return True if label == '➕ New Chat' else False

    monkeypatch.setattr(st, 'button', fake_button)
    monkeypatch.setattr(st, 'rerun', lambda *a, **k: None)

    api_key = render_sidebar()

    # New Chat should reset session and remove pending charts
    assert st.session_state.session_id != 'old-session'
    assert isinstance(st.session_state.session_id, str)
    assert st.session_state.messages == []
    assert 'pending_charts' not in st.session_state