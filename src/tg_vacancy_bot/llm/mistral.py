"""
LLM Analyzer для анализа текста вакансий через Mistral AI
"""
import asyncio
import json
import logging
from dotenv import load_dotenv
from openai import APIError, AsyncOpenAI

from tg_vacancy_bot import config
from tg_vacancy_bot.llm.prompts import SYSTEM_PROMPT
from tg_vacancy_bot.llm.schemas import (
    InvalidAnalysisResultError,
    validate_analysis_result,
)
from tg_vacancy_bot.models import VacancyAnalysis

load_dotenv()

client: AsyncOpenAI | None = None
MAX_ATTEMPTS = 2
RETRY_DELAYS = (1, 3)


def _get_client() -> AsyncOpenAI:
    """Лениво создаёт Mistral-клиент после валидации entrypoint."""
    global client
    if client is None:
        client = AsyncOpenAI(
            api_key=config.MISTRAL_API_KEY,
            base_url="https://api.mistral.ai/v1",
        )
    return client


async def analyze_text(vacancy_text: str) -> VacancyAnalysis | None:
    """
    Анализирует текст вакансии через Mistral AI API
    
    Args:
        vacancy_text: Текст вакансии из Telegram
        
    Returns:
        Проверенная VacancyAnalysis или None в случае ошибки.
    """
    for attempt in range(MAX_ATTEMPTS):
        response_text = None
        try:
            response = await _get_client().chat.completions.create(
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": vacancy_text},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )

            response_text = response.choices[0].message.content
            if not response_text:
                logging.warning("[MISTRAL] Пустой ответ от API")
                return None

            result = json.loads(response_text)
            return validate_analysis_result(result)

        except (APIError, json.JSONDecodeError, InvalidAnalysisResultError) as exc:
            logging.warning(
                "[MISTRAL] Ошибка попытки %d/%d: %s",
                attempt + 1,
                MAX_ATTEMPTS,
                exc,
            )
            if attempt + 1 >= MAX_ATTEMPTS:
                logging.error("[MISTRAL] Исчерпаны попытки анализа")
                return None

            delay = RETRY_DELAYS[attempt]
            logging.info("[MISTRAL] Повтор через %d сек.", delay)
            await asyncio.sleep(delay)
        except Exception as exc:
            logging.error("[MISTRAL] Непредвиденная ошибка: %s", exc)
            return None

    return None
