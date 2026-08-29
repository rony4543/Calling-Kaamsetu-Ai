"""
State Evaluator — the deterministic missing-field engine (app/agents/state_evaluator.py).

This is the anti-hallucination backbone, so it's worth pinning down hard:
missing-field lists, completeness %, and the single next_field to ask for.
"""

from app.agents.state_evaluator import evaluate_candidate, evaluate_job
from app.supabase.schemas import (
    Candidate,
    CandidateLocation,
    Job,
    JobLocation,
    SalaryRange,
)


def _full_candidate() -> Candidate:
    return Candidate(
        wa_id="911111111111",
        name="Ramesh Kumar",
        location=CandidateLocation(city="Jaipur"),
        skills=["welding", "fitting"],
        experience_years=5,
        expected_salary=SalaryRange(min=15000),
        job_type_pref=["full_time"],
        availability="immediate",
    )


def _full_job() -> Job:
    return Job(
        employer_id="emp-1",
        title="Welder",
        skills_required=["welding"],
        experience_min=2,
        location=JobLocation(city="Jaipur"),
        job_type="full_time",
        salary=SalaryRange(min=20000, max=30000),
        openings=3,
    )


class TestCandidate:
    def test_empty_candidate_is_zero_complete(self):
        res = evaluate_candidate(Candidate(wa_id="910000000000"))
        assert res["completeness_pct"] == 0
        assert res["next_field"] == "name"  # first required field
        # all seven required fields missing
        assert set(res["missing_fields"]) == {
            "name",
            "location",
            "skills",
            "experience_years",
            "expected_salary",
            "job_type_pref",
            "availability",
        }

    def test_full_candidate_is_complete(self):
        res = evaluate_candidate(_full_candidate())
        assert res["missing_fields"] == []
        assert res["completeness_pct"] == 100
        assert res["next_field"] is None

    def test_location_satisfied_by_district_or_city(self):
        by_city = _full_candidate()
        assert "location" not in evaluate_candidate(by_city)["missing_fields"]
        by_district = _full_candidate()
        by_district.location = CandidateLocation(district="Jaipur Rural")
        assert "location" not in evaluate_candidate(by_district)["missing_fields"]

    def test_next_field_is_first_missing_in_required_order(self):
        c = _full_candidate()
        c.name = None          # clear the first required field
        c.availability = None  # ...and a later one
        res = evaluate_candidate(c)
        assert res["next_field"] == "name"
        assert set(res["missing_fields"]) == {"name", "availability"}

    def test_partial_completeness_math(self):
        c = Candidate(
            wa_id="910000000001",
            name="Sita",
            skills=["cooking"],
            experience_years=3,
        )  # 3 of 7 required present
        res = evaluate_candidate(c)
        assert res["completeness_pct"] == round(3 / 7 * 100)  # 43
        assert res["next_field"] == "location"


class TestJob:
    def test_empty_job_is_zero_complete(self):
        res = evaluate_job(Job(employer_id="emp-1"))
        assert res["completeness_pct"] == 0
        assert res["next_field"] == "title"

    def test_full_job_is_complete(self):
        res = evaluate_job(_full_job())
        assert res["missing_fields"] == []
        assert res["completeness_pct"] == 100
        assert res["next_field"] is None

    def test_salary_satisfied_by_min_or_max_only(self):
        j = _full_job()
        j.salary = SalaryRange(max=25000)  # only max set
        assert "salary" not in evaluate_job(j)["missing_fields"]
