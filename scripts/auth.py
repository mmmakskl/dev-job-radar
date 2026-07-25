#!/usr/bin/env python3
"""
Скрипт для первичной авторизации в Telegram через QR-код.
Использует API-ключи и имя сессии из .env.
"""
import argparse
import asyncio
import os

import qrcode
from telethon import TelegramClient

from tg_vacancy_bot import config


async def main(force_relogin: bool = False):
    """Авторизация через QR-код"""
    config.validate_required_settings(
        require_mistral=False,
        require_google_sheets=False,
    )
    print("🔐 Начинаем авторизацию в Telegram")
    print(f"📁 Файл сессии: {config.SESSION_NAME}.session")

    session_file = f"{config.SESSION_NAME}.session"
    if force_relogin and os.path.exists(session_file):
        print(f"⚠️  --force-relogin: удаляем существующую сессию: {session_file}")
        os.remove(session_file)

    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)

    await client.connect()

    if not await client.is_user_authorized():
        print("\n📱 Авторизация через QR-код...")
        print("👇 Отсканируйте QR-код в приложении Telegram:")
        print("   Settings → Devices → Link Desktop Device\n")

        try:
            # Запрашиваем QR-логин
            qr_login = await client.qr_login()

            # Генерируем ASCII QR-код
            qr = qrcode.QRCode(border=1)
            qr.add_data(qr_login.url)
            qr.make()
            qr.print_ascii()

            print("\n⏳ Ожидаем сканирования (120 секунд)...")

            # Ждём авторизации
            await qr_login.wait(timeout=120)

            print("✅ Авторизация успешна!")
            print(f"👤 Вы вошли как: {(await client.get_me()).first_name}")

        except asyncio.TimeoutError:
            print("\n❌ Время ожидания истекло. Запустите скрипт заново.")
            await client.disconnect()
            return
        except Exception as e:
            print(f"\n❌ Ошибка при авторизации: {e}")
            await client.disconnect()
            return
    else:
        print("✅ Сессия уже авторизована!")
        print(f"👤 Вы вошли как: {(await client.get_me()).first_name}")
        await client.disconnect()
        return

    await client.disconnect()
    print(f"\n💾 Сессия сохранена в {session_file}")
    print("🚀 Теперь можно запускать make channels или make run")


def parse_args() -> argparse.Namespace:
    """Разбирает параметры безопасной повторной авторизации."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--force-relogin',
        action='store_true',
        help='удалить существующий session-файл и выполнить вход заново',
    )
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    asyncio.run(main(force_relogin=args.force_relogin))
