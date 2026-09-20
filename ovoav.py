"""Ovoav docs 169 and 184. Never retry a potentially billed query."""
import json
import re
from urllib.parse import urlsplit, parse_qsl
from .core import SkyError, now, pretty
from datetime import timedelta

GIFT_URL = 'https://ovoav.com/api/sky/lbcx/gflb'
WING_URL = 'https://ovoav.com/api/sky/gycx/gyt'


def key_for(http):
    key = http.config.get('ovoav_api_key', '')
    if not key:
        raise SkyError('请在插件后台填写 ovoav_api_key，并开通所查询接口的权限。')
    return key


def decode(raw, label):
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise SkyError(f'独角兽{label}接口未返回有效 JSON；请检查接口权限、额度或服务状态。本次不会自动重试。') from None
    if not isinstance(result, dict):
        raise SkyError(f'独角兽{label}响应结构异常。')
    if 'code' in result and str(result['code']) not in ('0', '200'):
        raise SkyError(f'独角兽{label}查询失败，请检查密钥、接口权限、额度和绑定信息。')
    return result


async def gift_report(http, code):
    # doc169: id is a friend code; omitting type requests the documented JSON.
    result = decode(await http.raw(GIFT_URL, params={'key': key_for(http), 'id': code}), '礼包')
    if not isinstance(result.get('purchasedList'), list) or not all(
            isinstance(item, dict) and 'name' in item for item in result['purchasedList']):
        raise SkyError('独角兽礼包响应缺少 purchasedList，不能将异常响应当作零礼包。')
    lines = [f'礼包总数：{result.get("totalCount", len(result["purchasedList"]))}',
             f'总价值：{result.get("totalPrice", "未知")}']
    lines += [f'{i+1}. {item["name"]} — {item.get("price", "未知")}'
              for i, item in enumerate(result['purchasedList'])]
    if not result['purchasedList']:
        lines.append('接口返回的已购礼包列表为空。')
    unknown = result.get('unknownProductIds')
    if isinstance(unknown, list) and unknown:
        lines.append(f'另有 {len(unknown)} 个商品尚未被接口识别。')
    return '\n'.join(lines)


def is_image(raw):
    return raw.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF8')) or (
        raw.startswith(b'RIFF') and raw[8:12] == b'WEBP')


async def wing_image(http, identifier):
    if not re.fullmatch(r'\d{1,30}', identifier):
        raise SkyError('光翼查询需要游戏内数字短 ID。')
    key = key_for(http)
    raw = await http.raw(WING_URL, params={'key': key, 'id': identifier}, mixed_image=True)
    if is_image(raw):
        return raw
    result = decode(raw, '光翼')
    # doc184 has no response example. Accept common explicit image-URL fields
    # as compatibility paths; unknown schemas fail visibly rather than guessing.
    data = result.get('data', result)
    candidates = [data] if isinstance(data, str) else (
        [data.get(k) for k in ('image', 'image_url', 'url', 'img')] if isinstance(data, dict) else [])
    for url in candidates:
        if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
            continue
        parsed = urlsplit(url)
        if key in url or any(k.lower() in ('key', 'api_key') for k, _ in parse_qsl(parsed.query)):
            raise SkyError('光翼图片地址含接口密钥，已停止后续请求。')
        return await http.raw(url, image=True)
    fields = ', '.join(re.sub(r'[^a-zA-Z0-9_]', '', str(k))[:32] for k in result)[:160]
    raise SkyError('光翼接口未返回可识别图片；文档184未提供返回示例。请提供此提示以便适配，勿发送密钥。响应字段：' + fields)


async def public_info(http, command):
    calendar_match = re.fullmatch(r'(\d{4})年(\d{1,2})月碎石', command)
    if calendar_match:
        year, month = map(int, calendar_match.groups())
        if not 2000 <= year <= 2100 or not 1 <= month <= 12:
            raise SkyError('年份应为2000～2100，月份应为1～12。')
        endpoint, title = 'hstp/hsc/hsrl', f'{year}年{month}月红石日历'
    elif command == '季节列表':
        endpoint, title = 'qdf/jl', '季节历史记录'
    elif command == '活动货币位置':
        endpoint, title = 'hbtp/hb', '活动货币位置'
    elif command in ('明日任务', '明日任务查询'):
        endpoint, title = 'mrrw/rw', '明日任务'
    else:
        endpoint, title = 'jjsj/sj', '当前季节结束时间'
    key = key_for(http)
    params = {'key':key}
    if calendar_match:
        params['time'] = f'{year}年{month}月碎石'
    raw = await http.raw('https://ovoav.com/api/sky/'+endpoint,
                         params=params, mixed_image=True)
    if endpoint == 'mrrw/rw':
        result = decode(raw, title)
        data = result.get('data')
        rows = data.get('task_list') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise SkyError('明日任务接口缺少 task_list，未能确认任务。')
        date = (now()+timedelta(days=1)).date().isoformat()
        tasks = [r['quest_cn'] for r in rows if isinstance(r, dict) and r.get('date') == date
                 and isinstance(r.get('quest_cn'), str) and r['quest_cn'].strip()]
        text = '\n'.join(f'{i+1}. {task}' for i,task in enumerate(tasks)) if tasks else '接口尚未提供该日期的任务，请稍后查询。'
        return title, f'北京时间 {date}\n{text}'.replace(key, '[密钥已隐藏]')
    if is_image(raw):
        return title, raw
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        text = raw.decode('utf-8', errors='replace').strip()
        if not text or '<html' in text.lower() or '<!doctype' in text.lower() or '<script' in text.lower():
            raise SkyError(title+'接口未返回有效内容，请检查权限或服务状态。') from None
        # Doc180 publishes a plain text example despite its JSON format label.
        return title, text.replace(key, '[密钥已隐藏]')
    if isinstance(data, dict):
        if ('code' in data and str(data['code']) not in ('0', '200')) or data.get('success') is False:
            raise SkyError(title+'接口查询失败，请检查密钥、权限、额度或服务状态。')
        data = data.get('data', data)
    candidates = [data] if isinstance(data, str) else (
        [data.get(k) for k in ('image', 'image_url', 'url', 'img')] if isinstance(data, dict) else [])
    for url in candidates:
        if isinstance(url, str) and url.startswith(('https://', 'http://')):
            if key in url or any(k.lower() in ('key','api_key','token') for k,_ in parse_qsl(urlsplit(url).query)):
                raise SkyError('接口图片地址含凭据，已停止后续请求。')
            return title, await http.raw(url, image=True)
    if not isinstance(data, (str, dict, list)) or not data:
        raise SkyError(title+'接口返回空内容或未知格式。')
    return title, pretty(data).replace(key, '[密钥已隐藏]')
