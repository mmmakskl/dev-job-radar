import asyncio
import argparse
import json
from telethon import TelegramClient

from tg_vacancy_bot import config

# Ключевые слова для фильтрации каналов
KEYWORDS = [
    'job',
    'ваканс',
    'вакансия',
    'вакансии',
    'career',
    'hr',
    'work',
    'remote',
    'go',
    'golang',
    'it',
    'работа',
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Найти каналы/группы среди текущих Telegram dialogs.',
    )
    parser.add_argument(
        '--query',
        action='append',
        default=[],
        help='Дополнительная строка поиска по названию. Можно указать несколько раз.',
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Показать все каналы и группы без фильтра по названию.',
    )
    return parser.parse_args()


def matches_dialog_name(name: str, queries: list[str], show_all: bool) -> bool:
    if show_all:
        return True

    dialog_name_lower = name.lower()
    search_terms = [*KEYWORDS, *(query.lower() for query in queries)]
    return any(keyword in dialog_name_lower for keyword in search_terms)


async def fetch_channels(queries: list[str], show_all: bool):
    """Сканирует все диалоги пользователя и находит каналы/группы с вакансиями"""
    config.validate_required_settings(
        require_mistral=False,
        require_google_sheets=False,
    )
    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    
    print("🔄 Подключаюсь к Telegram...")
    await client.connect()
    
    # Проверяем, авторизован ли клиент
    if not await client.is_user_authorized():
        print("❌ Сессия не авторизована. Запустите make auth для авторизации.")
        await client.disconnect()
        return
    print(f"✓ Подключено к Telegram как: {await client.get_me()}")
    
    found_channels = []
    
    print("\nСканируем диалоги...")
    async for dialog in client.iter_dialogs():
        # Фильтруем только каналы и группы
        if not (dialog.is_channel or dialog.is_group):
            continue
        
        if not matches_dialog_name(dialog.name, queries, show_all):
            continue
        
        # Добавляем найденный канал
        channel_info = {
            "name": dialog.name,
            "username": dialog.entity.username if hasattr(dialog.entity, 'username') else None,
            "id": dialog.id
        }
        found_channels.append(channel_info)
        identifier = channel_info['username'] or channel_info['id']
        print(
            f"  ✓ Найден: {dialog.name} "
            f"(@{channel_info['username'] or 'закрытый'}, id={channel_info['id']}) "
            f"-> TARGET_CHANNELS: {identifier}"
        )
    
    # Сохраняем в файл
    output_file = 'found_channels.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(found_channels, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"Найдено и сохранено каналов: {len(found_channels)}")
    print(f"Файл: {output_file}")
    print(f"{'='*60}")
    
    await client.disconnect()

if __name__ == '__main__':
    args = parse_args()
    asyncio.run(fetch_channels(args.query, args.all))
