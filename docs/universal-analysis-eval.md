# Универсальный анализ: offline-оценка и shadow runner

Корпус `tests/fixtures/catalog_evaluation.json` содержит 30 синтетических,
вручную размеченных случаев. Он покрывает обязательный/значимый Go,
предпочтительный Go, Python backend, API без Go, отрицания, обучение Go в
будущем, несколько ролей, Frontend, QA, Data, DevOps/SRE, HR, дизайн,
безопасность, менеджмент, резюме, рекламу, неоднозначность и prompt injection.
Это небольшая инженерная выборка, а не репрезентативная production-оценка.

Выполненная проверка prefilter (2026-10-04):

| Метрика | Результат |
| --- | ---: |
| True positive | 25 |
| True negative | 4 |
| False positive | 1 (`ambiguous`) |
| False negative | 0 |
| Precision | 25/26 ≈ 96,15% |
| Recall | 25/25 = 100% |

Лишний пропуск неоднозначного поста на дешёвом этапе допустим: классификатор
должен пометить его review. Никакого живого вызова Mistral на этом этапе не было.
Качество классификации, confidence и matching на реальных ответах модели
**не подтверждено** зелёными тестами.

`tests/test_shadow_evaluation.py` отдельно воспроизводит классификацию и
matching на синтетических payloads: проверяет строгий контракт, все роли,
Go-проекцию, предпочтения, неизвестные поля, неверные ответы, лимиты и ошибки.
Эти payloads построены из разметки и проверяют только интеграцию. Их точность
нельзя представлять как точность модели. Исходный 10-примерный корпус
`universal_cases.json` также сохранён как регрессионный тест prefilter.

## Воспроизведение без API

```bash
PYTHONPATH=src venv/bin/python scripts/shadow_analysis.py --max-examples 30
PYTHONPATH=src venv/bin/python -m pytest -q tests/test_shadow_evaluation.py
```

По умолчанию runner оценивает только prefilter и явно помечает классификации
как `not_evaluated`. Он не открывает реестр, не экспортирует Sheets, не
публикует и не отправляет сообщения. В JSON-отчёте нет исходных текстов,
контактов, токенов или Telegram ID. Версии схемы и prompt входят в отчёт.

Сохранённые ответы можно проверить offline:

```bash
PYTHONPATH=src venv/bin/python scripts/shadow_analysis.py \
  --predictions /secure/path/predictions.json --max-examples 30
```

Файл — JSON object `{"case_id": {полный UniversalDecision contract}}` с
`schema_version`, `prompt_version`, `is_vacancy`, `classifications`, `analysis`,
`confidence`, `reason_code`, `needs_review`. Отсутствующие и неверные ответы
учитываются отдельно. Отчёт разделяет confusion matrix prefilter, определения
вакансии, Go-проекции и matching; дополнительно показывает точность полного
набора ролей и флага review. Replay не доказывает происхождение ответов:
рядом с отчётом следует сохранять модель, дату и способ получения predictions.

## Ограниченный shadow-run и релизный барьер

Только при отдельном решении выполнить живую оценку:

```bash
PYTHONPATH=src venv/bin/python scripts/shadow_analysis.py \
  --live --model mistral-large-2512 --corpus /secure/path/real_posts.json \
  --max-examples 30 --max-calls 10
```

Без `--live` вызовы невозможны; с `--live` требуется положительный `--max-calls`.
Ошибки тоже расходуют бюджет попыток. Дополнительно действует общий
`MISTRAL_DAILY_LIMIT` в Premium SQLite, без повторов SDK. Примеры и вызовы
ограничены диапазоном до 1000. Live runner учитывает prefilter, поэтому
отклонённые им примеры отдельно видны в отчёте, а не исчезают из оценки.
Нулевые экспорты и рассылки гарантируются отсутствием таких зависимостей в
runner; он вызывает только анализатор. Перед расширением allowlist нужны
ручная проверка ошибок, независимая выборка и отдельная калибровка порогов.
На 2026-10-07 локальный ключ вернул `PermissionDeniedError` для четырёх
ограниченных проб Java, HR, Go и 1С. Production-ключ также вернул HTTP 403
`tier_not_allowed` даже на коротком запросе к `mistral-large-2512`:
модель недоступна текущему тарифу. На production моделью по умолчанию остаётся
`ministral-3b-2512`, автоматическая личная доставка выключена, все три сервиса
healthy. В реестре на момент сверки было 731 `unavailable`, 20 `available`,
5 `review` анализов и 12 записей с `eligible_at`; эти числа включают историю
анализов, а не только последние решения. Валидность и качество большой модели
на реальных постах не подтверждены. Production-релиз и backfill остаются закрыты
до доступа к модели и успешной контрольной выборки.
