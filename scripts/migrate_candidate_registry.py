"""Offline additive registry migration. Defaults to an isolated dry-run."""

import argparse
import json

from tg_vacancy_bot.registry_migration import backup_database, import_legacy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-db', required=True)
    parser.add_argument('--premium-db')
    parser.add_argument('--search-db')
    parser.add_argument('--state-jsonl', help='Append-only exported dedupe state')
    parser.add_argument('--text-hash-ttl-days', type=int, default=30)
    parser.add_argument(
        '--apply', action='store_true', help='Apply; default is dry-run'
    )
    parser.add_argument(
        '--backup', help='New destination for a consistent pre-migration backup'
    )
    args = parser.parse_args()
    if args.backup:
        backup_database(args.candidate_db, args.backup)
    print(
        json.dumps(
            import_legacy(
                args.candidate_db,
                premium_path=args.premium_db,
                search_path=args.search_db,
                state_path=args.state_jsonl,
                text_hash_ttl_days=args.text_hash_ttl_days,
                dry_run=not args.apply,
            ),
            indent=2,
        )
    )


if __name__ == '__main__':
    main()
