#!/usr/bin/env python3
"""Выгрузка истории ОТКРЫТЫХ Telegram-каналов через веб-версию t.me/s/<канал> (без аккаунта).
python tgscreen/scrape.py tgscreen/channels.txt tg_raw 2024-11-24
Пишет tg_raw/<канал>.json: {channel, title, ok, messages:[{id, date, text}]} — от новых к старым до даты начала."""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from bs4 import BeautifulSoup

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'}
MAXMSG = 6000


def page(name, before=None):
    url = f'https://t.me/s/{name}' + (f'?before={before}' if before else '')
    for a in range(6):
        try:
            r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
            if r.status_code == 429:
                time.sleep(10 * (a + 1)); continue
            if r.status_code != 200:
                return None, None
            return r.url, r.text
        except Exception:
            time.sleep(5)
    return None, None


def parse(html):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.select_one('.tgme_channel_info_header_title')
    out = []
    for m in soup.select('.tgme_widget_message'):
        post = m.get('data-post') or ''
        if '/' not in post:
            continue
        mid = int(post.split('/')[-1])
        t = m.select_one('time[datetime]')
        tx = m.select_one('.tgme_widget_message_text')
        text = tx.get_text('\n') if tx else ''
        out.append({'id': mid, 'date': t['datetime'] if t else None, 'text': text,
                    'fwd': bool(m.select_one('.tgme_widget_message_forwarded_from'))})
    return (title.get_text(strip=True) if title else ''), out


def scrape(name, start, outdir):
    fn = os.path.join(outdir, f'{name}.json')
    url, html = page(name)
    if not html or '/s/' not in (url or ''):
        json.dump({'channel': name, 'ok': False, 'why': 'нет веб-версии / не существует'}, open(fn, 'w'))
        return name, 0
    title, msgs = parse(html)
    if not msgs:
        json.dump({'channel': name, 'ok': False, 'why': 'нет сообщений в веб-версии', 'title': title}, open(fn, 'w'))
        return name, 0
    seen = {m['id'] for m in msgs}
    while len(msgs) < MAXMSG:
        before = min(m['id'] for m in msgs)
        if before <= 1:
            break
        oldest = min((m['date'] for m in msgs if m['date']), default=None)
        if oldest and oldest[:10] < start:
            break
        time.sleep(0.7)
        _, h = page(name, before)
        if not h:
            break
        _, more = parse(h)
        more = [m for m in more if m['id'] not in seen]
        if not more:
            break
        seen |= {m['id'] for m in more}
        msgs += more
    msgs = sorted([m for m in msgs if not m['date'] or m['date'][:10] >= start], key=lambda m: m['id'])
    json.dump({'channel': name, 'title': title, 'ok': True, 'messages': msgs}, open(fn, 'w'), ensure_ascii=False)
    return name, len(msgs)


def run(chans, start, outdir):
    todo = [c for c in dict.fromkeys(chans) if not os.path.exists(os.path.join(outdir, f'{c}.json'))]
    print(f'к выгрузке {len(todo)}', flush=True)
    with ThreadPoolExecutor(4) as ex:
        for name, n in ex.map(lambda c: scrape(c, start, outdir), todo):
            print(f'{datetime.utcnow():%H:%M:%S} {name}: {n} сообщений', flush=True)


def mentions(outdir, known, limit=60):
    """Снежный ком: каналы, на которые ссылаются уже выгруженные (только похожие на сигнальные)."""
    cnt = {}
    for fn in os.listdir(outdir):
        d = json.load(open(os.path.join(outdir, fn)))
        for m in d.get('messages', []):
            for u in re.findall(r't\.me/(?:s/)?([A-Za-z][\w]{4,31})|@([A-Za-z][\w]{4,31})', m['text']):
                u = u[0] or u[1]
                if u.lower().endswith('bot') or u.lower() in known:
                    continue
                if re.search(r'signal|futur|trad|binance|crypto|сигнал', u, re.I):
                    cnt[u] = cnt.get(u, 0) + 1
    return [u for u, _ in sorted(cnt.items(), key=lambda x: -x[1])[:limit]]


def main():
    chans = [c.strip().lstrip('@') for c in open(sys.argv[1]) if c.strip() and not c.startswith('#')]
    outdir, start = sys.argv[2], sys.argv[3]
    os.makedirs(outdir, exist_ok=True)
    run(chans, start, outdir)
    known = {c.lower() for c in chans} | {f[:-5].lower() for f in os.listdir(outdir)}
    more = mentions(outdir, known)
    print('снежный ком:', ', '.join(more), flush=True)
    run(more, start, outdir)


if __name__ == '__main__':
    main()
