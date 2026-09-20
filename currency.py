"""Self-service currency history through Ovoav doc174."""
import hashlib
import json
from urllib.parse import urlsplit, parse_qs
from .core import SkyError
from .ovoav import key_for, decode

URL = 'https://ovoav.com/api/sky/lzcx/query'
QUERIES = ('蜡烛变化查询', '季节蜡烛查询', '爱心变化查询', '升华蜡烛查询',
           '点赞爱心查询', '魔法变化查询', '代币变化查询', '我的光遇id')
HELP = '''Token 获取与绑定
1. 登录自己的光遇账号，进入小精灵并等待页面加载。
2. 第三方教程的方法：断网后点小精灵右上角刷新，长按空白处全选并复制链接；复制后恢复网络。
3. 检查复制内容是否是小精灵链接，带有 token= 参数。不同版本界面可能不支持此方法；没有出现就不要填写猜测的值。
4. 私聊机器人发送「绑定token 完整小精灵链接或token」，不需要管理员代填。
5. 私聊或群聊发送「蜡烛变化查询」，只查询发送者自己的账号。首次查询会把 token 提交给独角兽并按QQ号绑定，后续成功查询不重复提交。
支持：蜡烛变化查询、季节蜡烛查询、爱心变化查询、升华蜡烛查询、点赞爱心查询、魔法变化查询、代币变化查询、我的光遇id。
token 仅通过私聊绑定命令提交，不能在群里绑定，也不要交给 LLM。此版不生成专属绑定网页，不会自动从游戏中提取 token。
独角兽文档：https://www.ovoav.com/doc/174'''


def token_for(config, sender):
    try:
        values = json.loads(config.get('candle_tokens', '{}'))
    except (ValueError, TypeError):
        raise SkyError('后台 candle_tokens 不是有效 JSON，请按绑定帮助填写。') from None
    if not isinstance(values, dict):
        raise SkyError('后台 candle_tokens 应为 QQ号与token 的 JSON 对象。')
    value = values.get(str(sender))
    if not isinstance(value, str) or not value.strip():
        raise SkyError('当前QQ尚未绑定token。请私聊机器人发送「绑定token 完整小精灵链接或token」，收到“已保存”后再查询。不需要进入后台，也不要让LLM代传token。')
    return normalize_token(value)


def normalize_token(value):
    value = value.strip()
    if len(value) > 16384:
        raise SkyError('Token 长度异常，请重新复制。')
    if value.startswith(('https://', 'http://')):
        parts = urlsplit(value)
        if parts.hostname != 'sprite.16163.com':
            raise SkyError('请填写小精灵链接，不是第三方机器人专属绑定链接。')
        values = parse_qs(parts.query).get('token', [])
        if len(values) != 1:
            raise SkyError('小精灵链接缺少唯一的 token 参数。')
        value = values[0]
    if not value or any(ch.isspace() for ch in value):
        raise SkyError('Token 格式异常，请复制完整 token 或小精灵链接。')
    return value


async def query(http, store, owner, sender, command):
    if command not in QUERIES:
        raise SkyError('不支持的蜡烛查询类型。')
    key = key_for(http)
    local = store.get('currency_tokens', owner, None)
    if local is not None:
        if not local.get('token'):
            raise SkyError('你已解除本地token绑定，请私聊发送「绑定token 完整链接或token」。')
        token = normalize_token(local['token'])
    else:
        token = token_for(http.config, sender)
    fingerprint = hashlib.sha256((key+'\0'+token).encode()).hexdigest()
    previous = store.get('currency_bindings', owner, {})
    params = {'key':key, 'id':str(sender), 'msg':command}
    if previous.get('fingerprint') != fingerprint:
        params['token'] = token
    result = decode(await http.raw(URL, params=params), '蜡烛')
    data = result.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('answer'), str) or not data['answer'].strip():
        raise SkyError('蜡烛接口未返回有效 answer，绑定结果未确认；不会自动重试。')
    answer = data['answer'].replace(token, '[token已隐藏]').replace(key, '[密钥已隐藏]')
    store.put('currency_bindings', owner, {'fingerprint':fingerprint})
    return answer


def bind(store, owner, value):
    token = normalize_token(value)
    store.put('currency_tokens', owner, {'token':token})
    store.put('currency_bindings', owner, {})
    return 'Token已保存到你自己的绑定，尚未验证有效性。现在可在私聊或群里发送「蜡烛变化查询」；首次查询将提交给独角兽完成远端绑定。'


def unbind(store, owner):
    # Tombstone prevents an old administrator-supplied token from taking over.
    store.put('currency_tokens', owner, {'token':''})
    store.put('currency_bindings', owner, {})
    return '已解除本地token绑定。此操作不会撤销独角兽已保存的远端授权。'
