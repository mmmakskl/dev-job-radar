#!/usr/bin/env python3
"""Evaluate a labeled synthetic corpus offline by default; never export or notify."""

import argparse
import asyncio
import json
from pathlib import Path

from tg_vacancy_bot.shadow_evaluation import evaluate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--corpus', type=Path, default=Path('tests/fixtures/catalog_evaluation.json')
    )
    parser.add_argument(
        '--predictions',
        type=Path,
        help='JSON mapping corpus IDs to universal payloads; no API calls',
    )
    parser.add_argument('--max-examples', type=int, default=30)
    parser.add_argument('--max-calls', type=int, default=0)
    parser.add_argument(
        '--live',
        action='store_true',
        help='Explicitly enable bounded Mistral calls using the shared daily quota',
    )
    args = parser.parse_args()
    if args.live and (args.max_calls <= 0 or args.predictions):
        parser.error('--live requires --max-calls > 0 and excludes --predictions')
    if not args.live and args.max_calls:
        parser.error('--max-calls requires --live')
    analyzer = None
    if args.live:
        from tg_vacancy_bot.llm.universal import analyze_universal_text

        analyzer = analyze_universal_text
    report = asyncio.run(
        evaluate(
            json.loads(args.corpus.read_text()),
            predictions=(
                json.loads(args.predictions.read_text()) if args.predictions else None
            ),
            analyzer=analyzer,
            max_examples=args.max_examples,
            max_calls=args.max_calls,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
