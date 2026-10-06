"""Conservative, offline parsing of asset history answers from Ovoav.

The summary is supplied by the service, while the displayed history can be a
limited sample.  No balances or totals are calculated from that sample.
Unrecognised or ambiguous responses are left to the complete-text renderer.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re


ASSETS = {
    '蜡烛变化查询': ('普通蜡烛', ('普通蜡烛', '蜡烛')),
    '季节蜡烛查询': ('季节蜡烛', ('季节蜡烛',)),
    '爱心变化查询': ('爱心', ('爱心',)),
    '升华蜡烛查询': ('升华蜡烛', ('升华蜡烛', '红蜡烛')),
    '点赞爱心查询': ('点赞爱心', ('点赞爱心', '心火')),
    '魔法变化查询': ('魔法', ('魔法',)),
    '代币变化查询': ('代币', ('活动代币', '代币')),
}
_NUMBER = r'[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_STAMP = r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?'
_DELIM = r'[\s|]+'
_HEADER = re.compile(
    r'时间' + _DELIM + r'(?:原因|变化原因)' + _DELIM
    + r'(?:变化前数量|变化前)' + _DELIM
    + r'(?:变化数量|变动数量)' + _DELIM
    + r'(?:变化后数量|变化后)'
)
_AFTER_HEADER = re.compile(
    r'时间' + _DELIM + r'(?:原因|变化原因)' + _DELIM
    + r'(?:变化数量|变动数量)' + _DELIM
    + r'(?:变化后数量|变化后)'
)
_RESOURCE_HEADER = re.compile(
    r'时间' + _DELIM + r'(?:原因|变化原因)' + _DELIM
    + r'(?:变化数量|变动数量)' + _DELIM + r'魔法名称'
)
_INITIAL = re.compile(r'原本有\s*([^【\[\]\n|]{1,24}?)\s*[【\[]\s*(' + _NUMBER + r')\s*[】\]]')
_GAINED = re.compile(r'(?:共)?(?:获得|获取|增加)\s*[【\[]\s*(' + _NUMBER + r')\s*[】\]]')
_SPENT = re.compile(r'(?:消耗|支出|花费)\s*[【\[]\s*(' + _NUMBER + r')\s*[】\]]')


@dataclass(frozen=True)
class CurrencyRow:
    timestamp: str
    reason: str
    before: Decimal | None
    delta: Decimal
    after: Decimal | None
    resource_name: str | None = None


@dataclass(frozen=True)
class CurrencyReport:
    asset: str
    label: str
    unit: str
    start: str | None
    end: str | None
    initial: Decimal | None
    gained: Decimal | None
    spent: Decimal | None
    rows: tuple[CurrencyRow, ...]
    notes: tuple[str, ...]
    raw: str
    # Shape describes columns actually supplied by the upstream service.
    # Missing balances are never reconstructed from partial history.
    shape: str = 'balance'
    empty: bool = False
    message: str | None = None

    @property
    def net(self):
        if self.gained is None or self.spent is None:
            return None
        return self.gained - self.spent


class _AnswerText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'tr', 'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'table'}:
            self.parts.append('\n')
        elif tag in {'td', 'th'}:
            self.parts.append(' | ')
        elif tag == 'br':
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'tr', 'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'table'}:
            self.parts.append('\n')

    def handle_data(self, data):
        self.parts.append(data)


def plain_answer(raw):
    """Keep answer text and table cells readable without discarding its rows."""
    value = str(raw)
    if re.search(r'</?[A-Za-z][^>]*>', value):
        reader = _AnswerText()
        try:
            reader.feed(value)
            reader.close()
            value = ''.join(reader.parts)
        except Exception:
            # The original text is preferable to a partly parsed response.
            value = str(raw)
    return value.replace('\xa0', ' ').replace('\u3000', ' ').strip()


def _decimal(value):
    try:
        return Decimal(value.replace(',', ''))
    except InvalidOperation:
        return None


def _timestamp(value):
    value = ' '.join(value.split())
    try:
        datetime.strptime(value, '%Y-%m-%d %H:%M:%S' if len(value) == 19 else '%Y-%m-%d %H:%M')
    except ValueError:
        return None
    return value


def _summary_amount(pattern, prefix):
    matches = list(pattern.finditer(prefix))
    if len(matches) > 1:
        return False, None
    if not matches:
        return True, None
    return True, _decimal(matches[0].group(1))


def _range(prefix):
    dates = list(re.finditer(_STAMP, prefix))
    if len(dates) not in (0, 2):
        return None
    start = end = None
    if dates:
        start, end = (_timestamp(item.group()) for item in dates)
        between = prefix[dates[0].end():dates[1].start()].strip(' \t\r\n[]【】')
        if not start or not end or start > end or between not in {'至', '到', '~', '～'}:
            return None
    return dates, start, end


def _summary_notes(prefix, initial_matches, dates, aliases):
    """Keep unfamiliar summary text rather than silently losing its warnings."""
    consumed = [(match.start(), match.end()) for match in initial_matches]
    if dates:
        between = prefix[dates[0].end():dates[1].start()].strip(' \t\r\n[]【】')
        if between not in {'至', '到', '~', '～'}:
            return None
        start, end = dates[0].start(), dates[1].end()
        if start and prefix[start - 1] in '[【':
            start -= 1
        if end < len(prefix) and prefix[end] in ']】':
            end += 1
        consumed.append((start, end))
    for pattern in (_GAINED, _SPENT):
        for match in pattern.finditer(prefix):
            end = match.end()
            # The resource immediately following an amount is part of that
            # displayed summary. Resource words in other notes stay intact.
            suffix = re.match(r'\s*(?:' + '|'.join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)) + ')', prefix[end:])
            if suffix:
                end += suffix.end()
            consumed.append((match.start(), end))
    remainder = []
    cursor = 0
    for start, end in sorted(consumed):
        remainder.append(prefix[cursor:start])
        cursor = end
    remainder.append(prefix[cursor:])
    value = ''.join(remainder)
    for boilerplate in ('经查询', '详情如下', '查询成功'):
        value = value.replace(boilerplate, '')
    value = re.sub(r'^\s*您', '', value)
    value = value.strip(' \t\r\n，,。:：;；|')
    return (value,) if value else ()


def _row(value):
    value = value.strip(' \t\r\n|')
    # Take the final three numeric cells, preserving words and numbers in the
    # reason itself.  Extra unparsed columns or a row that does not add up are
    # rejected, instead of being silently discarded.
    match = re.fullmatch(
        '(' + _STAMP + r')' + _DELIM + r'(.+?)' + _DELIM
        + '(' + _NUMBER + ')' + _DELIM + '(' + _NUMBER + ')' + _DELIM
        + '(' + _NUMBER + ')', value, re.DOTALL,
    )
    if not match:
        return None
    timestamp = _timestamp(match.group(1))
    reason = ' '.join(match.group(2).strip(' |').split())
    if not timestamp or not reason or '|' in reason or '\n' in match.group(2):
        return None
    amounts = tuple(_decimal(match.group(i)) for i in (3, 4, 5))
    if any(item is None for item in amounts):
        return None
    before, delta, after = amounts
    if before < 0 or after < 0 or before + delta != after:
        return None
    return CurrencyRow(timestamp, reason, before, delta, after)


def _special_row(value, shape):
    """Parse the two observed four-column formats without inventing cells."""
    value = value.strip(' \t\r\n|')
    if '|' in value:
        cells = [part.strip() for part in value.split('|')]
        if len(cells) != 4 or any(not part or '\n' in part for part in cells):
            return None
        timestamp, reason, delta_value, final = cells
        if not re.fullmatch(_STAMP, timestamp) or not re.fullmatch(_NUMBER, delta_value):
            return None
    elif shape == 'after':
        match = re.fullmatch(
            '(' + _STAMP + r')\s+(.+?)\s+(' + _NUMBER + r')\s+(' + _NUMBER + ')',
            value, re.DOTALL,
        )
        if not match or '\n' in match.group(2):
            return None
        timestamp, reason, delta_value, final = match.groups()
    else:
        # The real flattened magic response uses a signed delta followed by
        # one resource name. Requiring those boundaries avoids guessing how
        # an unfamiliar multi-column/plain response should be split.
        signed = r'[+\-](?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
        match = re.fullmatch(
            '(' + _STAMP + r')\s+(.+?)\s+(' + signed + r')\s+([^\s|]+)',
            value, re.DOTALL,
        )
        if not match or '\n' in match.group(2):
            return None
        timestamp, reason, delta_value, final = match.groups()
    timestamp = _timestamp(timestamp)
    reason = ' '.join(reason.split())
    delta = _decimal(delta_value)
    if not timestamp or not reason or delta is None:
        return None
    if shape == 'after':
        if not re.fullmatch(_NUMBER, final):
            return None
        after = _decimal(final)
        if after is None or after < 0:
            return None
        return CurrencyRow(timestamp, reason, None, delta, after)
    if not final or '\n' in final or '|' in final or re.fullmatch(_NUMBER, final):
        return None
    return CurrencyRow(timestamp, reason, None, delta, None, final)


def _empty_likes(title, value, raw):
    """The observed no-record answer is a status, never an account balance."""
    if title != '点赞爱心查询' or '没有点赞爱心获得的记录' not in value:
        return None
    range_result = _range(value)
    if range_result is None:
        return None
    dates, start, end = range_result
    if not dates or any(pattern.search(value) for pattern in (_INITIAL, _GAINED, _SPENT)):
        return None
    if re.search(r'时间[\s|]+(?:原因|变化原因)', value):
        return None
    # Keep every sentence after the query range, including latency wording;
    # the service's complete rule remains a separate reading note.
    end_at = dates[1].end()
    if end_at < len(value) and value[end_at] in ']】':
        end_at += 1
    status = value[end_at:].strip()
    intro = value[:dates[0].start()].rstrip(' \t\r\n[【')
    intro_notes = ()
    if intro and not re.fullmatch(r'旅人您好[、，,]\s*查看到您在', intro):
        intro_notes = (intro,)
    note = re.search(r'(?:温馨提示|查询提示|提示)\s*[：:]', status)
    notes = ()
    if note:
        notes = (status[note.start():].strip(),)
        status = status[:note.start()].strip()
    latency = status.find('数据查询有一定延迟时间')
    if latency >= 0:
        notes = (status[latency:].strip(),) + notes
        status = status[:latency].rstrip(' ，,。')
    # A change-row-like suffix is inconsistent with the empty result. Its
    # original response must remain intact in the fallback renderer.
    if not re.fullmatch(r'时间内是没有点赞爱心获得的记录的呢', status):
        return None
    notes = intro_notes + notes
    asset = ASSETS[title][0]
    return CurrencyReport(
        asset=asset, label=title, unit=asset, start=start, end=end,
        initial=None, gained=None, spent=None, rows=(), notes=notes,
        raw=raw, shape='empty', empty=True, message=status,
    )


def parse_currency_report(title, raw):
    """Parse complete observed history shapes; unknown answers stay intact."""
    if title not in ASSETS or not isinstance(raw, str) or not raw.strip():
        return None
    asset, aliases = ASSETS[title]
    value = plain_answer(raw)
    # These formatting substitutions only help matching; raw remains intact.
    value = value.replace('−', '-').replace('－', '-').replace('＋', '+')
    value = value.replace('**', '').replace('__', '')
    value = re.sub(r'(?m)^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$', '', value)
    headers = [(match, shape) for pattern, shape in
               ((_HEADER, 'balance'), (_AFTER_HEADER, 'after'), (_RESOURCE_HEADER, 'resource'))
               for match in pattern.finditer(value)]
    if not headers:
        return _empty_likes(title, value, raw)
    if len(headers) != 1:
        return None
    header, shape = headers[0]
    if (shape == 'after' and title != '代币变化查询') or (shape == 'resource' and title != '魔法变化查询'):
        return None
    prefix, body = value[:header.start()].strip(' \r\n|'), value[header.end():].strip(' \r\n|')
    notes = []
    note_match = re.search(r'(?:温馨提示|查询提示|提示)\s*[：:]', prefix)
    if note_match:
        notes.append(prefix[note_match.start():].strip())
        prefix = prefix[:note_match.start()].strip()

    initial_matches = list(_INITIAL.finditer(prefix))
    if len(initial_matches) > 1:
        return None
    initial = None
    if initial_matches:
        initial_match = initial_matches[0]
        if initial_match.group(1).strip() not in aliases:
            return None
        initial = _decimal(initial_match.group(2))
        if initial is None or initial < 0:
            return None
    # A generic table cannot determine which magic or token type it counts.
    # Require the upstream summary to explicitly identify the single asset.
    if shape != 'resource' and asset in {'魔法', '代币'} and not initial_matches:
        return None
    if shape == 'resource' and (initial_matches or _GAINED.search(prefix) or _SPENT.search(prefix)):
        return None

    # Catch other resource summaries without treating the 蜡烛 in 季节蜡烛
    # or the 爱心 in 点赞爱心 as a different resource.
    resource_names = {name for _, names in ASSETS.values() for name in names}
    resource_pattern = '|'.join(re.escape(name) for name in sorted(resource_names, key=len, reverse=True))
    if any(match.group() not in aliases for match in re.finditer(resource_pattern, prefix)):
        return None

    ok_gain, gained = _summary_amount(_GAINED, prefix)
    ok_spent, spent = _summary_amount(_SPENT, prefix)
    if not ok_gain or not ok_spent or (gained is not None and gained < 0):
        return None
    spent = abs(spent) if spent is not None else None
    range_result = _range(prefix)
    if range_result is None:
        return None
    dates, start, end = range_result
    remainder_notes = _summary_notes(prefix, initial_matches, dates, aliases)
    if remainder_notes is None:
        return None
    # Remove only the exact, observed resource-table introduction; unfamiliar
    # summary wording remains a note rather than being silently discarded.
    observed_intro = len(remainder_notes) == 1 and (
        shape == 'resource' and re.fullmatch(
            r'旅人您好[、，,]\s*查询到您\s*游戏内魔法道具变动记录如下', remainder_notes[0])
        or shape == 'after' and re.fullmatch(
            r'(?:活动)?代币\s*变化[\s，,。:：;；]*明细如下', remainder_notes[0])
    )
    if not observed_intro:
        notes.extend(remainder_notes)

    # Split on row timestamps, never on prose whitespace, so flattened plain
    # answers work as well as HTML and Markdown tables.
    stamps = list(re.finditer(_STAMP, body))
    if not stamps or body[:stamps[0].start()].strip(' \r\n|'):
        return None
    rows = []
    for index, stamp in enumerate(stamps):
        stop = stamps[index + 1].start() if index + 1 < len(stamps) else len(body)
        item = _row(body[stamp.start():stop]) if shape == 'balance' else _special_row(body[stamp.start():stop], shape)
        if item is None:
            return None
        rows.append(item)
    return CurrencyReport(
        asset=asset, label=title, unit=asset, start=start, end=end,
        initial=initial, gained=gained, spent=spent, rows=tuple(rows),
        notes=tuple(notes), raw=raw, shape=shape,
    )
