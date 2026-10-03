from dataclasses import replace
from types import SimpleNamespace

from tg_vacancy_bot.llm.universal import UniversalDecision
from tg_vacancy_bot.profile_matcher import match_profile, match_profiles
from tg_vacancy_bot.telegram.candidate_store import CandidateProfile


def profile(*, direction_id="development", **preferences):
    return CandidateProfile(
        profile_id="p1",
        telegram_user_id=1,
        name="Backend",
        direction_id=direction_id,
        specialization_id="backend",
        role_id="backend_developer",
        preferences=preferences,
        is_active=True,
        version=1,
    )


def decision(**changes):
    values = dict(
        is_vacancy=True,
        classifications=(
            {
                "direction_id": "development",
                "specialization_id": "backend",
                "role_id": "backend_developer",
                "confidence": 55,
            },
        ),
        analysis=SimpleNamespace(
            required_stack=["Go", "PostgreSQL"],
            preferred_stack=["Kubernetes"],
            grade_from="Senior",
            grade_to="Senior",
            work_format="Remote",
            hiring_geography="Worldwide",
            country="Не указано",
            city="Не указано",
            vacancy_language="English",
            requirements="Go and PostgreSQL experience required",
            responsibilities="Build backend services",
            summary="",
        ),
        confidence=55,
        reason_code="vacancy_match",
        needs_review=False,
    )
    values.update(changes)
    return UniversalDecision(**values)


def test_all_alternatives_and_required_skills_must_match():
    result = match_profile(
        decision(),
        profile(
            stacks=["golang", "Java"],
            seniority=["middle", "senior"],
            formats=["remote"],
            geography=["Worldwide", "Germany"],
            vacancy_languages=["ru", "English"],
            required_skills=["Go", "PostgreSQL"],
        ),
    )
    assert result.status == "match"
    assert "stacks" in result.matched_fields


def test_veto_and_role_mismatch_are_no_match():
    result = match_profile(decision(), profile(excluded_skills=["PostgreSQL"]))
    assert result.status == "no_match"
    assert result.reason_code == "criteria_conflict"
    assert "excluded_skills:postgresql" in result.conflicting_fields


def test_missing_required_criterion_is_review_with_field_name():
    vacancy = decision()
    vacancy = replace(
        vacancy,
        analysis=SimpleNamespace(
            **{**vars(vacancy.analysis), "vacancy_language": "Не указано"}
        ),
    )
    result = match_profile(vacancy, profile(vacancy_languages=["ru"]))
    assert result.status == "review"
    assert result.reason_code == "required_data_missing"
    assert result.missing_fields == ("vacancy_languages",)


def test_review_precedes_known_conflict_and_profiles_are_local():
    uncertain = decision(needs_review=True)
    profiles = [profile(), profile(direction_id="hr")]
    assert [m.status for m in match_profiles(uncertain, profiles)] == [
        "review",
        "review",
    ]


def test_non_vacancy_is_not_a_match():
    result = match_profile(decision(is_vacancy=False), profile())
    assert (result.status, result.reason_code) == ("no_match", "not_a_vacancy")


def test_skill_names_do_not_match_substrings_of_other_technologies():
    value = decision()
    value = replace(
        value,
        analysis=SimpleNamespace(
            **{
                **vars(value.analysis),
                'required_stack': ['Python', 'Django', 'JavaScript', 'C++'],
                'requirements': 'Django and JavaScript experience; C++ services.',
                'responsibilities': '',
                'summary': '',
            }
        ),
    )
    for skill in ('Go', 'Java', 'C'):
        assert (
            match_profile(value, profile(required_skills=[skill])).status == 'no_match'
        )
        assert match_profile(value, profile(excluded_skills=[skill])).status == 'match'
    assert match_profile(value, profile(required_skills=['C++'])).status == 'match'
    assert match_profile(value, profile(excluded_skills=['C++'])).status == 'no_match'


def test_normalized_skill_phrases_match_token_boundaries():
    value = decision()
    value = replace(
        value,
        analysis=SimpleNamespace(
            **{
                **vars(value.analysis),
                'requirements': 'Experience: distributed systems, C#.',
            }
        ),
    )
    assert (
        match_profile(
            value, profile(required_skills=['distributed systems', 'C#'])
        ).status
        == 'match'
    )


def test_primary_and_additional_languages_use_the_stack_matching_rule():
    assert match_profile(decision(), profile(primary_language='go')).status == 'match'
    assert (
        match_profile(decision(), profile(primary_language='python')).status
        == 'no_match'
    )
    assert (
        match_profile(
            decision(),
            profile(primary_language='python', additional_languages=['go']),
        ).status
        == 'match'
    )


def test_worldwide_geography_does_not_veto_a_country_specific_listing():
    value = decision(
        analysis=SimpleNamespace(
            **{
                **vars(decision().analysis),
                'hiring_geography': 'Europe',
                'country': 'Germany',
                'city': 'Berlin',
            }
        )
    )
    assert match_profile(value, profile(geography=['Весь мир'])).status == 'match'


def test_api_developer_catalog_role_remains_compatible_with_backend_profile():
    value = decision(
        classifications=(
            {
                'direction_id': 'development',
                'specialization_id': 'backend',
                'role_id': 'api_developer',
                'confidence': 90,
            },
        )
    )
    assert match_profile(value, profile()).status == 'match'
