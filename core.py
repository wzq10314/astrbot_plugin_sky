import calendar
import json
import sqlite3
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))


class SkyError(Exception):
    """Only safe, user-facing messages belong here."""


def now():
    return datetime.now(TZ)


def friend_code(value):
    code = re.sub(r'[\s-]', '', value).upper()
    if not re.fullmatch(r'[A-Z0-9]{12}', code):
        raise SkyError('好友码应为 12 位字母或数字，例如 ABCD-EFGH-IJKL。')
    return '-'.join(code[i:i+4] for i in range(0, 12, 4))


def player_id(value):
    if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
        raise SkyError('请提供游戏长 ID：xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx。')
    return value.lower()


def pretty(value):
    if isinstance(value, dict):
        return '\n'.join(f'{k}：{pretty(v)}' for k, v in value.items())
    if isinstance(value, list):
        return '\n'.join(pretty(v) for v in value)
    return str(value if value is not None else '未知')


class Store:
    """One short transaction per mutation; no network await inside transactions."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (space TEXT, owner TEXT, body TEXT NOT NULL, PRIMARY KEY(space,owner))')
        self.db.commit()

    def get(self, space, owner, default=None):
        row = self.db.execute('SELECT body FROM state WHERE space=? AND owner=?', (space, owner)).fetchone()
        return json.loads(row[0]) if row else json.loads(json.dumps(default))

    def put(self, space, owner, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES (?,?,?)', (space, owner, json.dumps(value, ensure_ascii=False)))

    def all(self, space):
        return [(owner, json.loads(body)) for owner, body in self.db.execute('SELECT owner,body FROM state WHERE space=?', (space,))]

    def close(self):
        self.db.close()


def due_jobs(date, config):
    """Minute schedule, explicitly China time, no dependency on container timezone."""
    minute = date.strftime('%H:%M')
    jobs = []
    for key in ('daily', 'grandma', 'sacrifice', 'shard'):
        if minute in [s.strip() for s in config[key + '_times'].split(',')]:
            if key == 'sacrifice' and date.weekday() != 6:
                continue
            jobs.append(key)
    if config.get('shard_advance_reminder', True):
        target = date + timedelta(minutes=10)
        day = (target.weekday()+1) % 7
        first = target.day <= 15
        times = []
        if day == 0:
            times = ['07:08','13:08','19:08']
        elif first and day == 6:
            times = ['10:08','14:08','22:08']
        elif not first and day == 5:
            times = ['11:08','17:08','23:08']
        elif first and day == 2:
            times = ['09:08','14:08','19:08']
        elif not first and day == 3:
            times = ['09:08','15:08','21:08']
        if target.strftime('%H:%M') in times:
            jobs.append('shard_before')
    return jobs
