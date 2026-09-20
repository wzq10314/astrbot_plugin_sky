import json
import math
import random
import re
from .core import SkyError, friend_code, player_id, now
from .network import OVOAV, T1QQ


def manage_ids(store, owner, space, action, value=''):
    data = store.get(space, owner, {'ids': [], 'current': ''})
    ids = data['ids']
    if action == '绑定':
        if space == 'wings':
            if not re.fullmatch(r'\d{1,30}', value):
                raise SkyError('光翼绑定需要游戏内数字短 ID。')
        elif space == 'gifts' and not re.fullmatch(r'[0-9a-fA-F-]{36}', value):
            value = friend_code(value)
        else:
            value = player_id(value)
        if value in ids:
            return '该 ID 已绑定。'
        if len(ids) >= 30:
            raise SkyError('最多绑定 30 个账号。')
        ids.append(value)
        data['current'] = data['current'] or value
    elif action in ('切换','删除'):
        if not value.isdigit() or not 1 <= int(value) <= len(ids):
            raise SkyError('请输入 ID 列表中的有效序号。')
        selected = ids[int(value)-1]
        if action == '切换':
            data['current'] = selected
        else:
            ids.remove(selected)
            if selected == data['current']:
                data['current'] = ids[0] if ids else ''
    store.put(space, owner, data)
    return '\n'.join(f'{i+1}. {v}{"（当前）" if v == data["current"] else ""}' for i,v in enumerate(ids)) or '暂无绑定 ID。'


def blind_box(store, owner, command, private):
    data = store.get('boxes','global',{'items': [], 'days': {}})
    if command.startswith('存入盲盒'):
        if not private:
            raise SkyError('请私聊机器人存入盲盒。')
        match = re.fullmatch(r'存入盲盒\s*(.*?)\*(国|国际|测试)服', command)
        if not match:
            raise SkyError('格式：存入盲盒ABCD-EFGH-IJKL*国服')
        code = friend_code(match[1])
        if any(item['code'] == code for item in data['items']):
            raise SkyError('该好友码已经在盲盒库中。')
        data['items'].append({'owner':owner,'code':code,'server':match[2]})
        reply = '好友盲盒已存入，其他用户可随机领取。'
    else:
        today = now().date().isoformat()
        if data['days'].get(owner) == today:
            raise SkyError('今天已领取过好友盲盒，请明天再来。')
        choices = [item for item in data['items'] if item['owner'] != owner]
        if not choices:
            raise SkyError('暂无可领取的他人好友盲盒。')
        selected = random.choice(choices)
        data['items'].remove(selected)
        data['days'] = {k:v for k,v in data['days'].items() if v == today}
        data['days'][owner] = today
        reply = f'好友代码：{selected["code"]}\n服务器：{selected["server"]}服'
    store.put('boxes','global',data)
    return reply


def parse_height(raw):
    """The ovoav document gives a text example despite advertising JSON."""
    text = raw.decode('utf-8-sig') if isinstance(raw, bytes) else str(raw)
    try:
        payload = json.loads(text)
    except ValueError:
        payload = text
    result = {}
    outfit = {}
    outfit_labels = ('发型','面具','发饰','斗篷','背饰','颈饰','裤子','鞋子')
    def keep_text(label, value):
        if isinstance(value,str) and value.strip():
            clean = ' '.join(value.split())[:120]
            if label == '当前身高描述':
                result['description'] = clean
            else:
                outfit[label] = clean
    aliases = {'current_height': ('current_height','当前身高'),
               'max_height': ('max_height','最高身高','最高可达'),
               'min_height': ('min_height','最矮身高','最低可达'),
               'scale': ('scale','体型值'), 'height': ('height','身高值')}
    def walk(value):
        if isinstance(value, dict):
            for label in (*outfit_labels, '当前身高描述'):
                if label in value:
                    keep_text(label,value[label])
            for target,names in aliases.items():
                for name in names:
                    if name in value:
                        try:
                            number = float(value[name])
                            if math.isfinite(number):
                                result[target] = number
                        except (ValueError, TypeError):
                            pass
            for field in ('player_id', 'target_id'):
                if field in value:
                    try:
                        result['player_id'] = player_id(str(value[field]))
                    except SkyError:
                        pass
            for nested in value.values():
                if isinstance(nested,(dict,list,str)):
                    walk(nested)
        elif isinstance(value,list):
            for nested in value: walk(nested)
        elif isinstance(value,str):
            # Fields documented by ovoav doc/144; do not forward arbitrary response data.
            for label in (*outfit_labels, '当前身高描述'):
                match = re.search(r'(?m)^\s*'+re.escape(label)+r'[ \t]*[:：][ \t]*([^\r\n]+)',value)
                if match:
                    keep_text(label,match[1])
            for target,names in aliases.items():
                for name in names:
                    match = re.search(re.escape(name) + r'\s*[:：]\s*(-?\d+(?:\.\d+)?)', value)
                    if match:
                        result[target] = float(match[1])
    if isinstance(payload, dict) and 'code' in payload and str(payload['code']) not in ('0','200'):
        raise SkyError('身高接口未成功，请检查密钥、额度，以及好友码/长 ID 是否有效、账号是否在线。')
    walk(payload)
    if 'current_height' not in result:
        raise SkyError('身高接口未提供当前身高，请检查账号在线和好友状态；也可能是接口返回格式变化。')
    result['outfit'] = {label:outfit[label] for label in outfit_labels if label in outfit}
    return result


async def height(http, store, owner, command, nickname=''):
    data = store.get('height',owner,{'friend_code':'','target_id':'','records':[],'nickname':''})
    if command.startswith('光遇绑定好友码'):
        data['friend_code'] = friend_code(command.removeprefix('光遇绑定好友码').strip())
        data['target_id'] = ''
        data['records'] = []
        store.put('height',owner,data)
        return '好友码已绑定。请保持游戏在线、同意好友申请，再发送「光遇身高查询」。'
    if command.startswith('光遇绑定长ID') or command.startswith('光遇绑定长id'):
        data['target_id'] = player_id(command[7:].strip())
        data['friend_code'] = ''
        data['records'] = []
        store.put('height',owner,data)
        return '长 ID 已绑定，发送「光遇身高查询」。首次查询可能仍需先绑定好友码。'
    if command in ('历史身高','光遇历史身高'):
        return '\n'.join(f'{r["time"]}  {r["height"]:.5f}' for r in data['records'][-10:][::-1]) or '暂无身高记录。'
    if command in ('身高排行榜','光遇身高排行榜'):
        entries = [v for _,v in store.all('height') if v['records']]
        entries.sort(key=lambda v:v['records'][-1]['height'],reverse=True)
        lines = ['本地身高数值排行（数值降序，与原版一致）']
        for index,item in enumerate(entries[:12]):
            name = item.get('nickname') or '玩家'
            lines.append(f'{index+1}. {name[:1]}**  {item["records"][-1]["height"]:.5f}')
        return '\n'.join(lines) if entries else '暂无本地身高记录。'
    argument = command.removeprefix('光遇身高查询').strip()
    target = (player_id(argument) if len(argument) == 36 else friend_code(argument)) if argument else (data['target_id'] or data['friend_code'])
    if not target:
        raise SkyError('先发送「光遇绑定好友码 ABCD-EFGH-IJKL」，或「光遇绑定长ID 你的长ID」。')
    provider = http.config['height_provider']
    key = http.config['ovoav_api_key' if provider == 'ovoav' else 'kevcore_api_key']
    if not key:
        raise SkyError(f'请先在插件后台填写 {provider}_api_key。')
    usage = store.get('height_usage',owner,{'day':'','count':0})
    today = now().date().isoformat()
    if usage['day'] != today:
        usage = {'day':today,'count':0}
    if usage['count'] >= http.config['height_daily_limit']:
        raise SkyError('已达到你今日的身高查询次数上限。')
    # Reserve before request; uncertain/failed billable requests are never retried automatically.
    usage['count'] += 1
    store.put('height_usage',owner,usage)
    if provider == 'ovoav':
        result = parse_height(await http.raw(OVOAV,params={'key':key,'id':target}))
    else:
        result = parse_height(json.dumps(await http.kevcore('sky-height-cn',{'target_id':target})))
    if argument:
        if target not in (data['target_id'],data['friend_code']):
            data['records'] = []
            data['friend_code'] = ''
            data['target_id'] = ''
        data['target_id' if len(target) == 36 else 'friend_code'] = target
    if result.get('player_id'):
        data['target_id'] = result['player_id']
    data['nickname'] = nickname
    data['records'].append({'time':now().isoformat(timespec='seconds'),'height':result['current_height']})
    data['records'] = data['records'][-100:]
    store.put('height',owner,data)
    names = {'current_height':'当前身高','max_height':'最高可达','min_height':'最低可达','scale':'体型值','height':'身高值'}
    lines = [f'{label}：{result[k]:.5f}' for k,label in names.items() if k in result]
    if result.get('description'):
        lines.append('当前身高描述：'+result['description'])
    if result.get('outfit'):
        lines += ['\n当前角色装扮：'] + [f'{label}：{value}' for label,value in result['outfit'].items()]
    else:
        lines.append('本次接口响应未包含可识别的装扮信息。')
    lines.append('已保存历史身高。')
    return '\n'.join(lines)


async def gifts(http, store, owner):
    data = store.get('gifts',owner,{'current':''})
    if http.config.get('gifts_provider', 't1qq') == 'ovoav':
        from .ovoav import gift_report
        try:
            code = friend_code(data['current'])
        except SkyError:
            raise SkyError('独角兽礼包接口需要好友码，旧长 ID 不能使用。请发送「国服id绑定 XXXX-XXXX-XXXX」，再用「国服id切换 序号」选中新好友码。') from None
        return await gift_report(http, code)
    if not data['current']:
        raise SkyError('请先发送「国服id绑定 游戏长ID」。')
    key = http.config['t1qq_api_key']
    if not key:
        raise SkyError('请在后台填写有礼包查询权限的 t1qq_api_key。')
    result = await http.json(T1QQ+'sc/mfskygift',params={'key':key,'id':data['current']})
    if str(result.get('code')) != '200':
        raise SkyError('礼包查询失败，请检查密钥、权限和游戏长 ID。')
    lines = [f'礼包总数：{result.get("count",0)}', f'总价值：{result.get("price","未知")}']
    lines += [f'{i+1}. {v.get("name", "未知")} {"[联动]" if v.get("is_collab") else ""} — {v.get("price", "未知")}'
              for i,v in enumerate(result.get('data') or [])]
    lines.append('查询时间：' + str(result.get('time','')))
    return '\n'.join(lines)
