import streamlit as st
from agents.orchestrator import Orchestrator


def test_orchestrator_team_cached(monkeypatch):
    # Patch agent constructors to simple sentinel objects
    monkeypatch.setattr('agents.orchestrator.get_data_agent', lambda cfg: {'name': 'data'})
    monkeypatch.setattr('agents.orchestrator.get_news_agent', lambda cfg: {'name': 'news'})
    # team leader returns a dict merging the team - simple container safe for tests
    monkeypatch.setattr('agents.orchestrator.get_team_lead', lambda cfg, team, sid, thinking_mode=False: {'team': team, 'sid': sid})

    model_config = type('cfg', (), {'id': 'cfg1'})()
    sid = 'session-123'

    orch1 = Orchestrator(model_config, sid)
    t1 = orch1.team()

    orch2 = Orchestrator(model_config, sid)
    t2 = orch2.team()

    # Should be the same object (cached) across Orchestrators using same session id
    assert t1 == t2


def test_orchestrator_team_differs_by_thinking_mode(monkeypatch):
    # Patch agent constructors to simple sentinel objects
    monkeypatch.setattr('agents.orchestrator.get_data_agent', lambda cfg: {'name': 'data'})
    monkeypatch.setattr('agents.orchestrator.get_news_agent', lambda cfg: {'name': 'news'})
    # get_team_lead returns a dict merging the team - simple container safe for tests
    monkeypatch.setattr('agents.orchestrator.get_team_lead', lambda cfg, team, sid, thinking_mode=False: {'team': team, 'sid': sid, 'thinking': thinking_mode})

    model_config = type('cfg', (), {'id': 'cfg1'})()
    sid = 'session-456'

    orch_no_think = Orchestrator(model_config, sid, thinking_mode=False)
    t_no_think = orch_no_think.team()

    orch_with_think = Orchestrator(model_config, sid, thinking_mode=True)
    t_with_think = orch_with_think.team()

    assert t_no_think != t_with_think