"""Readable asset totals, magic ledgers and empty-query cards in the Sky theme."""
from decimal import Decimal
import re

from PIL import ImageDraw

from .rendering import backdrop, finish, font, header, panel, text, wrap, INK, MUTED, GOLD

GREEN = '#9ed9c2'
RED = '#efb2a7'


def number(value, *, signed=False):
    if value is None:
        return '未提供'
    value = Decimal(value)
    result = format(value, ',f').rstrip('0').rstrip('.') if value != value.to_integral_value() else format(value, ',.0f')
    return ('+' if signed and value > 0 else '') + result


def fitted(draw, xy, value, width, *, size=28, color=INK):
    value = str(value)
    while size > 14 and font(size).getlength(value) > width:
        size -= 1
    text(draw, xy, value, size, color)


def centered(draw, x, y, value, width, size=27, color=INK):
    value = str(value)
    while size > 14 and font(size).getlength(value) > width:
        size -= 1
    text(draw, (x + max(0, (width - font(size).getlength(value)) / 2), y), value, size, color)


def _notes(report):
    lines = []
    if report.gained is not None or report.spent is not None:
        lines.extend(wrap('上方为接口的区间汇总；明细仅为本次返回的记录。', 20, 824))
    for note in report.notes:
        note = re.sub(r'^(?:温馨提示|查询提示|提示)\s*[：:]\s*', '', note).strip()
        for part in re.split(r'(?<!\d)(?=\d+[.、](?!\d))', note):
            part = re.sub(r'^\d+[.、]\s*', '', part).strip()
            if not part:
                continue
            wrapped = wrap(part, 20, 798)
            lines.extend(['· ' + wrapped[0], *('  ' + line for line in wrapped[1:])])
    return lines


def _period(draw, report):
    period = f'{report.start}  →  {report.end}' if report.start and report.end else '统计时间：接口未提供完整区间'
    fitted(draw, (54, 188), period, 892, size=19, color=MUTED)


def _summary(image, report):
    panel(image, (48, 234, 952, 479), fill=(13, 32, 47, 174))
    draw = ImageDraw.Draw(image)
    text(draw, (78, 257), report.asset, 27)
    fitted(draw, (78, 438), f'期初数量：{number(report.initial)}', 820, size=21, color=MUTED)
    values = (
        ('累计获得', number(report.gained, signed=True), GREEN if report.gained is not None else MUTED),
        ('累计消耗', number(report.spent), RED if report.spent is not None else MUTED),
        ('区间净变化', number(report.net, signed=True), MUTED if report.net is None else GREEN if report.net >= 0 else RED),
    )
    for index, (label, value, color) in enumerate(values):
        x = 78 + index * 288
        if index:
            draw.line((x - 15, 312, x - 15, 411), fill='#48606e')
        text(draw, (x, 314), label, 20, MUTED)
        fitted(draw, (x - 1, 350), value, 253, size=49, color=color)


def _note_panel(image, top, lines, *, last=True):
    height = 64 + len(lines) * 29 if last else 72
    panel(image, (48, top, 952, top + height), radius=23, fill=(15, 33, 44, 115))
    draw = ImageDraw.Draw(image)
    text(draw, (78, top + 18), '阅读提示', 19, GOLD)
    if last:
        for index, line in enumerate(lines):
            text(draw, (78, top + 49 + index * 29), line, 20, MUTED)
    else:
        text(draw, (193, top + 18), '其余明细及接口提示见下一页', 19, MUTED)


def _empty_card(title, report, notes):
    # No rows does not establish the current balance or a numeric interval total.
    top = 478
    image = backdrop(top + 64 + len(notes) * 29 + 111).copy()
    header(image, title, '资产记录  ·  查询结果')
    _period(ImageDraw.Draw(image), report)
    panel(image, (48, 234, 952, 454), fill=(13, 32, 47, 174))
    draw = ImageDraw.Draw(image)
    text(draw, (78, 260), report.asset, 25, MUTED)
    text(draw, (78, 309), '此区间暂无获得记录', 39, GOLD)
    text(draw, (80, 376), '这是所选时间段内的点赞爱心获得记录。', 23, MUTED)
    _note_panel(image, top, notes)
    return [finish(image, 1, 1)]


_COLUMNS = {
    'balance': (
        ('timestamp', '时间', 78, 174), ('reason', '原因', 271, 212),
        ('before', '变化前', 518, 112), ('delta', '变化量', 646, 104), ('after', '变化后', 776, 112),
    ),
    'after': (
        ('timestamp', '时间', 78, 174), ('reason', '原因', 271, 260),
        ('delta', '变化量', 592, 128), ('after', '变化后', 778, 132),
    ),
    'resource': (
        ('timestamp', '时间', 78, 174), ('resource_name', '魔法名称', 271, 192),
        ('reason', '原因', 501, 213), ('delta', '变化量', 783, 116),
    ),
}


def ledger_cards(title, report):
    """Keep service totals separate from rows; preserve missing fields.

    Content that cannot fit the boundaries returns None for the complete-text
    renderer, rather than truncating rows or notes.
    """
    note_lines = _notes(report)
    if len(note_lines) > 14:
        return None
    if report.empty:
        return _empty_card(title, report, note_lines)
    columns = _COLUMNS.get(report.shape)
    if columns is None:
        return None
    prepared = []
    for row in report.rows:
        labels = {}
        for key, _, _, width in columns:
            if key in {'reason', 'resource_name'}:
                labels[key] = wrap(getattr(row, key) or '未提供', 24, width)
                if len(labels[key]) > 12:
                    return None
            elif key != 'timestamp' and font(14).getlength(number(getattr(row, key), signed=key == 'delta')) > width:
                return None
        height = max(84, 28 * max((len(lines) for lines in labels.values()), default=1) + 30)
        prepared.append((row, labels, height))
    batches = [[]]
    used = 0
    for item in prepared:
        if batches[-1] and used + item[2] > 1450:
            batches.append([])
            used = 0
        batches[-1].append(item)
        used += item[2]
    results = []
    resource = report.shape == 'resource'
    for page_index, batch in enumerate(batches, 1):
        body_height = max(100, sum(item[2] for item in batch))
        table_top = 397 if resource else 500
        table_bottom = table_top + 110 + body_height
        notes_top = table_bottom + 24
        is_last = page_index == len(batches)
        notes_height = 64 + len(note_lines) * 29 if is_last else 72
        image = backdrop(notes_top + notes_height + 111).copy()
        header(image, title, '道具记录  ·  按魔法类型查看' if resource else '资产记录  ·  汇总与明细')
        _period(ImageDraw.Draw(image), report)
        if resource:
            panel(image, (48, 234, 952, 373), fill=(13, 32, 47, 174))
            draw = ImageDraw.Draw(image)
            text(draw, (78, 254), '魔法道具变化', 27)
            text(draw, (78, 302), f'本次返回 {len(report.rows)} 条记录', 30, GOLD)
            fitted(draw, (481, 313), '不同魔法分项展示，不混合计算余额', 434, size=20, color=MUTED)
        else:
            _summary(image, report)
        panel(image, (48, table_top, 952, table_bottom), fill=(12, 30, 44, 181))
        draw = ImageDraw.Draw(image)
        text(draw, (78, table_top + 23), '变化明细', 27)
        fitted(draw, (669, table_top + 29), f'本次返回 {len(report.rows)} 条', 254, size=19, color=MUTED)
        for key, label, x, width in columns:
            if key in {'timestamp', 'reason', 'resource_name'}:
                text(draw, (x, table_top + 72), label, 19, MUTED)
            else:
                centered(draw, x, table_top + 72, label, width, 19, MUTED)
        y = table_top + 110
        for index, (row, labels, row_height) in enumerate(batch):
            if index % 2 == 0:
                panel(image, (62, y, 938, y + row_height), radius=12, fill=(197, 222, 226, 9))
                draw = ImageDraw.Draw(image)
            for key, _, x, width in columns:
                if key == 'timestamp':
                    date, _, clock = row.timestamp.partition(' ')
                    text(draw, (x, y + 16), date, 20, INK)
                    text(draw, (x, y + 43), clock, 19, MUTED)
                elif key in labels:
                    for line_index, line in enumerate(labels[key]):
                        text(draw, (x, y + 25 + line_index * 28), line, 24)
                else:
                    value = getattr(row, key)
                    color = GREEN if value is not None and value >= 0 else RED
                    if key != 'delta' or value is None:
                        color = INK
                    centered(draw, x, y + 24, number(value, signed=key == 'delta'), width, 30 if key == 'delta' else 27, color)
            y += row_height
        if not batch:
            text(draw, (78, y + 28), '接口未返回变化明细', 25, MUTED)
        _note_panel(image, notes_top, note_lines, last=is_last)
        results.append(finish(image, page_index, len(batches)))
    return results
