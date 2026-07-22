import asyncio
import qrcode
from telethon import TelegramClient
from tg_vacancy_bot import config

async def main():
    """
    Основная функция для работы с Telegram userbot.
    Авторизация через QR-код, затем получение списка диалогов
    и вывод первых 10 чатов с непрочитанными сообщениями.
    """
    # Создаем клиент с указанными credentials
    config.validate_required_settings(
        require_mistral=False,
        require_google_sheets=False,
    )
    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    
    # Подключаемся к Telegram
    await client.connect()
    
    # Проверяем, авторизован ли пользователь
    if not await client.is_user_authorized():
        print("Требуется авторизация. Генерируем QR-код...\n")
        
        # Запускаем процесс авторизации через QR-код
        qr_login = await client.qr_login()
        
        # Генерируем ASCII QR-код в терминале
        qr = qrcode.QRCode()
        qr.add_data(qr_login.url)
        qr.print_ascii()
        
        print("\nОтсканируйте QR-код с помощью камеры вашего телефона в Telegram.")
        print("Ожидание сканирования (таймаут: 120 секунд)...\n")
        
        try:
            # Ожидаем сканирования с таймаутом 120 секунд
            await qr_login.wait(120)
            print("Авторизация успешна!")
        except asyncio.TimeoutError:
            print("Таймаут ожидания. QR-код не был отсканирован.")
            await client.disconnect()
            return
    else:
        print("Сессия уже активна. Авторизация не требуется.")
    
    print("\nПоиск чатов с непрочитанными сообщениями...\n")
    
    # Счетчик найденных чатов
    found_count = 0
    
    # Перебираем все диалоги
    async for dialog in client.iter_dialogs():
        # Проверяем наличие непрочитанных сообщений
        if dialog.unread_count > 0:
            found_count += 1
            print(f"{found_count}. {dialog.name} — непрочитанных: {dialog.unread_count}")
            
            # Останавливаемся после 10 чатов
            if found_count >= 10:
                break
    
    if found_count == 0:
        print("Нет чатов с непрочитанными сообщениями.")
    else:
        print(f"\nВсего найдено: {found_count} чат(ов) с непрочитанными сообщениями.")
    
    # Отключаемся от клиента
    await client.disconnect()
    print("\nРабота завершена.")


if __name__ == '__main__':
    asyncio.run(main())
