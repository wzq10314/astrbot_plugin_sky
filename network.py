import ipaddress
import json
import socket
from urllib.parse import urlsplit, urljoin
import aiohttp
from .core import SkyError

RESOURCE = 'https://raw.gitcode.com/Kevin1217/resources/raw/master/resources/'
DATA = RESOURCE + 'json/SkyChildrenoftheLight/'
IMAGES = RESOURCE + 'img/光遇/'
WINGS = 'https://s.166.net/config/ds_yy_02/ma75_wing_wings.json'
DETAIL = 'http://sh-aliyun2.vincentzyu233.cn:51024/queryGuangyi'
KEVCORE = 'https://api.kevcore.cn/v1/gateway/'
OVOAV = 'https://ovoav.com/api/sky/sgwz/sgv1'
T1QQ = 'https://api.t1qq.com/api/sky/'


def public(address):
    ip = ipaddress.ip_address(address.split('%')[0])
    return ip.is_global and not (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global)


def validate(url):
    try:
        p = urlsplit(url)
        if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
            raise ValueError
        if '\\' in url or any(ord(c) < 32 for c in url):
            raise ValueError
        if p.port not in (None, 80, 443) and not (url.split('?')[0] == DETAIL):
            raise ValueError
        try:
            ipaddress.ip_address(p.hostname)
        except ValueError:
            if p.hostname == 'localhost' or p.hostname.endswith(('.local','.localhost')):
                raise ValueError
        else:
            if not public(p.hostname):
                raise ValueError
    except ValueError:
        raise SkyError('图片或接口地址不是允许的公网地址。') from None


class Resolver(aiohttp.abc.AbstractResolver):
    def __init__(self):
        self.delegate = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        rows = await self.delegate.resolve(host, port, family)
        if not rows or any(not public(row['host']) for row in rows):
            raise OSError('Non-public address rejected')
        return rows

    async def close(self):
        await self.delegate.close()


class HTTP:
    def __init__(self, config):
        self.config = config

    async def __aenter__(self):
        self.resolver = Resolver()
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=self.resolver, limit=4, use_dns_cache=False),
            timeout=aiohttp.ClientTimeout(total=self.config['request_timeout']),
            cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
            headers={'User-Agent': 'Mozilla/5.0 TlonSkyAstrBot/0.1'})
        return self

    async def __aexit__(self, *args):
        await self.session.close()
        await self.resolver.close()

    async def raw(self, url, *, params=None, headers=None, body=None, image=False, mixed_image=False):
        # Credential-bearing requests never follow redirects or get retried.
        authenticated = bool(params or headers or body)
        for _ in range(5):
            validate(url)
            async with self.session.request('POST' if body is not None else 'GET', url,
                    params=params, headers=headers, json=body, allow_redirects=False) as response:
                if response.status in (301,302,303,307,308):
                    if authenticated:
                        raise SkyError('接口发生跳转，已停止发送密钥，请核对接口配置。')
                    location = response.headers.get('Location')
                    if not location:
                        raise SkyError('图片地址跳转异常。')
                    url = urljoin(url, location)
                    continue
                if response.status >= 400:
                    error = SkyError(f'上游接口 HTTP {response.status}，请检查接口权限或稍后重试。')
                    error.status = response.status
                    raise error
                maximum = 12 * 1024 * 1024 if image or mixed_image else 4 * 1024 * 1024
                data = bytearray()
                async for part in response.content.iter_chunked(65536):
                    data.extend(part)
                    if len(data) > maximum:
                        raise SkyError('接口响应超过大小限制。')
                if image and not (data.startswith((b'\xff\xd8\xff', b'\x89PNG', b'GIF8')) or
                                  (data.startswith(b'RIFF') and data[8:12] == b'WEBP')):
                    raise SkyError('图片源未返回图片，请检查 API Key、额度或图片是否存在。')
                return bytes(data)
        raise SkyError('接口跳转次数过多。')

    async def json(self, url, **kwargs):
        body = await self.raw(url, **kwargs)
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise SkyError('接口未返回有效 JSON，请检查数据源。') from None

    async def kevcore(self, endpoint, body=None):
        key = self.config['kevcore_api_key']
        if not key:
            raise SkyError('请在插件后台填写 kevcore_api_key（光翼、本月日历、KevCore 身高查询共用）。')
        try:
            data = await self.json(KEVCORE + endpoint, headers={'X-API-Key': key}, body=body)
        except SkyError as error:
            if endpoint != 'sky-calendar-cn' or getattr(error,'status',0) != 405:
                raise
            data = await self.json(KEVCORE + endpoint, headers={'X-API-Key':key}, body={})
        if not isinstance(data, dict) or str(data.get('code')) != '0':
            # Do not echo upstream error strings that may contain keys/IDs.
            code = data.get('code') if isinstance(data, dict) else None
            error = SkyError('KevCore 查询失败，请检查账号在线状态、好友关系、接口权限和余额。')
            error.code = code
            raise error
        return data.get('data')

    async def resource(self, filename):
        return await self.json(DATA + filename + '.json')

    async def task_images(self):
        key = self.config['t1qq_api_key']
        if not key:
            raise SkyError('任务、季蜡、大蜡和魔法图片需要在后台填写自己的 t1qq_api_key。')
        return [await self.raw(T1QQ + endpoint, params={'key': key}, image=True)
                for endpoint in ('sc/scrw','sc/scjl','sc/scdl','mf/magic')]
