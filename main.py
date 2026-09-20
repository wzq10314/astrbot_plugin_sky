import asyncio
import base64
import random
import re
import time
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register
import astrbot.api.message_components as Comp

from . import accounts, information, ovoav, currency
from .configuration import load
from .core import Store, SkyError, now, pretty, due_jobs
from .network import HTTP, IMAGES
from .rendering import cards

VERSION = '3.0.0'
HELP = '''Tlon-Sky 光遇菜单
【攻略】
明日任务 / 活动货币位置 / 每日任务 / 今日魔法 / 季蜡 / 大蜡烛 / 任务图 / 季节任务
今日碎石 / 本月碎石 / 2026年9月碎石
【信息与图鉴】
光遇状态 / 光遇公告 / 光翼统计 / 光遇进度
季节列表 / XX季多久未复刻 / 全图鉴参考
26年复刻记录 / 全部年复刻记录 / 26年复刻日历
光遇本月日历 / 光遇下载
【光翼】
光遇绑定 短ID / 光遇ID列表 / 光遇切换 序号 / 光遇删除 序号
光翼查询 [短ID] / 光翼详情 [短ID]
【身高】
光遇绑定好友码 ABCD-EFGH-IJKL
光遇绑定长ID 游戏长ID
光遇身高查询 [好友码或长ID] / 光遇历史身高 / 光遇身高排行榜
【娱乐】
光遇绘画分享 / 存入盲盒ABCD-EFGH-IJKL*国服（私聊）/ 随机好友
【在线蜡烛与资产】
私聊绑定token 完整链接或token / 私聊解绑token / token绑定状态
光遇token帮助 / 蜡烛变化查询 / 季节蜡烛查询 / 爱心变化查询
升华蜡烛查询 / 点赞爱心查询 / 魔法变化查询 / 代币变化查询 / 我的光遇id
【礼包】
国服id绑定 好友码（独角兽）或长ID（t1qq） / 国服id列表 / 国服id切换 序号 / 国服礼包查询
【群管理员】
开启/关闭每日任务推送
开启/关闭老奶奶干饭提醒
开启/关闭献祭刷新提醒
开启/关闭碎石提醒（含提前10分钟提醒）
光遇推送状态 / 光遇接口状态
所有命令可加 # 或 / 前缀；时间按北京时间。
身高和部分查询需要管理员配置第三方接口密钥，可能按次计费。'''

PATTERN = r'(?i)^[#/]?(?:(?:sky|光遇)(?:帮助|菜单|娱乐菜单|(?:服务器)?状态|公告|(?:强制)?更新)|光遇接口状态|光遇推送状态|(?:光遇|国服)?(?:每日|今日)?(?:任务|魔法|季蜡|大蜡烛?)|季节任务|任务图|本月[红黑碎]石|今日[红黑碎]石|碎石路线图|碎石规律(?:说明)?|(?:查询)?\d{4}年\d{1,2}月碎石|全图鉴参考|(?:全部|\d{2}|\d{4})年复刻(?:记录|日历)|季节列表|(?:光遇)?活动(?:货币|代币)(?:位置|点位|点位图|位置图|查询)?|明日任务(?:查询)?|.*季多久未复刻|光翼统计|(?:光遇|游戏|季节|活动)(?:剩余|进度)|光遇本月日历|光遇下载(?:链接)?|下载光遇|光遇(?:绑定|切换|删除)\s*\d+|光遇ID列表|光翼(?:查询|详情)(?:\s*\d+)?|(?:光遇)?绘[画图]分享|存入盲盒.*|随机好友(?:盲盒)?|国服id(?:绑定|切换|删除).*|国服id列表|国服礼包查询|礼包查询帮助|光遇token帮助|(?:绑定token|这是我的token|我的token)(?:[\s：:]+[^\r\n]+)?|解绑token|token绑定状态|蜡烛变化查询|季节蜡烛查询|爱心变化查询|升华蜡烛查询|点赞爱心查询|魔法变化查询|代币变化查询|我的光遇id|光遇绑定(?:好友码|长ID).*|光遇身高查询.*|(?:光遇)?(?:历史身高|身高排行榜)|(?:开启|关闭)(?:每日任务推送|老奶奶干饭提醒|献祭刷新提醒|碎石提醒))$'

PUSH_KEYS = {'每日任务推送':'daily','老奶奶干饭提醒':'grandma','献祭刷新提醒':'sacrifice','碎石提醒':'shard'}


def normalize_activity(command):
    if re.fullmatch(r'(?:光遇)?活动(?:货币|代币)(?:位置|点位|点位图|位置图|查询)?', command):
        return '活动货币位置'
    return command


@register('astrbot_plugin_sky', 'Tlon-Sky Port Contributors', '光遇查询、攻略、资产查询与提醒', VERSION)
class TlonSky(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = load(config)
        self.store = Store(Path(StarTools.get_data_dir('astrbot_plugin_tlon_sky')) / 'sky.sqlite3')
        self.work_lock = asyncio.Lock()
        self.scheduler = None
        self.running = set()
        self.cooldowns = OrderedDict()

    async def initialize(self):
        self.scheduler = asyncio.create_task(self.push_loop())

    async def terminate(self):
        tasks = list(self.running)
        if self.scheduler:
            tasks.append(self.scheduler)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.store.close()

    def is_admin(self, event):
        return event.is_admin() or str(event.get_sender_id()) in self.config['admins']

    def is_group_admin(self, event):
        raw = getattr(event.message_obj, 'raw_message', {})
        role = (raw.get('sender') or {}).get('role') if isinstance(raw,dict) else None
        return self.is_admin(event) or role in ('owner','admin')

    async def emit_text(self, event, text):
        for start in range(0,len(str(text)),1800):
            await event.send(event.plain_result(str(text)[start:start+1800]))

    async def emit_image(self, event, content):
        # Base64 works even when AstrBot and NapCat have separate filesystems.
        encoded = base64.b64encode(content).decode('ascii')
        await event.send(event.chain_result([Comp.Image.fromBase64(encoded)]))

    async def report(self, event, title, text):
        if isinstance(event, ToolEvent):
            event.reports.append(title+'\n'+text)
        if self.config['report_images']:
            images = await asyncio.to_thread(cards, title, text)
            for image in images:
                await self.emit_image(event,image)
        else:
            await self.emit_text(event, title+'\n'+text)

    @filter.llm_tool(name='tlon_sky')
    async def sky_tool(self, event: AstrMessageEvent, command: str) -> str:
        """调用光遇插件处理自然语言中的光遇查询、记录、账号绑定和群提醒请求。
        将用户意图转换为下面的插件命令，无需让用户自己输入命令。
        活动货币、活动代币点位图请调用「活动货币位置」，该功能已接入独角兽，不是每日任务。
        「代币变化查询」是账号资产记录，与活动货币地图位置不同。
        红石查询使用独角兽月历，今日红石返回本月日历；不支持本地规律或路线推算。
        查询：明日任务、活动货币位置、每日任务、今日魔法、季蜡、大蜡烛、季节任务、今日碎石、本月碎石、
        YYYY年M月碎石、光遇状态、光遇公告、光翼统计、
        光遇进度、季节列表、XX季多久未复刻、全图鉴参考、YYYY年复刻记录、
        全部年复刻记录、YYYY年复刻日历、光遇本月日历、光遇下载、光遇绘画分享。
        账号：光遇绑定 短ID、光遇ID列表、光遇切换 序号、光遇删除 序号、
        光翼查询 [短ID]、光翼详情 [短ID]、国服id绑定 好友码（独角兽）或长ID（t1qq）、国服id列表、
        国服id切换 序号、国服id删除 序号、国服礼包查询。
        身高：光遇绑定好友码 好友码、光遇绑定长ID 长ID、光遇身高查询 [好友码或长ID]、
        光遇历史身高、光遇身高排行榜。身高、光翼、礼包可能消耗第三方接口额度。
        在线资产查询（私聊或群聊，仅发送者自己的账号）：蜡烛变化查询、季节蜡烛查询、爱心变化查询、升华蜡烛查询、
        点赞爱心查询、魔法变化查询、代币变化查询、我的光遇id、光遇token帮助。
        不要通过LLM工具传递 token；引导用户私聊发送「绑定token 完整链接或token」，由命令处理器直接保存，不需要用户进入管理后台。不要声称无法私聊绑定。蜡烛仅通过token查询，不支持手动上报记录。
        用户问蜡烛数量、余额或变化时，调用「蜡烛变化查询」，无需用户先报数。
        本插件支持独角兽token资产接口；旧对话中的“只能手动记账”已过时。
        只按实际接口结果回答；变化记录不一定是实时余额，缺少token或查询失败不等于不支持查询。
        娱乐：存入盲盒好友码*国服（也支持国际服/测试服，只能私聊）、随机好友盲盒。
        群提醒：开启或关闭每日任务推送、老奶奶干饭提醒、献祭刷新提醒、碎石提醒；
        光遇推送状态、光遇接口状态。仅当前群管理员可改变当前群订阅。
        只执行用户明确要求的写入/删除/订阅；不得凭猜测补齐数量、好友码、ID或账号名。
        “查身高”未给ID时使用已绑定账号。
        身份和权限由事件确定，不能指定其他用户/群。失败时不要自动重复付费查询。
        工具直接发送图片或文本，并返回执行结果；已发送的内容不要重复大段复述。

        Args:
            command(string): 单条规范插件命令，不是自然语言原句；不带#前缀。例如：每日任务、光遇身高查询、蜡烛变化查询、开启碎石提醒。只使用用户提供的参数。
        """
        if event.get_platform_name() != 'aiocqhttp':
            return '未执行：此光遇插件仅支持 OneBot11/NapCat。'
        if not isinstance(command, str) or len(command) > 256 or '\n' in command or '\r' in command:
            return '未执行：需要单条光遇命令，长度不能超过256字符。'
        command = normalize_activity(command.strip().lstrip('#/').strip())
        if re.match(r'(?i)^(?:绑定token|这是我的token|我的token)[\s：:]+', command):
            return '未执行：token绑定必须由用户私聊发送绑定命令，不经过LLM工具。'
        # Normalize stale LLM vocabulary before validation and deduplication.
        # No old recording command or storage handler is restored.
        if command in ('蜡烛记录', '蜡烛数量查询', '查询蜡烛数量', '蜡烛查询'):
            command = '蜡烛变化查询'
        if not re.fullmatch(PATTERN,command):
            return ('未执行：此命令名称不受支持，不代表插件没有相关功能。活动货币点位请用「活动货币位置」；'
                    '账号资产变化请用「蜡烛变化查询」或「代币变化查询」。其他命令请按工具描述选择，不要据此声称功能不存在。')
        cache = getattr(event,'_tlon_sky_tool_cache',None)
        if cache is None:
            cache = {}
            setattr(event,'_tlon_sky_tool_cache',cache)
        if command in cache:
            return '本条消息已执行过，未重复调用。\n'+cache[command]
        if len(cache) >= 8:
            return '未执行：本条消息已达到光遇工具调用上限。'
        if self.work_lock.locked():
            return '未执行：正在处理其他光遇请求，请稍后再试。'
        async with self.work_lock:
            proxy = ToolEvent(event)
            task = asyncio.current_task()
            self.running.add(task)
            try:
                owner = str(event.get_platform_id())+':'+str(event.get_sender_id())
                async with asyncio.timeout(180):
                    await self.dispatch(proxy,owner,command)
                summary = '\n'.join(proxy.reports or proxy.texts)
                result = f'执行完成；已发送 {proxy.images} 张图片、{len(proxy.texts)} 条文本。\n'+summary[:6000]
            except SkyError as error:
                result = '未完成：'+str(error)
            except TimeoutError:
                result = '请求超时；可能已产生接口费用或发送部分内容，不要自动重试。'
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning('Tlon-Sky LLM tool failed: %s',type(error).__name__)
                result = '请求或消息发送失败；不要声称成功，也不要自动重试付费查询。请检查插件日志。'
            finally:
                self.running.discard(task)
            if command in currency.QUERIES:
                result += ('\n能力说明：本次使用独角兽token资产查询，不是手动记账。'
                           '只根据上面的真实结果说明成功或失败，不要要求用户报数或声称插件只能手动记录。'
                           '接口变化记录不应被解释为无延迟的实时余额。')
            cache[command] = result
            return result

    @filter.regex(PATTERN)
    async def command(self, event: AstrMessageEvent):
        if event.get_platform_name() != 'aiocqhttp':
            return
        if str(event.get_sender_id()) == str(getattr(event.message_obj,'self_id','')):
            return
        command = normalize_activity(event.get_message_str().strip().lstrip('#/').strip())
        owner = str(event.get_platform_id()) + ':' + str(event.get_sender_id())
        if self.work_lock.locked():
            await self.emit_text(event,'正在处理其他光遇请求，请稍后重试。')
            event.stop_event()
            return
        async with self.work_lock:
            task = asyncio.current_task()
            self.running.add(task)
            try:
                async with asyncio.timeout(180):
                    await self.dispatch(event, owner, command)
            except SkyError as error:
                await self.emit_text(event,str(error))
            except TimeoutError:
                await self.emit_text(event,'光遇请求超时，已停止等待。身高查询可能已计费，请勿连续重试。')
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Never log URL, request body, API key, friend code, or upstream body.
                logger.warning('Tlon-Sky request failed: %s', type(error).__name__)
                await self.emit_text(event,'光遇请求或发送失败，请检查网络、接口配置及日志中的 Tlon-Sky 记录。')
            finally:
                self.running.discard(task)
                event.stop_event()

    async def dispatch(self, event, owner, command):
        if re.fullmatch(r'(光遇|sky)(帮助|菜单|娱乐菜单)',command,re.I):
            return await self.report(event,'光遇菜单',HELP)
        if re.fullmatch(r'(光遇|sky)(强制)?更新',command,re.I):
            if not self.is_admin(event):
                raise SkyError('更新说明仅管理员可查看。')
            return await self.emit_text(event,f'AstrBot 移植版 v{VERSION}。请在 AstrBot 插件管理上传新版 ZIP 后重载；数据独立保存在 plugin_data 中。Yunzai 的 git 更新会覆盖成 JS 插件，因此此命令已改为 AstrBot 更新指引。')
        if command == '光遇接口状态':
            if not self.is_admin(event):
                raise SkyError('接口配置状态仅管理员可查看。')
            return await self.emit_text(event,'\n'.join(f'{key}：{"已配置（未验证权限）" if self.config[key] else "未配置"}'
                for key in ('ovoav_api_key','kevcore_api_key','t1qq_api_key')) + '\n身高来源：'+self.config['height_provider']
                + '\n光翼来源：'+self.config['wings_provider'] + '\n礼包来源：'+self.config['gifts_provider'])
        match = re.fullmatch(r'(开启|关闭)(每日任务推送|老奶奶干饭提醒|献祭刷新提醒|碎石提醒)',command)
        if match or command == '光遇推送状态':
            if not event.get_group_id() or not self.is_group_admin(event):
                raise SkyError('请由群主、群管理员或 AstrBot 管理员在目标群操作。')
            origin = str(event.unified_msg_origin)
            sub = self.store.get('subscriptions',origin,{})
            if match:
                sub[PUSH_KEYS[match[2]]] = match[1] == '开启'
                self.store.put('subscriptions',origin,sub)
            return await self.emit_text(event,'\n'.join(f'{name}：{"开启" if sub.get(key) else "关闭"}' for name,key in PUSH_KEYS.items()))
        match = re.fullmatch(r'光遇(绑定|切换|删除)\s*(\d+)',command,re.I)
        if match or command.lower() == '光遇id列表':
            result = accounts.manage_ids(self.store,owner,'wings',match[1] if match else '列表',match[2] if match else '')
            return await self.emit_text(event,result)
        match = re.fullmatch(r'国服id(绑定|切换|删除)\s*(.*)',command,re.I)
        if match or command.lower() == '国服id列表':
            result = accounts.manage_ids(self.store,owner,'gifts',match[1] if match else '列表',match[2].strip() if match else '')
            return await self.emit_text(event,result)
        if command == '礼包查询帮助':
            return await self.emit_text(event,'国服id绑定 好友码（独角兽）或长ID（t1qq）\n国服id列表\n国服id切换 序号\n国服礼包查询\n国服id删除 序号\n默认独角兽：后台 ovoav_api_key。旧 t1qq 来源需要长ID与 t1qq_api_key。绑定后用国服id列表查看，再切换对应序号。')
        token_match = re.fullmatch(r'(?:绑定token|这是我的token|我的token)[\s：:]+(.+)', command, re.I)
        if token_match or command.lower() == '解绑token':
            if not event.is_private_chat():
                raise SkyError('Token只能私聊绑定或解绑。请私聊机器人发送「绑定token 完整链接或token」。')
            result = currency.bind(self.store, owner, token_match[1]) if token_match else currency.unbind(self.store, owner)
            return await self.emit_text(event, result)
        if command.lower() == 'token绑定状态':
            local = self.store.get('currency_tokens', owner, None)
            if local is None:
                try:
                    currency.token_for(self.config, event.get_sender_id())
                    status = '已由后台配置token'
                except SkyError:
                    status = '未绑定token'
            else:
                status = '已保存个人token' if local.get('token') else '已解除本地绑定'
            return await self.emit_text(event, status + '；不代表接口当前授权有效。')
        if command.lower() in ('光遇token帮助','绑定token','这是我的token','我的token'):
            return await self.emit_text(event, currency.HELP)
        if command.startswith(('存入盲盒','随机好友')):
            return await self.emit_text(event,accounts.blind_box(self.store,owner,command,event.is_private_chat()))
        if command in ('碎石路线图','碎石规律','碎石规律说明'):
            return await self.emit_text(event,'本地红石规律和路线图已移除。当前使用独角兽红石月历接口，请发送「本月碎石」；该接口不提供单独路线图；提前提醒另按内置时刻表执行。')
        calendar_command = None
        if re.fullmatch(r'(?:查询)?\d{4}年\d{1,2}月碎石', command):
            calendar_command = command.removeprefix('查询')
        elif re.fullmatch(r'(?:今日|本月)[红黑碎]石', command):
            calendar_command = f'{now().year}年{now().month}月碎石'
        is_height = command.startswith(('光遇身高查询','光遇绑定好友码','光遇绑定长ID','光遇绑定长id')) or command in ('历史身高','光遇历史身高','身高排行榜','光遇身高排行榜')
        local_height = is_height and not command.startswith('光遇身高查询')
        if not local_height:
            stamp = time.monotonic()
            if stamp - self.cooldowns.get(owner,-10000) < self.config['api_cooldown']:
                raise SkyError(f'接口查询冷却中，请间隔 {self.config["api_cooldown"]} 秒再试。')
            self.cooldowns[owner] = stamp
            self.cooldowns.move_to_end(owner)
            while len(self.cooldowns) > 2048:
                self.cooldowns.popitem(last=False)
        async with HTTP(self.config) as http:
            if calendar_command:
                title, result = await ovoav.public_info(http, calendar_command)
                if command.startswith('今日'):
                    await self.emit_text(event,'接口提供当月红石日历，请查看今天对应日期；不再使用本地规则推算。')
                if isinstance(result, bytes):
                    return await self.emit_image(event, result)
                return await self.report(event, title, result)
            if command in currency.QUERIES:
                return await self.report(event, command, await currency.query(http, self.store, owner, event.get_sender_id(), command))
            if is_height:
                text = await accounts.height(http,self.store,owner,command,event.get_sender_name())
                return await self.report(event,'光遇身高',text)
            if command == '国服礼包查询':
                return await self.report(event,'国服礼包',await accounts.gifts(http,self.store,owner))
            match = re.fullmatch(r'光翼(查询|详情)\s*(\d*)',command)
            if match:
                identifier = match[2] or self.store.get('wings',owner,{'current':''})['current']
                if not identifier:
                    raise SkyError('先发送「光遇绑定 游戏短ID」，再查询光翼。')
                if self.config['wings_provider'] == 'ovoav':
                    return await self.emit_image(event, await ovoav.wing_image(http, identifier))
                return await self.report(event,command[:4],await information.wings(http,identifier,match[1]=='详情'))
            if re.fullmatch(r'(光遇|sky)(服务器)?状态',command,re.I):
                return await self.emit_text(event,await information.status(http))
            if re.fullmatch(r'(光遇|sky)公告',command,re.I):
                return await self.report(event,'光遇公告',await information.announcement(http))
            if command == '光翼统计':
                return await self.report(event,command,await information.wing_counts(http))
            if command in ('季节列表','活动货币位置','明日任务','明日任务查询') or re.fullmatch(r'(光遇|游戏|季节|活动)(剩余|进度)',command):
                title, result = await ovoav.public_info(http, command)
                if isinstance(result, bytes):
                    return await self.emit_image(event, result)
                return await self.report(event, title, result)
            if command.endswith('季多久未复刻'):
                return await self.report(event,'季节复刻间隔',await information.season_absence(http,command.removesuffix('多久未复刻')))
            match = re.fullmatch(r'(全部|\d{2}|\d{4})年复刻(记录|日历)',command)
            if match:
                year = None if match[1]=='全部' else int(match[1]) + (2000 if len(match[1])==2 else 0)
                if match[2] == '日历' and year is not None:
                    return await self.report(event,command,await information.reissue_calendar(http,year))
                return await self.report(event,command,await information.reissues(http,year))
            if command in ('光遇下载','光遇下载链接','下载光遇'):
                return await self.emit_text(event,pretty(await http.resource('GameDownload')))
            if command == '光遇本月日历':
                result = await http.kevcore('sky-calendar-cn')
                url = result.get('image_url') if isinstance(result,dict) else result
                if not isinstance(url,str):
                    raise SkyError('日历接口未返回图片地址。')
                return await self.emit_image(event,await http.raw(url,image=True))
            if command == '任务图' or re.fullmatch(r'(光遇|国服)?(每日|今日)?(任务|魔法|季蜡|大蜡烛?)',command):
                for image in await http.task_images():
                    await self.emit_image(event,image)
                return
            if command == '季节任务':
                url = IMAGES+'当前/当前季节任务.jpg'
            elif command == '全图鉴参考':
                url = IMAGES+'其他/全图鉴参考.jpg'
            elif re.fullmatch(r'(光遇)?绘[画图]分享',command):
                url = IMAGES+f'绘画分享/{random.randint(0,720)}.jpg'
            else:
                raise SkyError('命令参数不正确，请发送「光遇菜单」查看。')
            await self.emit_image(event,await http.raw(url,image=True))

    async def push_loop(self):
        while True:
            try:
                await self.push_tick(now())
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning('Tlon-Sky scheduler failed: %s',type(error).__name__)
            await asyncio.sleep(15)

    async def push_tick(self, date):
        jobs = due_jobs(date,self.config)
        for origin,sub in self.store.all('subscriptions'):
            for job in jobs:
                key = 'shard' if job == 'shard_before' else job
                if not sub.get(key):
                    continue
                stamp = date.strftime('%Y-%m-%d %H:%M') + ':' + job
                done = self.store.get('push_done',origin,{})
                if done.get(job) == stamp:
                    continue
                # At-most-once: persist before send, including reloads during this minute.
                done[job] = stamp
                self.store.put('push_done',origin,done)
                try:
                    async with asyncio.timeout(150):
                        await self.send_push(origin,job,date)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning('Tlon-Sky push failed (%s): %s',job,type(error).__name__)

    async def send_push(self, origin, job, date):
        if job == 'shard_before':
            target = (date + timedelta(minutes=10)).strftime('%H:%M')
            parts = [Comp.Plain(f'碎石提前提醒：按内置时刻表，预计10分钟后（{target}）坠落。\n此时刻未由API核验；请以游戏内实际情况为准。发送「本月碎石」查看API月历。')]
            if self.config['push_at_all']:
                parts.insert(0, Comp.At(qq='all'))
            await self.context.send_message(origin, MessageChain(chain=parts))
            return
        key = job
        if key == 'shard':
            async with HTTP(self.config) as http:
                title, result = await ovoav.public_info(http, f'{date.year}年{date.month}月碎石')
            parts = [Comp.Plain(self.config['shard_text'] + '\n' + title)]
            if isinstance(result, bytes):
                parts.append(Comp.Image.fromBase64(base64.b64encode(result).decode('ascii')))
            else:
                parts.append(Comp.Plain(result))
            if self.config['push_at_all']:
                parts.insert(0, Comp.At(qq='all'))
            await self.context.send_message(origin, MessageChain(chain=parts))
            return
        text = self.config[key+'_text']
        components = [Comp.Plain(text)]
        if self.config['push_at_all']:
            components.insert(0,Comp.At(qq='all'))
        await self.context.send_message(origin,MessageChain(chain=components))
        async with HTTP(self.config) as http:
            image_url = self.config[key+'_image']
            if image_url:
                images = [await http.raw(image_url,image=True)]
            elif key == 'daily':
                images = await http.task_images()
            else:
                images = []
        for image in images:
            component = Comp.Image.fromBase64(base64.b64encode(image).decode('ascii'))
            await self.context.send_message(origin,MessageChain(chain=[component]))


class ToolEvent:
    """Keep real event identity/permissions; collect tool results without stopping the agent."""
    def __init__(self,event):
        self.event = event
        self.texts = []
        self.reports = []
        self.images = 0

    def __getattr__(self,name):
        return getattr(self.event,name)

    def plain_result(self,text):
        self.texts.append(str(text))
        return self.event.plain_result(text)

    def chain_result(self,parts):
        self.images += len(parts)
        return self.event.chain_result(parts)
