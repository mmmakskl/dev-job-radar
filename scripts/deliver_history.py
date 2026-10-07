#!/usr/bin/env python3
"""Deliver confirmed backfill matches while normal personal delivery is held."""

import argparse
import asyncio
from pathlib import Path

from tg_vacancy_bot import config
from tg_vacancy_bot.registry import VacancyRegistry
from tg_vacancy_bot.telegram.bot_api import TelegramBotApi
from tg_vacancy_bot.telegram.candidate_delivery_worker import (
    CandidateDeliveryWorker,
    PersonalDeliveryStore,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--release',
        action='store_true',
        help='Resume normal delivery after reconciliation',
    )
    args = parser.parse_args()
    hold = Path(config.DATA_DIR) / 'candidate-delivery.hold'
    if not hold.exists():
        parser.error('Run history with --hold-delivery first')
    api = TelegramBotApi(config.CANDIDATE_BOT_TOKEN)
    registry = VacancyRegistry(config.CANDIDATE_BOT_DB_PATH)
    worker = CandidateDeliveryWorker(
        api,
        registry,
        PersonalDeliveryStore(config.CANDIDATE_BOT_DB_PATH),
        allowed_user_ids=config.CANDIDATE_BOT_ALLOWED_USER_IDS,
        enabled=True,
    )
    counts = await worker.deliver_history()
    print(counts)
    if args.release and counts['unknown'] == 0:
        with worker.store.connect() as connection:
            unresolved = connection.execute(
                "SELECT COUNT(*) FROM candidate_personal_deliveries WHERE state IN ('sending','unknown')"
            ).fetchone()[0]
        if unresolved:
            print(
                f'Delivery remains held: {unresolved} uncertain sends require reconciliation'
            )
        else:
            hold.unlink()


if __name__ == '__main__':
    asyncio.run(main())
