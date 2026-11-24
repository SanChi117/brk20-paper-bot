#!/usr/bin/env python3
"""Выгрузка истории закрытых Telegram-каналов вашим аккаунтом (только чтение) — для проверки сигналов авторов.

Запуск на сервере:  cd /root/brk20 && bash tg.sh
Один раз спросит api_id / api_hash (с my.telegram.org), номер телефона, код из Telegram и облачный пароль, если он есть.
Всё это остаётся только на сервере (папка tg_private/, права 600) и никуда не отправляется.
Результат: файлы tg_exports/<канал>.json — их можно скачать в веб-терминале (Настройки → Выгрузки Telegram).
"""
import asyncio
import json
import os
import re
import sys
from datetime import timezone

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIV = os.path.join(DIR, 'tg_private')
OUT = os.path.join(DIR, 'tg_exports')
CONF = os.path.join(PRIV, 'api.json')


def ask(prompt):
    return input(prompt).strip()


def api_creds():
    os.makedirs(PRIV, exist_ok=True); os.chmod(PRIV, 0o700)
    if os.path.exists(CONF):
        c = json.load(open(CONF))
        return int(c['api_id']), c['api_hash']
    print('\nНужны api_id и api_hash вашего аккаунта. Где взять: my.telegram.org → API development tools →')
    print('создать приложение (название любое) → скопировать App api_id и App api_hash.\n')
    api_id = ask('api_id: '); api_hash = ask('api_hash: ')
    json.dump({'api_id': api_id, 'api_hash': api_hash}, open(CONF, 'w')); os.chmod(CONF, 0o600)
    return int(api_id), api_hash


def slug(s):
    s = re.sub(r'[^\w\-]+', '_', s, flags=re.U).strip('_')
    return s[:60] or 'channel'


async def main():
    from telethon import TelegramClient
    from telethon.tl.types import Channel, Chat
    api_id, api_hash = api_creds()
    client = TelegramClient(os.path.join(PRIV, 'user'), api_id, api_hash)
    await client.start()          # спросит телефон, код и облачный пароль (если есть) — только при первом входе
    os.chmod(os.path.join(PRIV, 'user.session'), 0o600)
    me = await client.get_me()
    print(f'\nВход выполнен: {me.first_name or ""} (@{me.username or "—"})\n')
    dialogs = [d for d in await client.get_dialogs() if isinstance(d.entity, (Channel, Chat))]
    for k, d in enumerate(dialogs, 1):
        kind = 'канал' if getattr(d.entity, 'broadcast', False) else 'группа'
        print(f'{k:3d}. {d.name}  ({kind})')
    pick = ask('\nНомера каналов для выгрузки через запятую (например 3,7,12): ')
    idx = [int(x) for x in re.findall(r'\d+', pick) if 0 < int(x) <= len(dialogs)]
    if not idx:
        print('Ничего не выбрано.'); return
    os.makedirs(OUT, exist_ok=True)
    for k in idx:
        d = dialogs[k - 1]
        print(f'\nВыгружаю «{d.name}»…', flush=True)
        msgs, n_media = [], 0
        async for m in client.iter_messages(d.entity, limit=None):
            text = m.message or ''
            if m.media and not text:
                n_media += 1
            msgs.append({'id': m.id, 'date': m.date.astimezone(timezone.utc).isoformat() if m.date else None,
                         'edit_date': m.edit_date.astimezone(timezone.utc).isoformat() if m.edit_date else None,
                         'reply_to': m.reply_to.reply_to_msg_id if m.reply_to else None,
                         'text': text, 'has_media': bool(m.media), 'views': m.views})
            if len(msgs) % 1000 == 0:
                print(f'  …{len(msgs)} сообщений', flush=True)
        msgs.reverse()
        fn = os.path.join(OUT, f'{slug(d.name)}_{d.entity.id}.json')
        json.dump({'channel': d.name, 'id': d.entity.id, 'count': len(msgs), 'media_only': n_media,
                   'messages': msgs}, open(fn, 'w'), ensure_ascii=False)
        print(f'  готово: {len(msgs)} сообщений ({n_media} только с картинкой) → {fn}')
    await client.disconnect()
    print('\nГОТОВО. Скачайте файлы в веб-терминале: Настройки → Выгрузки Telegram, и пришлите их в чат.')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\nОстановлено.')
