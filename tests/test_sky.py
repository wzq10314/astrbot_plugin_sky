"""Real sqlite/Pillow/aiohttp imports; only AstrBot and upstream responses are doubled."""
import asyncio
import io
import json
import logging
import re
import sys
import tempfile
import types
import unittest
from datetime import timedelta, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from astrbot_plugin_sky.core import Store, SkyError, TZ, due_jobs, friend_code
from astrbot_plugin_sky.configuration import DEFAULTS, load
from astrbot_plugin_sky.accounts import manage_ids, blind_box, parse_height, height, gifts
from astrbot_plugin_sky.network import HTTP, validate, OVOAV, Resolver
from astrbot_plugin_sky import information
from astrbot_plugin_sky.rendering import cards

api = types.ModuleType('astrbot.api')
api.AstrBotConfig = dict
api.logger = logging.getLogger('sky-test')
events = types.ModuleType('astrbot.api.event')
events.AstrMessageEvent = object
events.filter = types.SimpleNamespace(regex=lambda pattern: lambda method: method)
events.filter.llm_tool = lambda name: lambda method: method
events.MessageChain = lambda chain: chain
stars = types.ModuleType('astrbot.api.star')
class Star:
    def __init__(self, context): self.context = context
stars.Star = Star
stars.Context = object
stars.StarTools = types.SimpleNamespace(get_data_dir=lambda name: '')
stars.register = lambda *args: lambda cls: cls
components = types.ModuleType('astrbot.api.message_components')
components.Plain = lambda text: text
components.At = lambda qq: {'at':qq}
components.Image = types.SimpleNamespace(fromBase64=lambda b64:{'base64':b64})
for name,value in {'astrbot':types.ModuleType('astrbot'),'astrbot.api':api,'astrbot.api.event':events,
                   'astrbot.api.star':stars,'astrbot.api.message_components':components}.items():
    sys.modules[name] = value
from astrbot_plugin_sky.main import TlonSky, PATTERN

class Event:
    def __init__(self,text='光遇菜单',admin=False,group='',role='member'):
        self.text,self.admin,self.group = text,admin,group
        self.message_obj = types.SimpleNamespace(self_id='999',raw_message={'sender':{'role':role}})
        self.unified_msg_origin = 'bot:GroupMessage:'+group
        self.sent = []
        self.stopped = False
    def get_sender_id(self): return '123'
    def get_sender_name(self): return '测试'
    def get_platform_id(self): return 'bot'
    def get_platform_name(self): return 'aiocqhttp'
    def get_message_str(self): return self.text
    def get_group_id(self): return self.group
    def is_private_chat(self): return not self.group
    def is_admin(self): return self.admin
    def stop_event(self): self.stopped = True
    def plain_result(self,text): return text
    def chain_result(self,parts): return parts
    async def send(self,value):
        self.sent.append(value)
        # Reproduce the pipeline situation seen in RConsole: stop after sending.
        self.stop_event()


class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'test.sqlite')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_ids_delete_current_and_scope(self):
        manage_ids(self.store,'a','wings','绑定','123')
        manage_ids(self.store,'a','wings','绑定','456')
        manage_ids(self.store,'a','wings','切换','2')
        manage_ids(self.store,'a','wings','删除','2')
        self.assertEqual(self.store.get('wings','a')['current'],'123')
        self.assertIn('暂无',manage_ids(self.store,'b','wings','列表'))
        with self.assertRaises(SkyError): manage_ids(self.store,'a','wings','切换','0')
        with self.assertRaises(SkyError): manage_ids(self.store,'a','gifts','绑定','123')

    def test_store_persists(self):
        self.store.put('a','b',{'中文':3})
        other = Store(Path(self.temp.name)/'test.sqlite')
        self.assertEqual(other.get('a','b'),{'中文':3})
        other.close()

    def test_friend_boxes(self):
        with self.assertRaises(SkyError): blind_box(self.store,'a','存入盲盒ABCD-EFGH-IJKL*国服',False)
        blind_box(self.store,'a','存入盲盒ABCD-EFGH-IJKL*国服',True)
        with self.assertRaises(SkyError): blind_box(self.store,'a','随机好友',True)
        self.assertIn('ABCD-EFGH-IJKL',blind_box(self.store,'b','随机好友',True))
        with self.assertRaises(SkyError): blind_box(self.store,'b','随机好友',True)
        self.assertFalse(self.store.get('boxes','global')['items'])

    def test_height_text_and_json(self):
        result = parse_height('身高解析结果：\n体型值: 0.00024\n身高值: 1.91005\n最高身高: 1.59803\n最矮身高: 13.59803\n当前身高: 1.86789')
        self.assertEqual(result['current_height'],1.86789)
        self.assertEqual(result['max_height'],1.59803)
        self.assertEqual(parse_height('{"code":200,"data":{"current_height":0}}')['current_height'],0)
        for value in ('<html>captcha</html>','{"code":403,"data":{"current_height":1}}','{"current_height":"NaN"}'):
            with self.assertRaises(SkyError): parse_height(value)

    async def test_height_query_binding_history_budget(self):
        http = types.SimpleNamespace(config={**DEFAULTS,'ovoav_api_key':'SECRET','height_daily_limit':1},
                                     raw=AsyncMock(return_value=b'{"current_height":2.3}'))
        await height(http,self.store,'a','光遇绑定好友码 abcdefghijkl')
        reply = await height(http,self.store,'a','光遇身高查询','张三')
        self.assertIn('2.30000',reply)
        self.assertEqual(http.raw.call_args.args[0],OVOAV)
        self.assertEqual(http.raw.call_args.kwargs['params']['id'],'ABCD-EFGH-IJKL')
        self.assertIn('2.30000',await height(http,self.store,'a','光遇历史身高'))
        with self.assertRaises(SkyError): await height(http,self.store,'a','光遇身高查询')
        http.raw.assert_awaited_once()
        await height(http,self.store,'a','光遇绑定好友码 123456789012')
        self.assertFalse(self.store.get('height','a')['records'])

    async def test_height_missing_key_no_request_or_usage(self):
        http = types.SimpleNamespace(config=DEFAULTS,raw=AsyncMock())
        await height(http,self.store,'a','光遇绑定好友码 ABCDEFGHIJKL')
        with self.assertRaises(SkyError): await height(http,self.store,'a','光遇身高查询')
        http.raw.assert_not_awaited()
        self.assertIsNone(self.store.get('height_usage','a'))

    async def test_height_long_id_and_kevcore(self):
        identifier = '4c447577-8032-44fe-877f-123456789012'
        http = types.SimpleNamespace(config={**DEFAULTS,'height_provider':'kevcore','kevcore_api_key':'SECRET'},
            kevcore=AsyncMock(return_value={'current_height':1.8,'target_id':identifier}))
        await height(http,self.store,'a','光遇绑定长ID '+identifier)
        await height(http,self.store,'a','光遇身高查询')
        http.kevcore.assert_awaited_once_with('sky-height-cn',{'target_id':identifier})
        self.assertEqual(self.store.get('height','a')['target_id'],identifier)

    def test_schedules(self):
        self.assertIn('shard',due_jobs(datetime(2026,9,17,0,1,tzinfo=TZ),DEFAULTS))
        self.assertNotIn('shard_before',due_jobs(datetime(2026,9,18,10,57,tzinfo=TZ),DEFAULTS))
        self.assertIn('sacrifice',due_jobs(datetime(2026,9,20,0,0,tzinfo=TZ),DEFAULTS))
        self.assertNotIn('sacrifice',due_jobs(datetime(2026,9,19,0,0,tzinfo=TZ),DEFAULTS))
        with self.assertRaises(ValueError): load({'daily_times':'26:01'})

    def test_ssrf(self):
        for url in ('http://127.0.0.1/','http://[::1]/','file:///etc/passwd','https://user:pass@ovoav.com/',
                    'http://10.0.0.1:51024/queryGuangyi','http://localhost/'):
            with self.assertRaises(SkyError): validate(url)
        validate('http://sh-aliyun2.vincentzyu233.cn:51024/queryGuangyi?id=123')

    async def test_dns(self):
        resolver = Resolver()
        resolver.delegate.resolve = AsyncMock(return_value=[{'host':'10.0.0.1'}])
        with self.assertRaises(OSError): await resolver.resolve('example.com')
        await resolver.close()

    def test_render_real_png_paginated(self):
        from PIL import Image
        images = cards('测试光遇','蜡烛变化 +6\n'*100)
        self.assertGreater(len(images),1)
        for image in images:
            with Image.open(io.BytesIO(image)) as im:
                self.assertEqual(im.width,1000)
                self.assertLess(im.height,2000)

    def test_regex_coverage(self):
        for command in ('#光遇菜单','/光遇状态','光遇绑定123','光翼详情 123','国服id绑定 uuid','光遇绑定好友码 ABCD-EFGH-IJKL',
            '光遇身高查询','光遇绑定长ID uuid','今日大蜡烛','全部年复刻记录','2026年复刻日历',
            '2026年9月碎石','光遇绘画分享','光遇强制更新','开启碎石提醒','光遇本月日历'):
            self.assertIsNotNone(re.fullmatch(PATTERN,command),command)
        for text in ('你好','记录','https://example.com','#RBQ'):
            self.assertIsNone(re.fullmatch(PATTERN,text))

    def plugin(self):
        with patch.object(stars.StarTools,'get_data_dir',return_value=Path(self.temp.name)/'plugin'):
            return TlonSky(types.SimpleNamespace(send_message=AsyncMock()),{'report_images':False})

    async def test_ovoav_direct_and_llm_routes(self):
        plugin = self.plugin()
        try:
            plugin.config['ovoav_api_key'] = 'SECRET'
            with patch('astrbot_plugin_sky.main.HTTP') as cls:
                http = types.SimpleNamespace(config=plugin.config,raw=AsyncMock(return_value=b'\x89PNG\r\n\x1a\nfixture'))
                cls.return_value.__aenter__ = AsyncMock(return_value=http)
                cls.return_value.__aexit__ = AsyncMock(return_value=None)
                event=Event('#光翼查询 123')
                await plugin.command(event)
                self.assertIn('base64',event.sent[0][0])
                http.raw.assert_awaited_once()
                plugin.cooldowns.clear()
                plugin.store.put('gifts','bot:123',{'ids':['ABCD-EFGH-IJKL'],'current':'ABCD-EFGH-IJKL'})
                http.raw=AsyncMock(return_value=b'{"totalCount":0,"totalPrice":0,"purchasedList":[]}')
                event=Event('帮我查礼包')
                reply=await plugin.sky_tool(event,'国服礼包查询')
                self.assertIn('礼包总数：0',reply)
                await plugin.sky_tool(event,'国服礼包查询')
                http.raw.assert_awaited_once()
        finally:
            await plugin.terminate()

    async def test_currency_llm_and_private_guard(self):
        plugin=self.plugin()
        try:
            event=Event('绑定token TESTTOKEN',group='456')
            await plugin.command(event)
            self.assertIn('私聊',event.sent[0])
            with patch('astrbot_plugin_sky.main.HTTP') as cls:
                cls.return_value.__aenter__=AsyncMock(return_value=object())
                cls.return_value.__aexit__=AsyncMock(return_value=None)
                with patch('astrbot_plugin_sky.main.currency.query',new=AsyncMock(return_value='增加12')) as query:
                    event=Event('查一下蜡烛')
                    result=await plugin.sky_tool(event,'蜡烛变化查询')
                    self.assertIn('增加12',result)
                    await plugin.sky_tool(event,'蜡烛变化查询')
                    query.assert_awaited_once()
        finally:await plugin.terminate()

    async def test_new_info_direct_and_llm_dispatch(self):
        plugin=self.plugin()
        try:
            with patch('astrbot_plugin_sky.main.HTTP') as cls:
                cls.return_value.__aenter__=AsyncMock(return_value=object())
                cls.return_value.__aexit__=AsyncMock(return_value=None)
                with patch('astrbot_plugin_sky.main.ovoav.public_info',new=AsyncMock(return_value=('查询','独角兽返回内容'))) as query:
                    for command in ('季节列表','光遇进度','活动货币位置','明日任务查询'):
                        plugin.cooldowns.clear()
                        self.assertIsNotNone(re.fullmatch(PATTERN,command))
                        self.assertIn('独角兽返回内容',await plugin.sky_tool(Event(),command))
                    self.assertEqual(query.await_count,4)
        finally:await plugin.terminate()

    async def test_private_token_binding_and_group_query(self):
        plugin=self.plugin()
        try:
            event=Event('绑定token PRIVATE_TOKEN')
            await plugin.command(event)
            self.assertNotIn('PRIVATE_TOKEN',str(event.sent))
            self.assertEqual(plugin.store.get('currency_tokens','bot:123')['token'],'PRIVATE_TOKEN')
            group=Event('绑定token BAD_TOKEN',group='456')
            await plugin.command(group)
            self.assertEqual(plugin.store.get('currency_tokens','bot:123')['token'],'PRIVATE_TOKEN')
            self.assertNotIn('BAD_TOKEN',str(group.sent))
            self.assertIn('未执行',await plugin.sky_tool(Event(),'绑定token LLM_TOKEN'))
            with patch('astrbot_plugin_sky.main.HTTP') as cls:
                http=types.SimpleNamespace(config={**plugin.config,'ovoav_api_key':'SECRET'},raw=AsyncMock(return_value=b'{"code":200,"data":{"answer":"balance"}}'))
                cls.return_value.__aenter__=AsyncMock(return_value=http)
                cls.return_value.__aexit__=AsyncMock(return_value=None)
                group=Event('蜡烛变化查询',group='456')
                await plugin.command(group)
                self.assertIn('balance',str(group.sent))
                self.assertEqual(http.raw.call_args.kwargs['params']['token'],'PRIVATE_TOKEN')
                self.assertEqual(http.raw.call_args.kwargs['params']['id'],'123')
                from astrbot_plugin_sky import currency
                with self.assertRaises(SkyError):await currency.query(http,plugin.store,'bot:999','999','蜡烛变化查询')
                http.raw.assert_awaited_once()
            await plugin.command(Event('解绑token'))
            self.assertFalse(plugin.store.get('currency_tokens','bot:123')['token'])
        finally:await plugin.terminate()

    async def test_natural_token_binding_prefixes(self):
        plugin=self.plugin()
        try:
            for prefix in ('这是我的token：','我的token:','绑定token：','#绑定token '):
                command=prefix+'TEST_ONLY_TOKEN'
                self.assertIsNotNone(re.fullmatch(PATTERN,command))
                event=Event(command)
                await plugin.command(event)
                self.assertIn('已保存',str(event.sent))
                self.assertNotIn('TEST_ONLY_TOKEN',str(event.sent))
                self.assertTrue(event.stopped)
                self.assertEqual(plugin.store.get('currency_tokens','bot:123')['token'],'TEST_ONLY_TOKEN')
            event=Event('这是我的token：GROUP_VALUE',group='456')
            await plugin.command(event)
            self.assertIn('只能私聊',str(event.sent))
            self.assertEqual(plugin.store.get('currency_tokens','bot:123')['token'],'TEST_ONLY_TOKEN')
            self.assertIn('未执行',await plugin.sky_tool(Event(),'这是我的token：LLM_VALUE'))
        finally:await plugin.terminate()

    async def test_activity_aliases_route_and_deduplicate(self):
        plugin=self.plugin()
        try:
            with patch('astrbot_plugin_sky.main.HTTP') as cls:
                cls.return_value.__aenter__=AsyncMock(return_value=object())
                cls.return_value.__aexit__=AsyncMock(return_value=None)
                with patch('astrbot_plugin_sky.main.ovoav.public_info',new=AsyncMock(return_value=('活动货币位置','活动点位'))) as query:
                    event=Event()
                    self.assertIn('活动点位',await plugin.sky_tool(event,'活动货币'))
                    for cmd in ('活动货币位置','活动货币点位图','活动代币位置','光遇活动货币查询'):
                        self.assertIsNotNone(re.fullmatch(PATTERN,cmd))
                        self.assertIn('未重复',await plugin.sky_tool(event,cmd))
                    query.assert_awaited_once()
                    self.assertEqual(query.call_args.args[1],'活动货币位置')
                    plugin.cooldowns.clear()
                    direct=Event('#活动货币')
                    await plugin.command(direct)
                    self.assertIn('活动点位',str(direct.sent))
                    self.assertEqual(query.call_args.args[1],'活动货币位置')
        finally:await plugin.terminate()

    async def test_api_shard_calendar_and_push(self):
        plugin=self.plugin()
        try:
            http=types.SimpleNamespace(config={'ovoav_api_key':'SECRET'},raw=AsyncMock(return_value='接口月历'.encode()))
            with patch('astrbot_plugin_sky.main.HTTP') as cls:
                cls.return_value.__aenter__=AsyncMock(return_value=http)
                cls.return_value.__aexit__=AsyncMock(return_value=None)
                event=Event('2026年7月碎石')
                await plugin.command(event)
                self.assertIn('接口月历',str(event.sent))
                self.assertEqual(http.raw.call_args.args[0],'https://ovoav.com/api/sky/hstp/hsc/hsrl')
                self.assertEqual(http.raw.call_args.kwargs['params'],{'key':'SECRET','time':'2026年7月碎石'})
                await plugin.send_push('bot:GroupMessage:1','shard',datetime(2026,9,17,tzinfo=TZ))
                self.assertEqual(http.raw.call_args.kwargs['params']['time'],'2026年9月碎石')
                self.assertIn('接口月历',str(plugin.context.send_message.call_args))
                http.raw.reset_mock()
                await plugin.command(Event('碎石路线图'))
                http.raw.assert_not_awaited()
        finally:await plugin.terminate()

    async def test_advance_reminder_switch_and_subscription(self):
        date=datetime(2026,9,18,10,58,tzinfo=TZ)
        self.assertIn('shard_before',due_jobs(date,DEFAULTS))
        self.assertNotIn('shard_before',due_jobs(date,{**DEFAULTS,'shard_advance_reminder':False}))
        self.assertNotIn('shard_before',due_jobs(date-timedelta(minutes=1),DEFAULTS))
        plugin=self.plugin()
        try:
            plugin.store.put('subscriptions','bot:GroupMessage:1',{'shard':True})
            plugin.store.put('subscriptions','bot:GroupMessage:2',{'shard':False})
            await plugin.push_tick(date)
            await plugin.push_tick(date)
            plugin.context.send_message.assert_awaited_once()
            self.assertEqual(plugin.context.send_message.call_args.args[0],'bot:GroupMessage:1')
            self.assertIn('内置时刻表',str(plugin.context.send_message.call_args))
        finally:await plugin.terminate()

    async def test_plugin_direct_send_not_interrupted(self):
        plugin = self.plugin()
        event = Event('#光遇菜单')
        await plugin.command(event)
        self.assertIn('光遇菜单',event.sent[0])
        self.assertTrue(event.stopped)
        self.assertFalse(plugin.running)
        await plugin.terminate()

    async def test_subscription_permission_and_persistence(self):
        plugin = self.plugin()
        event = Event('开启每日任务推送',group='456')
        await plugin.command(event)
        self.assertIn('管理员',event.sent[0])
        self.assertFalse(plugin.store.all('subscriptions'))
        event = Event('开启每日任务推送',group='456',role='admin')
        await plugin.command(event)
        await plugin.command(event)
        self.assertEqual(len(plugin.store.all('subscriptions')),1)
        event.text = '关闭每日任务推送'
        await plugin.command(event)
        self.assertFalse(plugin.store.all('subscriptions')[0][1]['daily'])
        await plugin.terminate()

    async def test_push_dedup_reload_and_failure(self):
        plugin = self.plugin()
        plugin.store.put('subscriptions','bot:GroupMessage:1',{'daily':True})
        plugin.store.put('subscriptions','bot:GroupMessage:2',{'daily':True})
        plugin.send_push = AsyncMock(side_effect=[RuntimeError('fail'),None])
        date = datetime(2026,9,19,6,0,tzinfo=TZ)
        await plugin.push_tick(date)
        await plugin.push_tick(date)
        self.assertEqual(plugin.send_push.await_count,2)
        await plugin.terminate()
        plugin = self.plugin()
        plugin.send_push = AsyncMock()
        await plugin.push_tick(date)
        plugin.send_push.assert_not_awaited()
        await plugin.terminate()

    async def test_push_message_chain(self):
        plugin = self.plugin()
        await plugin.send_push('bot:GroupMessage:1','grandma',datetime(2026,9,19,tzinfo=TZ))
        plugin.context.send_message.assert_awaited_once()
        self.assertIn('老奶奶',plugin.context.send_message.call_args.args[1][0])
        await plugin.terminate()

    async def test_real_public_fixtures(self):
        path = Path('work/tlon-fixtures')
        if not path.exists(): self.skipTest('Optional downloaded public fixtures unavailable')
        async def resource(name): return json.loads((path/(name+'.json')).read_text(encoding='utf-8'))
        async def get_json(url,**kwargs):
            name = 'wing_catalog' if 'ma75_wing' in url else 'announcement'
            return await resource(name)
        http = types.SimpleNamespace(resource=resource,json=get_json)
        self.assertIn('永久翼',await information.wing_counts(http))
        self.assertIn('复刻',await information.reissues(http,2026))
        self.assertIn('日历',await information.reissue_calendar(http,2026))
        self.assertIn('PC',await information.announcement(http))
        self.assertIn('感恩',await information.season_absence(http,'感恩季'))

    async def test_wing_query_new_and_old_fields(self):
        http = types.SimpleNamespace(resource=AsyncMock(return_value={'l_Dawn_0':'晨岛山洞'}),
            kevcore=AsyncMock(return_value={'role_total':260,'role_collected':200,'role_uncollected':60,
                'map_groups':[{'name':'晨岛','total':10,'collected':5}],
                'uncollected_by_map':[{'map':'晨岛','wings':[{'wing_id':'l_Dawn_0'}]}]}))
        result = await information.wings(http,'123')
        self.assertIn('260',result)
        self.assertIn('晨岛山洞',result)
        http.kevcore.return_value = {'wing_count':200,'collected_count':180,'uncollected_count':20}
        self.assertIn('总光翼：200',await information.wings(http,'123'))

    async def test_wing_details_and_gifts(self):
        http = types.SimpleNamespace(resource=AsyncMock(return_value={'l_Dawn_0':'晨岛'}),
            json=AsyncMock(side_effect=[{'success':True,'data':{'result':json.dumps({'wing_buffs':[
                {'name':'l_Dawn_0','collected':True,'deposited':False,'last_conversion':0}]})}},
                [{'光翼名字':'l_Dawn_0'}]]))
        result = await information.wings(http,'123',True)
        self.assertIn('总光翼：4',result)
        self.assertIn('已收集：1',result)
        manage_ids(self.store,'a','gifts','绑定','4c447577-8032-44fe-877f-123456789012')
        http.config = {**DEFAULTS,'t1qq_api_key':'SECRET','gifts_provider':'t1qq'}
        http.json = AsyncMock(return_value={'code':200,'count':1,'price':6,'data':[{'name':'测试礼包','price':6}]})
        self.assertIn('测试礼包',await gifts(http,self.store,'a'))

    async def test_calendar_method_fallback(self):
        http = HTTP({**DEFAULTS,'kevcore_api_key':'SECRET'})
        error = SkyError('method')
        error.status = 405
        http.json = AsyncMock(side_effect=[error,{'code':0,'data':{'image_url':'https://example.com/a.png'}}])
        self.assertIn('image_url',await http.kevcore('sky-calendar-cn'))
        self.assertEqual(http.json.call_args.kwargs['body'],{})

    async def test_no_credential_redirect(self):
        class Response:
            status = 302
            headers = {'Location':'https://attacker.example/'}
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
        from unittest.mock import Mock
        http = HTTP(DEFAULTS)
        http.session = types.SimpleNamespace(request=Mock(return_value=Response()))
        with self.assertRaises(SkyError):
            await http.raw(OVOAV,params={'key':'SECRET','id':'test'})
        http.session.request.assert_called_once()

    async def test_cancel_releases_command_and_database(self):
        plugin = self.plugin()
        started = asyncio.Event()
        async def dispatch(*args):
            started.set()
            await asyncio.Event().wait()
        plugin.dispatch = dispatch
        task = asyncio.create_task(plugin.command(Event()))
        await started.wait()
        await plugin.terminate()
        self.assertTrue(task.cancelled())
        self.assertFalse(plugin.work_lock.locked())
        self.assertFalse(plugin.running)

    async def test_stale_candle_tool_alias_dedup(self):
        plugin=self.plugin()
        try:
            plugin.dispatch=AsyncMock()
            event=Event('帮我查询蜡烛数量')
            result=await plugin.sky_tool(event,'蜡烛记录')
            self.assertEqual(plugin.dispatch.call_args.args[2],'蜡烛变化查询')
            self.assertIn('不是手动记账',result)
            for cmd in ('蜡烛变化查询','查询蜡烛数量','蜡烛查询'):
                self.assertIn('未重复',await plugin.sky_tool(event,cmd))
            plugin.dispatch.assert_awaited_once()
            plugin.dispatch=AsyncMock(side_effect=SkyError('当前QQ未配置 token'))
            result=await plugin.sky_tool(Event(),'蜡烛记录')
            self.assertIn('未配置 token',result)
            self.assertIn('不是手动记账',result)
            self.assertFalse(plugin.store.get('candles','bot:123',{}))
        finally:await plugin.terminate()

    async def test_manual_candles_removed(self):
        plugin = self.plugin()
        old = {'current':'默认ID','accounts':{'默认ID':[{'values':[1,2,3,4]}]}}
        plugin.store.put('candles','bot:123',old)
        try:
            for cmd in ('记录蜡烛100:20:30:10','蜡烛记录','蜡烛记录帮助','蜡烛ID列表','添加蜡烛ID 小号','切换蜡烛ID 小号','删除蜡烛ID 小号'):
                self.assertIsNone(re.fullmatch(PATTERN,cmd))
                if cmd != '蜡烛记录':
                    self.assertIn('未执行',await plugin.sky_tool(Event(),cmd))
            self.assertEqual(plugin.store.get('candles','bot:123'),old)
        finally: await plugin.terminate()

    async def test_llm_current_group_permissions(self):
        plugin = self.plugin()
        event = Event('每天给群里发任务',group='456')
        reply = await plugin.sky_tool(event,'开启每日任务推送')
        self.assertIn('管理员',reply)
        self.assertFalse(plugin.store.all('subscriptions'))
        event = Event('每天给群里发任务',group='456',role='admin')
        self.assertIn('执行完成',await plugin.sky_tool(event,'开启每日任务推送'))
        self.assertEqual(plugin.store.all('subscriptions')[0][0],event.unified_msg_origin)
        await plugin.terminate()

    async def test_llm_validation_and_failure_truthfulness(self):
        plugin = self.plugin()
        event = Event()
        self.assertIn('未执行',await plugin.sky_tool(event,'rm -rf /'))
        self.assertIn('未执行',await plugin.sky_tool(event,'光遇菜单\n开启碎石提醒'))
        plugin.dispatch = AsyncMock(side_effect=SkyError('缺少接口密钥'))
        self.assertIn('未完成',await plugin.sky_tool(event,'光遇身高查询'))
        self.assertIn('未重复',await plugin.sky_tool(event,'光遇身高查询'))
        plugin.dispatch.assert_awaited_once()
        await plugin.terminate()

    async def test_llm_image_report_returns_readable_data(self):
        plugin = self.plugin()
        plugin.config['report_images'] = True
        event = Event()
        result = await plugin.sky_tool(event,'光遇菜单')
        self.assertIn('张图片',result)
        self.assertIn('执行完成',result)
        await plugin.terminate()

    def test_llm_docstring_schema(self):
        import inspect
        doc = inspect.getdoc(TlonSky.sky_tool)
        self.assertIn('Args:',doc)
        self.assertRegex(doc,r'command\(string\):')

    def test_height_outfit_documented_formats(self):
        text = ('当前身高: 1.86789\n当前身高描述: 高身高\n当前角色服装：\n'
                '发型: 萌新发型\n面具: 初始面具\n发饰: 彩虹耳坠\n斗篷: 黄色斗篷\n'
                '背饰: 未知装扮\n颈饰: 未穿戴\n裤子: 平角短裤\n鞋子: 跃动跑鞋\n查询耗时: 2.1s')
        for raw in (text, json.dumps({'code':200,'data':text}),
                    json.dumps({'code':200,'data':{'当前身高':1.86789,'当前身高描述':'高身高',
                        '当前角色服装':{'发型':'萌新发型','面具':'初始面具','发饰':'彩虹耳坠','斗篷':'黄色斗篷',
                                      '背饰':'未知装扮','颈饰':'未穿戴','裤子':'平角短裤','鞋子':'跃动跑鞋'}}})):
            with self.subTest(raw=raw):
                result = parse_height(raw)
                self.assertEqual(len(result['outfit']),8)
                self.assertEqual(result['outfit']['鞋子'],'跃动跑鞋')
                self.assertEqual(result['description'],'高身高')
                self.assertNotIn('查询耗时',result)

    async def test_height_outfit_display_and_absence(self):
        http = types.SimpleNamespace(config={**DEFAULTS,'ovoav_api_key':'SECRET'},raw=AsyncMock())
        await height(http,self.store,'a','光遇绑定好友码 ABCDEFGHIJKL')
        http.raw.return_value = '当前身高: 1.86789\n斗篷: 黄色斗篷'.encode()
        self.assertIn('斗篷：黄色斗篷',await height(http,self.store,'a','光遇身高查询'))
        http.raw.return_value = b'{"current_height":2.3}'
        self.assertIn('未包含可识别的装扮信息',await height(http,self.store,'a','光遇身高查询'))

    async def test_wing_names_offline_and_missing_rate(self):
        http = types.SimpleNamespace(resource=AsyncMock(side_effect=SkyError('GitCode unavailable')),
            kevcore=AsyncMock(return_value={'role_total':254,'role_collected':142,'role_uncollected':112,
                'uncollected_by_map':[{'map':'晨岛','wings':[{'wing_id':'l_Dawn_TrialsFire_0'},
                    {'wing_id':'l_Dawn_0'},{'wing_id':'l_Dawn_1'}, {'wing_id':'l_unknown_new_0'}]}]}))
        text = await information.wings(http,'123')
        self.assertIn('55.91%',text)
        self.assertIn('火试炼',text)
        self.assertIn('晨岛 × 2',text)
        self.assertNotIn('l_Dawn_TrialsFire_0',text)
        self.assertIn('未收录中文名（l_unknown_new_0）',text)

    async def test_wing_names_bad_source_and_refresh(self):
        for payload in ([],{'error':'failed'},{'l_Dawn_0':None}):
            http = types.SimpleNamespace(resource=AsyncMock(return_value=payload))
            self.assertEqual((await information.wing_names(http))['l_dawn_trialsfire_0'],'火试炼')
        http = types.SimpleNamespace(resource=AsyncMock(return_value={'L_Dawn_0':'新增中文名称'}))
        self.assertEqual((await information.wing_names(http))['l_dawn_0'],'新增中文名称')

    async def test_wing_names_timeout_uses_snapshot(self):
        http = types.SimpleNamespace(resource=AsyncMock(side_effect=TimeoutError))
        self.assertEqual((await information.wing_names(http))['l_night_paintedworld_1'],'月牙绿洲')

if __name__ == '__main__': unittest.main()


class OvoavTests(unittest.IsolatedAsyncioTestCase):
    async def test_gift_schema_and_params(self):
        from astrbot_plugin_sky.ovoav import gift_report, GIFT_URL
        http = types.SimpleNamespace(config={'ovoav_api_key':'SECRET'}, raw=AsyncMock(return_value=json.dumps({
            'totalCount':1,'totalPrice':6,'purchasedList':[{'name':'测试礼包','price':6}], 'unknownProductIds':['unknown']}).encode()))
        text = await gift_report(http,'ABCD-EFGH-IJKL')
        self.assertIn('测试礼包',text)
        self.assertIn('总价值：6',text)
        self.assertIn('1 个商品',text)
        http.raw.assert_awaited_once_with(GIFT_URL,params={'key':'SECRET','id':'ABCD-EFGH-IJKL'})

    async def test_gift_empty_and_error(self):
        from astrbot_plugin_sky.ovoav import gift_report
        http = types.SimpleNamespace(config={'ovoav_api_key':'SECRET'},raw=AsyncMock(return_value=b'{"purchasedList":[],"totalCount":0,"totalPrice":0}'))
        self.assertIn('列表为空',await gift_report(http,'ABCD-EFGH-IJKL'))
        for raw in (b'<html>SECRET</html>',b'{"code":403,"msg":"SECRET"}',b'{}'):
            http.raw.reset_mock();http.raw.return_value=raw
            with self.assertRaises(SkyError) as error: await gift_report(http,'ABCD-EFGH-IJKL')
            self.assertNotIn('SECRET',str(error.exception))
            self.assertEqual(http.raw.await_count,1)

    async def test_old_gift_id_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'state.sqlite3')
            try:
                manage_ids(store,'a','gifts','绑定','4c447577-8032-44fe-877f-123456789012')
                http=types.SimpleNamespace(config=DEFAULTS,raw=AsyncMock())
                with self.assertRaises(SkyError): await gifts(http,store,'a')
                http.raw.assert_not_awaited()
                manage_ids(store,'a','gifts','绑定','ABCD-EFGH-IJKL')
                self.assertEqual(len(store.get('gifts','a')['ids']),2)
                manage_ids(store,'a','gifts','切换','2')
                self.assertEqual(store.get('gifts','a')['current'],'ABCD-EFGH-IJKL')
            finally: store.close()

    async def test_wing_image_and_url(self):
        from astrbot_plugin_sky.ovoav import wing_image, WING_URL
        png=b'\x89PNG\r\n\x1a\nfixture'
        http=types.SimpleNamespace(config={'ovoav_api_key':'SECRET'},raw=AsyncMock(return_value=png))
        self.assertEqual(await wing_image(http,'123'),png)
        http.raw.assert_awaited_once_with(WING_URL,params={'key':'SECRET','id':'123'},mixed_image=True)
        http.raw=AsyncMock(side_effect=[b'{"code":200,"data":{"url":"https://example.com/report.png"}}',png])
        self.assertEqual(await wing_image(http,'123'),png)
        self.assertEqual(http.raw.await_count,2)
        self.assertEqual(http.raw.call_args.kwargs,{'image':True})

    async def test_wing_unknown_missing_key_no_retry(self):
        from astrbot_plugin_sky.ovoav import wing_image
        http=types.SimpleNamespace(config={'ovoav_api_key':''},raw=AsyncMock())
        with self.assertRaises(SkyError): await wing_image(http,'123')
        http.raw.assert_not_awaited()
        http.config['ovoav_api_key']='SECRET'
        for raw in (b'{"unexpected":"SECRET"}',b'{"data":{"url":"https://example.com/?key=SECRET"}}'):
            http.raw=AsyncMock(return_value=raw)
            with self.assertRaises(SkyError) as error: await wing_image(http,'123')
            self.assertNotIn('SECRET',str(error.exception))
            self.assertEqual(http.raw.await_count,1)


class CurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_and_query_params(self):
        from astrbot_plugin_sky import currency
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'state.sqlite3')
            try:
                http=types.SimpleNamespace(config={'ovoav_api_key':'SECRET','candle_tokens':json.dumps({'123':'https://sprite.16163.com/ma75/?token=MYTOKEN'})},raw=AsyncMock(return_value=json.dumps({'code':200,'data':{'answer':'蜡烛增加12'}}).encode()))
                self.assertEqual(await currency.query(http,store,'bot:123','123','蜡烛变化查询'),'蜡烛增加12')
                self.assertEqual(http.raw.call_args.kwargs['params'],{'key':'SECRET','id':'123','msg':'蜡烛变化查询','token':'MYTOKEN'})
                await currency.query(http,store,'bot:123','123','爱心变化查询')
                self.assertNotIn('token',http.raw.call_args.kwargs['params'])
                self.assertNotIn('MYTOKEN',str(store.get('currency_bindings','bot:123')))
                with self.assertRaises(SkyError): await currency.query(http,store,'bot:456','456','蜡烛变化查询')
                self.assertEqual(http.raw.await_count,2)
            finally: store.close()

    async def test_bad_response_not_bound(self):
        from astrbot_plugin_sky import currency
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'state.sqlite3')
            try:
                http=types.SimpleNamespace(config={'ovoav_api_key':'SECRET','candle_tokens':'{"123":"TOKEN"}'},raw=AsyncMock(return_value=b'{"code":403,"message":"TOKEN"}'))
                with self.assertRaises(SkyError):await currency.query(http,store,'bot:123','123','蜡烛变化查询')
                self.assertFalse(store.get('currency_bindings','bot:123',{}))
                http.raw.assert_awaited_once()
            finally:store.close()

    def test_wrong_binding_link_and_commands(self):
        from astrbot_plugin_sky import currency
        with self.assertRaises(SkyError):currency.token_for({'candle_tokens':'{"123":"https://yingling3.cn/session?id=abc"}'},'123')
        for command in currency.QUERIES+('光遇token帮助','绑定token'):
            self.assertIsNotNone(re.fullmatch(PATTERN,command))


class NewInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_documented_tomorrow_filter(self):
        from astrbot_plugin_sky.ovoav import public_info
        rows=[{'date':'2026-09-20','quest_cn':'今天任务'}, {'date':'2026-09-21','quest_cn':'明天任务'}]
        http=types.SimpleNamespace(config={'ovoav_api_key':'SECRET'},raw=AsyncMock(return_value=json.dumps({'code':200,'data':{'task_list':rows}}).encode()))
        with patch('astrbot_plugin_sky.ovoav.now',return_value=datetime(2026,9,20,23,tzinfo=TZ)):
            title,text=await public_info(http,'明日任务')
            self.assertIn('明天任务',text)
            self.assertNotIn('今天任务',text)
            self.assertIn('2026-09-21',text)
        self.assertEqual(http.raw.call_args.args[0],'https://ovoav.com/api/sky/mrrw/rw')
        self.assertEqual(http.raw.call_args.kwargs['params'],{'key':'SECRET'})
        with patch('astrbot_plugin_sky.ovoav.now',return_value=datetime(2026,9,22,tzinfo=TZ)):
            self.assertIn('尚未提供',(await public_info(http,'明日任务'))[1])

    async def test_plain_text_and_image_sources(self):
        from astrbot_plugin_sky.ovoav import public_info
        for command,path,body in [('季节列表','qdf/jl','季节历史记录：感恩季'),('光遇进度','jjsj/sj','当前季节结束时间：待更新')]:
            http=types.SimpleNamespace(config={'ovoav_api_key':'SECRET'},raw=AsyncMock(return_value=body.encode()))
            self.assertEqual((await public_info(http,command))[1],body)
            self.assertEqual(http.raw.call_args.args[0],'https://ovoav.com/api/sky/'+path)
        png=b'\x89PNG\r\n\x1a\nfixture'
        http.raw=AsyncMock(return_value=png)
        self.assertEqual((await public_info(http,'活动货币位置'))[1],png)
        self.assertEqual(http.raw.call_args.args[0],'https://ovoav.com/api/sky/hbtp/hb')
        http.raw=AsyncMock(side_effect=[b'{"code":200,"data":{"image_url":"https://example.com/a.png"}}',png])
        self.assertEqual((await public_info(http,'活动货币位置'))[1],png)
        self.assertEqual(http.raw.call_args.kwargs,{'image':True})

    async def test_upstream_error_no_retry(self):
        from astrbot_plugin_sky.ovoav import public_info
        for raw in (b'{"code":403,"msg":"SECRET"}',b'<html>blocked</html>'):
            http=types.SimpleNamespace(config={'ovoav_api_key':'SECRET'},raw=AsyncMock(return_value=raw))
            with self.assertRaises(SkyError) as error:await public_info(http,'季节列表')
            self.assertNotIn('SECRET',str(error.exception))
            http.raw.assert_awaited_once()
