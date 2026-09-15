import pytest

from tg_vacancy_bot.search.terms import expand_queries
from tg_vacancy_bot.threads.rules import (
    ClassificationError,
    classify_rules,
    parse_classification,
)


@pytest.mark.parametrize(
    'text,label',
    [
        ('We are hiring a Senior Go engineer', 'JOB'),
        ('Ищем Golang разработчика в команду', 'JOB'),
        ('Вакансия: Go backend developer', 'JOB'),
        ('Senior Go Engineer looking for new opportunities', 'CANDIDATE'),
        ('Я Go разработчик, ищу работу', 'CANDIDATE'),
        ('Ищу вакансию Go разработчика', 'CANDIDATE'),
        ('Looking for a Senior Go developer', 'JOB'),
        ('Golang developer open to work', 'CANDIDATE'),
        ('Новый курс по Golang для разработчиков', 'NOISE'),
        ('Go conference news and tutorials', 'NOISE'),
        ('Ищу работу Go разработчиком. Также мы ищем Java инженера', 'AMBIGUOUS'),
        ('Golang engineer', 'AMBIGUOUS'),
        ('Go to the beach', 'NOISE'),
        ('ищу Go разработчика', 'JOB'),
        ('Go engineer looking for work', 'CANDIDATE'),
        ('Go разработчик, рассматриваю предложения', 'CANDIDATE'),
        ('Go разработчик, открыт к предложениям', 'CANDIDATE'),
        ('Мое резюме: Go разработчик', 'CANDIDATE'),
        ('My CV: Senior Go engineer', 'CANDIDATE'),
        ('My resume: Go developer', 'CANDIDATE'),
        ('Why we migrated our backend from Java to Go', 'NOISE'),
        ('Go ahead, hiring Python developer', 'NOISE'),
        ('Hiring Python developer; Go is optional', 'NOISE'),
        ('Hiring Go developer, send your CV', 'JOB'),
    ],
)
def test_rules(text, label):
    assert classify_rules(text).label == label


def test_variants_bounded_and_multilingual():
    variants = expand_queries('Senior Go developer', limit=6)
    assert len(variants) == 6
    assert variants[0] == 'Senior Go developer'
    assert 'Senior' in variants[1]
    assert 'вакансия' in variants[1]
    assert 'hiring' in variants[2]
    assert len(expand_queries('Go', limit=1)) == 1
    assert expand_queries('Go', limit=1)[0] != 'Go'
    assert expand_queries('Go', limit=0) == []
    assert 'Python' in expand_queries('Python engineer', track='python')[1]
    all_variants = expand_queries('Senior Go', limit=12)
    assert len(all_variants) == 12
    assert any('Go engineer' in query for query in all_variants)
    assert 'ищу Go разработчика' in all_variants
    assert 'ищем Go разработчика' in all_variants


@pytest.mark.parametrize(
    'payload',
    [
        {},
        {'is_job': 'true', 'confidence': 0.9, 'language': 'en'},
        {'is_job': True, 'confidence': True, 'language': 'en'},
        {'is_job': True, 'confidence': 2, 'language': 'en'},
        {'is_job': True, 'confidence': float('nan'), 'language': 'en'},
        {'is_job': True, 'confidence': 0.9, 'language': 'bad'},
        {'is_job': True, 'confidence': 0.9, 'language': 'en', 'injected': 'secret'},
    ],
)
def test_invalid_classification(payload):
    with pytest.raises(ClassificationError, match='invalid_classification'):
        parse_classification(payload)


def test_valid_classification():
    result = parse_classification({'is_job': True, 'confidence': 0.9, 'language': 'en'})
    assert result.label == 'JOB'
    assert result.confidence == 0.9


def test_llm_classification_uses_existing_client(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from tg_vacancy_bot.llm import mistral
    from tg_vacancy_bot.threads.rules import classify_text

    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"is_job": true, "confidence": 0.85, "language": "en"}'
                    )
                )
            ]
        )
    )
    client = Mock()
    client.with_options.return_value = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(mistral, '_get_client', lambda: client)
    assert asyncio.run(classify_text('ambiguous engineer post')).is_job is True
    client.with_options.assert_called_once_with(max_retries=0)
    assert (
        create.call_args.kwargs['messages'][1]['content'] == 'ambiguous engineer post'
    )


def test_llm_error_is_sanitized(monkeypatch):
    import asyncio
    from tg_vacancy_bot.llm import mistral
    from tg_vacancy_bot.threads.rules import classify_text

    def broken():
        raise RuntimeError('secret-token')

    monkeypatch.setattr(mistral, '_get_client', broken)
    with pytest.raises(ClassificationError) as caught:
        asyncio.run(classify_text('post'))
    assert str(caught.value) == 'classification_failed'
