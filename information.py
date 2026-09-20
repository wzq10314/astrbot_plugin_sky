import difflib
import calendar
import html
import json
import math
import asyncio
import re
from pathlib import Path
import aiohttp
from collections import Counter
from datetime import datetime, timedelta
from .core import SkyError, TZ, now, pretty
from .network import WINGS, DETAIL


def as_date(value):
    try:
        text = str(value).replace('/', '-').replace('Z','+00:00')
        try:
            date = datetime.fromisoformat(text)
        except ValueError:
            date = datetime.strptime(text, '%Y-%m-%d %H:%M:%S' if ' ' in text else '%Y-%m-%d')
        return date.astimezone(TZ) if date.tzinfo else date.replace(tzinfo=TZ)
    except (TypeError, ValueError):
        raise SkyError('数据源日期格式异常。') from None


async def announcement(http):
    records = []
    for kind, label in [('pc','PC'),('live','正式服'),('qa','测试服')]:
        try:
            data = await http.json(f'https://ma75.update.netease.com/game_notice/announcement_{kind}.json')
            content = str(data.get('OtherChannelMessage') or data.get('NeteaseMessage') or '暂无内容')
            records.append((label, str(data.get('Title') or '公告'), html.unescape(content)))
        except SkyError:
            records.append((label,'获取失败','该公告源暂时不可用'))
    text = '\n\n'.join(f'【{label}】{title}\n{body}' for label,title,body in records)
    differences = list(difflib.unified_diff(records[1][2].splitlines(), records[2][2].splitlines(),
                                          fromfile='正式服',tofile='测试服',lineterm=''))
    if differences:
        text += '\n\n【正式服与测试服差异】\n' + '\n'.join(differences)
    return text


async def status(http):
    data = await http.json('https://live-queue-sky-merge.game.163.com/queue?type=json',
                           headers={'X-Sky-content':'c2t5R2FtZQ=='})
    if data.get('text') == 'enter':
        return '当前光遇服务器畅通，无需排队。'
    if data.get('text') == 'queue':
        return f'当前排队人数：{data.get("pos","未知")}\n预计等待：{data.get("wait_time","未知")} 秒'
    raise SkyError('服务器状态接口返回未知状态，可能正在维护。')


async def wing_counts(http):
    data = await http.json(WINGS)
    counts = Counter(item.get('一级标签','其他') for item in data)
    return f'总光翼：{len(data)}\n永久翼：{counts["复刻永久"]+counts["普通永久"]}\n' + pretty(dict(counts))


def valid_names(data):
    if not isinstance(data,dict):
        return {}
    return {str(k).strip().lower():v.strip() for k,v in data.items()
            if isinstance(v,str) and re.search(r'[\u4e00-\u9fff]',v)}


async def wing_names(http):
    # Ship the upstream snapshot so GitCode errors cannot erase all translations.
    try:
        names = valid_names(json.loads((Path(__file__).parent/'assets/wing_names.json').read_text(encoding='utf-8')))
    except (OSError,ValueError):
        names = {}
    try:
        async with asyncio.timeout(3):
            names.update(valid_names(await http.resource('GuangYi')))
    except (SkyError, aiohttp.ClientError, TimeoutError, OSError):
        pass
    return names


async def wings(http, identifier, details=False):
    names = await wing_names(http)
    missing = set()
    def name(value):
        identifier = str(value).strip()
        translated = names.get(identifier.lower())
        if translated:
            return translated
        missing.add(identifier)
        return f'未收录中文名（{identifier}）'
    if not details:
        data = await http.kevcore('sky-wings-cn', {'role_id':identifier})
        if not isinstance(data, dict):
            raise SkyError('光翼接口返回格式异常。')
        total = data.get('role_total',data.get('wing_count',0))
        collected = data.get('role_collected',data.get('collected_count',0))
        rate = data.get('collection_rate')
        if rate is None or str(rate).strip() in ('','未知'):
            try:
                t, c = float(total),float(collected)
                rate = f'{c/t*100:.2f}%' if math.isfinite(t) and math.isfinite(c) and 0 <= c <= t and t > 0 else '未知'
            except (ValueError, TypeError, ZeroDivisionError):
                rate = '未知'
        lines = [f'角色：{identifier}', f'总光翼：{total}  已收集：{collected}',
                 f'未收集：{data.get("role_uncollected",data.get("uncollected_count","未知"))}',
                 f'已存入：{data.get("deposited_count",0)}', f'收集率：{rate}']
        labels = {'total':'总数','collected':'已收集','uncollected':'未收集','deposited':'已存入',
                  'unknown':'未知','known_uncollected':'已知未收集'}
        for field, fallback, category, label in [('map_groups','map_wings','map_wings','地图光翼'),
                                              ('permanent_groups','permanent_wings','spirit_wings','永久光翼')]:
            groups = data.get(field) or [{'name':label, **((data.get('categories') or {}).get(category) or data.get(fallback) or {})}]
            for group in groups:
                lines.append(str(group.get('name',label)) + '：' + ' / '.join(f'{v} {group.get(k,0)}' for k,v in labels.items()))
        for group in data.get('uncollected_by_map') or []:
            lines.append('\n未收集 · '+str(group.get('map','未知地图')))
            translated = [name(wing.get('wing_id','未知')) for wing in group.get('wings',[])]
            lines.extend(f'{label} × {count}' if count > 1 else label for label,count in Counter(translated).items())
        if missing:
            lines.append('\n部分新光翼尚未收录中文名，括号中保留原始编号。')
        return '\n'.join(lines)
    response = await http.json(DETAIL,params={'id':identifier})
    if not response.get('success'):
        raise SkyError('原版光翼详情接口查询失败，请确认短 ID 或稍后再试。')
    raw = response['data']['result']
    result = json.loads(raw) if isinstance(raw,str) else raw
    owned = {v['name']:v for v in result.get('wing_buffs',[])}
    catalog = await http.json(WINGS)
    all_ids = list(dict.fromkeys([v['光翼名字'] for v in catalog] + ['l_SunsetEnd_1','l_CandleSpace_0','l_MainStreet_0']))
    rows = [owned.get(k,{'name':k,'collected':False,'deposited':False,'last_conversion':0}) for k in all_ids]
    groups = {}
    prefixes = {'Prairie':'云野','DayHubCave':'云野','Rain':'雨林','Skyway':'雨林','Dusk':'暮土','Sunset':'霞谷',
                'Night':'禁阁','Credits':'伊甸','Storm':'伊甸','Dawn':'晨岛','CandleSpace':'小黑屋','MainStreet':'小黑屋'}
    for row in rows:
        area = next((v for k,v in prefixes.items() if row['name'].startswith('l_'+k)),
                    '先祖永久翼' if not row['name'].startswith('l_') else '未知')
        groups.setdefault(area,[]).append(row)
    lines = [f'总光翼：{len(rows)} / 已收集：{sum(bool(v.get("collected")) for v in rows)}',
             f'未收集：{sum(not v.get("collected") for v in rows)} / 已存入：{sum(bool(v.get("deposited")) for v in rows)}']
    for area,items in groups.items():
        lines.append('\n【'+area+'】')
        for item in items:
            stamp = item.get('last_conversion')
            when = datetime.fromtimestamp(stamp,TZ).strftime('%Y-%m-%d %H:%M') if stamp else '从未收集'
            lines.append(f'{name(item["name"])} | {"已收集" if item.get("collected") else "未收集"} | {"已存入" if item.get("deposited") else "未存入"} | {when}')
    if missing:
        lines.append('\n部分新光翼尚未收录中文名，括号中保留原始编号。')
    return '\n'.join(lines)


def flatten(records):
    return [dict(record,year=year['year'],month=month['month']) for year in records
            for month in year.get('yearRecord',[]) for record in month.get('monthRecord',[])]


async def reissues(http, year=None):
    records = flatten(await http.resource('RegressionRecords'))
    seasons = await http.resource('SeasonalSpirits')
    season_map = {str(s.get('name') if isinstance(s,dict) else s):item['name']
                  for item in seasons for s in item.get('spirits',[])}
    if year is not None:
        records = [r for r in records if int(r['year']) == year]
    if not records:
        return '暂无该年份的复刻记录。'
    lines = ['复刻记录（不含集体复刻，以游戏内为准）']
    for y in sorted({int(r['year']) for r in records}, reverse=True):
        rows = sorted([r for r in records if int(r['year']) == y], key=lambda r:(int(r['month']),int(r['day'])))
        lines.append(f'\n【{y}年】共 {len(rows)} 次')
        lines.append('平台：'+pretty(dict(Counter(r.get('platform','未知') for r in rows))))
        lines.append('季节分布：'+pretty(dict(Counter(season_map.get(r['name'],'未知') for r in rows))))
        for platform,key in [('iOS','i'),('安卓','a')]:
            counts = Counter(str((r.get('count') or {}).get(key,0)) for r in rows if (r.get('count') or {}).get(key,0))
            lines.append(platform+'复刻次数分布：'+pretty(dict(counts)))
        for r in rows:
            price = r.get('price') or {}
            counts = r.get('count') or {}
            lines.append(f'{r["month"]}月{r["day"]}日 {r["name"]}（{r.get("platform", "未知")}）\n'
                         f'季节：{season_map.get(r["name"],"未知")} / iOS {counts.get("i",0)}次 / 安卓 {counts.get("a",0)}次\n'
                         f'兑换：{pretty(price)}')
    return '\n'.join(lines)


async def season_absence(http, requested):
    seasons = await http.resource('SeasonalSpirits')
    records = flatten(await http.resource('RegressionRecords'))
    season = next((s for s in seasons if str(s.get('name','')).removesuffix('季') == requested.removesuffix('季')),None)
    if season is None:
        raise SkyError('没有找到这个季节，可发送「季节列表」查看。')
    lines = [str(season['name'])]
    for spirit in season.get('spirits',[]):
        name = spirit['name'] if isinstance(spirit,dict) else spirit
        rows = [r for r in records if r.get('name') == name]
        if rows:
            end = max(datetime(int(r['year']),int(r['month']),int(r['day']),tzinfo=TZ) for r in rows)+timedelta(days=5)
        else:
            ends = [as_date(t['E']) for t in season.get('time',[]) if t.get('E')]
            end = max(ends) if ends else None
        days = max(0,math.ceil((now()-end).total_seconds()/86400)) if end else None
        lines.append(f'{name}：复刻 {len(rows)} 次；'+(f'距上次结束 {days} 天' if days is not None else '结束时间未知'))
    return '\n'.join(lines)


async def reissue_calendar(http, year):
    if not 2000 <= year <= 2100:
        raise SkyError('年份应在 2000～2100 之间。')
    rows = [r for r in flatten(await http.resource('RegressionRecords')) if int(r['year']) == year]
    if not rows:
        return '暂无该年份的复刻日历。'
    lines = [f'{year}年复刻日历 · *为复刻开始日']
    for month in range(1,13):
        selected = [r for r in rows if int(r['month']) == month]
        marked = {int(r['day']) for r in selected}
        lines += [f'\n【{month}月】', '一    二    三    四    五    六    日']
        for week in calendar.monthcalendar(year,month):
            lines.append('  '.join('    ' if day == 0 else f'{day:2d}{"*" if day in marked else " "}' for day in week))
        lines += [f'{r["day"]}日：{r["name"]}（{r.get("platform","未知")}）' for r in selected]
    return '\n'.join(lines)
