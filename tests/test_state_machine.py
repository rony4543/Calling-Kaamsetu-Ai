"""Role detection heuristics — app/orchestrator/state_machine.py."""

from app.orchestrator.state_machine import (
    TRANSITIONS,
    detect_role_from_text,
)
from app.supabase.schemas import ActiveFlow, UserRole


class TestDetectRole:
    def test_candidate_english(self):
        assert detect_role_from_text("I am looking for work") is UserRole.CANDIDATE
        assert detect_role_from_text("need a job please") is UserRole.CANDIDATE

    def test_candidate_hindi(self):
        assert detect_role_from_text("मुझे नौकरी चाहिए") is UserRole.CANDIDATE
        assert detect_role_from_text("कोई काम है क्या") is UserRole.CANDIDATE

    def test_employer_english(self):
        assert detect_role_from_text("I want to hire staff") is UserRole.EMPLOYER
        assert detect_role_from_text("post a job") is UserRole.EMPLOYER

    def test_employer_hindi(self):
        assert detect_role_from_text("मुझे भर्ती करनी है") is UserRole.EMPLOYER

    def test_employer_wins_ties(self):
        # Contains both a candidate ("job") and employer ("hire") signal.
        assert detect_role_from_text("I want to hire for this job") is UserRole.EMPLOYER
        # "worker chahiye" is an employer phrase even though "work" appears.
        assert detect_role_from_text("mujhe worker chahiye") is UserRole.EMPLOYER

    def test_case_insensitive(self):
        assert detect_role_from_text("HIRE") is UserRole.EMPLOYER
        assert detect_role_from_text("NAUKRI") is UserRole.CANDIDATE

    def test_unclear_returns_none(self):
        assert detect_role_from_text("hello there") is None
        assert detect_role_from_text("") is None
        assert detect_role_from_text(None) is None


class TestTransitionsTable:
    def test_every_flow_has_an_entry(self):
        for flow in (
            ActiveFlow.WELCOME,
            ActiveFlow.CANDIDATE_INTAKE,
            ActiveFlow.EMPLOYER_INTAKE,
            ActiveFlow.IDLE,
            ActiveFlow.OPTIN,
        ):
            assert flow in TRANSITIONS
            assert isinstance(TRANSITIONS[flow], list) and TRANSITIONS[flow]

    def test_intake_flows_can_reach_idle(self):
        assert ActiveFlow.IDLE in TRANSITIONS[ActiveFlow.CANDIDATE_INTAKE]
        assert ActiveFlow.IDLE in TRANSITIONS[ActiveFlow.EMPLOYER_INTAKE]
