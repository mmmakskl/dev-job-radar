import asyncio
import json
from telethon import TelegramClient

from tg_vacancy_bot import config

# Ключевые слова для фильтрации каналов
KEYWORDS = ['job', 'ваканс', 'career', 'hr', 'work', 'go', 'golang', 'it', 'работа']

async def fetch_channels():
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
        
        # Проверяем название на ключевые слова
        dialog_name_lower = dialog.name.lower()
        if not any(keyword in dialog_name_lower for keyword in KEYWORDS):
            continue
        
        # Добавляем найденный канал
        channel_info = {
            "name": dialog.name,
            "username": dialog.entity.username if hasattr(dialog.entity, 'username') else None,
            "id": dialog.id
        }
        found_channels.append(channel_info)
        print(f"  ✓ Найден: {dialog.name} (@{channel_info['username'] or 'закрытый'})")
    
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
    asyncio.run(fetch_channels())
