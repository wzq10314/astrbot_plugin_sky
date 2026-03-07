"""
AstrBot 光遇(Sky)插件
通过LLM自然语言交互查询光遇游戏信息、光遇ID绑定、定时推送提醒
API来源: https://gitee.com/Tloml-Starry/Tlon-Sky
"""
import asyncio
import json
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import AstrBotConfig, logger
import astrbot.api.message_components as Comp

# 尝试导入 croniter，如果没有则使用备用方案
try:
    from croniter import croniter
    CRONITER_AVAILABLE = True
except ImportError:
    CRONITER_AVAILABLE = False
    logger.warning("croniter 未安装，将使用备用定时方案。建议安装: pip install croniter")


# 常量定义
SACRIFICE_INFO_TEXT = """🔥 献祭信息

📅 刷新时间: 每周六 00:00
📍 位置: 暴风眼（伊甸之眼）

📖 献祭是光遇中获取升华蜡烛的主要途径

🎁 献祭奖励:
   • 升华蜡烛（用于解锁先祖节点）
   • 每周最多约15根升华蜡烛

💡 小贴士:
   • 进入暴风眼需要20+光翼
   • 献祭时尽量点亮更多石像
   • 可以组队献祭互相照亮
   • 注意躲避冥龙，被照到会损失光翼"""

GRANDMA_SCHEDULE_TEXT = """🍲 老奶奶用餐信息

📍 位置: 雨林隐藏图（秘密花园）
📖 雨林老奶奶会在用餐时间提供烛火

⏰ 用餐时间:
   • 08:00 - 08:30
   • 10:00 - 10:30
   • 12:00 - 12:30
   • 16:00 - 16:30
   • 18:00 - 18:30
   • 20:00 - 20:30

💡 小贴士:
   • 带上火盆或火把可以自动收集烛火
   • 可以挂机收集
   • 每次约可获得1000+烛火（约10根蜡烛）"""


class SkyPlugin(Star):
    """光遇游戏助手插件"""
    
    # API 基础地址
    SKY_API_BASE = "https://api.t1qq.com/api/sky"
    RESOURCES_BASE = "https://ghfast.top/https://raw.githubusercontent.com/A-Kevin1217/resources/master/resources"
    WING_API = "https://s.166.net/config/ds_yy_02/ma75_wing_wings.json"
    WING_QUERY_API = "https://ovoav.com/api/sky/gycx/gka"
    SERVER_STATUS_API = "https://live-queue-sky-merge.game.163.com/queue?type=json"
    
    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
    
    # Cron 表达式配置（标准 5 位格式: 分 时 日 月 周）
    CRON_SCHEDULES = {
        "daily_task": "0 8 * * *",             # 每天 8:00
        "grandma_8": "55 7 * * *",             # 7:55 提前5分钟提醒
        "grandma_10": "55 9 * * *",            # 9:55 提前5分钟提醒
        "grandma_12": "55 11 * * *",           # 11:55 提前5分钟提醒
        "grandma_16": "55 15 * * *",           # 15:55 提前5分钟提醒
        "grandma_18": "55 17 * * *",           # 17:55 提前5分钟提醒
        "grandma_20": "55 19 * * *",           # 19:55 提前5分钟提醒
        "sacrifice": "0 0 * * 6",              # 每周六 00:00
        "debris": "0 0 * * *",                 # 每天 00:00
    }
    
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 从配置读取，并检查是否为空
        self.sky_api_key = config.get("sky_api_key", "")
        self.wing_query_key = config.get("wing_query_key", "")
        self.push_platform = config.get("push_platform", "aiocqhttp")
        self.push_groups = config.get("push_groups", [])
        
        # 推送配置
        self.enable_daily_task_push = config.get("enable_daily_task_push", True)
        self.daily_task_push_time = config.get("daily_task_push_time", "08:00")
        self.enable_grandma_reminder = config.get("enable_grandma_reminder", True)
        self.enable_sacrifice_reminder = config.get("enable_sacrifice_reminder", True)
        self.enable_debris_reminder = config.get("enable_debris_reminder", True)
        
        # API配置
        self.api_timeout = config.get("api_timeout", 10)
        self.cache_duration = config.get("cache_duration", 30)
        
        # 时间格式校验
        self._validate_configs()
        
        # 数据缓存
        self._cache: Dict[str, Dict] = {}
        self._cache_time: Dict[str, float] = {}
        self._cache_locks: Dict[str, asyncio.Lock] = {}
        
        # 数据目录
        plugin_data_dir = StarTools.get_data_dir()
        self.sky_bindings_dir = plugin_data_dir / "sky_bindings"
        self.sky_bindings_dir.mkdir(parents=True, exist_ok=True)
        
        # 文件写入锁
        self._file_lock = asyncio.Lock()
        # 每个用户的操作锁，防止并发下丢更新
        self._user_locks: Dict[str, asyncio.Lock] = {}
        
        # 共享的 ClientSession
        self._session: Optional[aiohttp.ClientSession] = None
        
        # 定时任务
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running = False
        self._active_push_tasks: Set[asyncio.Task] = set()
        self._last_executed: Dict[str, str] = {}
        
        # Cron 迭代器（如果使用 croniter）
        self._cron_iters: Dict[str, any] = {}
        
        logger.info("光遇插件已加载")
    
    def _validate_configs(self):
        """配置校验"""
        if not self.sky_api_key:
            logger.warning("⚠️ sky_api_key 未配置，图片查询功能将不可用")
        if not self.wing_query_key:
            logger.warning("⚠️ wing_query_key 未配置，光翼查询功能将不可用")
        
        # 校验时间格式
        if self.enable_daily_task_push:
            try:
                hour, minute = map(int, self.daily_task_push_time.split(':'))
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError
                # 更新 daily_task 的 cron 表达式（标准 5 位格式: 分 时 日 月 周）
                self.CRON_SCHEDULES["daily_task"] = f"{minute} {hour} * * *"
            except (ValueError, AttributeError):
                logger.error(f"❌ daily_task_push_time 格式错误: {self.daily_task_push_time}，应为 HH:MM 格式，已使用默认 08:00")
                self.daily_task_push_time = "08:00"
    
    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        """获取用户级锁，防止并发修改同一用户数据"""
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]
    
    async def initialize(self):
        """插件加载时自动调用"""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.api_timeout)
        )
        self._running = True
        
        if (self.enable_daily_task_push or self.enable_grandma_reminder or 
            self.enable_sacrifice_reminder or self.enable_debris_reminder):
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            logger.info("光遇定时任务调度器已启动")
            logger.info(f"当前 Cron 配置: {self.CRON_SCHEDULES}")
    
    async def terminate(self):
        """插件关闭时自动调用"""
        self._running = False
        
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        
        if self._active_push_tasks:
            logger.info(f"正在取消 {len(self._active_push_tasks)} 个未完成的推送任务...")
            for task in list(self._active_push_tasks):
                if not task.done():
                    task.cancel()
            
            if self._active_push_tasks:
                await asyncio.gather(*self._active_push_tasks, return_exceptions=True)
            self._active_push_tasks.clear()
        
        if self._session:
            await self._session.close()
            self._session = None
        
        logger.info("光遇插件已终止")
    
    def _create_tracked_task(self, coro) -> asyncio.Task:
        """创建被跟踪的异步任务"""
        task = asyncio.create_task(coro)
        self._active_push_tasks.add(task)
        
        def cleanup(t):
            self._active_push_tasks.discard(t)
        
        task.add_done_callback(cleanup)
        return task
    
    def _build_unified_msg_origin(self, group_id: str) -> str:
        """构造统一消息来源标识符"""
        if ":" in str(group_id):
            return str(group_id)
        
        return f"{self.push_platform}:GroupMessage:{group_id}"
    
    # ==================== 数据文件操作 ====================
    
    def _get_sky_binding_file(self, user_id: str) -> Path:
        """获取用户光遇ID绑定文件路径"""
        return self.sky_bindings_dir / f"{user_id}.json"
    
    async def _load_json(self, file_path: Path, default: Optional[dict] = None) -> dict:
        """加载JSON文件（异步安全）"""
        if default is None:
            default = {}
        
        if not file_path.exists():
            return default.copy()
        
        try:
            async with self._file_lock:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if not content.strip():
                        logger.warning(f"JSON文件为空 {file_path}，使用默认值")
                        return default.copy()
                    return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败 {file_path}: {e}，为避免数据覆盖，抛出异常")
            raise RuntimeError(f"用户数据文件损坏，请检查: {file_path}") from e
        except Exception as e:
            logger.error(f"读取文件失败 {file_path}: {e}，为避免数据覆盖，抛出异常")
            raise RuntimeError(f"无法读取用户数据: {file_path}") from e
    
    async def _save_json(self, file_path: Path, data: dict):
        """保存JSON文件（异步安全，带锁保护）"""
        try:
            async with self._file_lock:
                temp_path = file_path.with_suffix('.tmp')
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                temp_path.replace(file_path)
        except Exception as e:
            logger.error(f"保存JSON文件失败 {file_path}: {e}")
            raise
    
    async def _get_user_sky_data(self, user_id: str) -> dict:
        """获取用户光遇ID绑定数据"""
        file_path = self._get_sky_binding_file(user_id)
        try:
            data = await self._load_json(file_path)
            if not data:
                data = {
                    "user_id": user_id,
                    "ids": [],
                    "current_id": None
                }
                await self._save_json(file_path, data)
            return data
        except RuntimeError:
            logger.error(f"用户 {user_id} 的数据文件损坏，请手动检查")
            return {
                "user_id": user_id,
                "ids": [],
                "current_id": None,
                "_error": "数据文件损坏，请检查服务器文件"
            }
    
    async def _save_user_sky_data(self, user_id: str, data: dict):
        """保存用户光遇ID绑定数据"""
        file_path = self._get_sky_binding_file(user_id)
        await self._save_json(file_path, data)
    
    # ==================== 缓存操作 ====================
    
    def _get_cache_lock(self, key: str) -> asyncio.Lock:
        """获取指定缓存键的锁"""
        if key not in self._cache_locks:
            self._cache_locks[key] = asyncio.Lock()
        return self._cache_locks[key]
    
    def _get_cache(self, key: str) -> Optional[Dict]:
        """获取缓存数据"""
        if key in self._cache:
            cache_time = self._cache_time.get(key, 0)
            if time.time() - cache_time < self.cache_duration * 60:
                return self._cache[key]
        return None
    
    def _set_cache(self, key: str, data: Dict):
        """设置缓存数据"""
        self._cache[key] = data
        self._cache_time[key] = time.time()
    
    # ==================== API请求 ====================
    
    def _mask_url(self, url: str) -> str:
        """隐藏 URL 中的敏感信息（API Key）"""
        masked = re.sub(r'([&?]key=)[^&]+', r'\1***', url)
        return masked
    
    async def _fetch_json(self, url: str, use_cache: bool = True, cache_key: Optional[str] = None) -> Optional[Dict]:
        """从URL获取JSON数据"""
        if use_cache and cache_key:
            cached = self._get_cache(cache_key)
            if cached is not None:
                return cached
        
        lock = self._get_cache_lock(cache_key) if use_cache and cache_key else None
        
        if lock:
            await lock.acquire()
        
        try:
            if use_cache and cache_key:
                cached = self._get_cache(cache_key)
                if cached is not None:
                    return cached
            
            if self._session is None:
                return None
            
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    try:
                        text = await resp.text()
                        if not text.strip():
                            logger.error(f"响应为空 ({self._mask_url(url)})")
                            return None
                        data = json.loads(text)
                        if use_cache and cache_key:
                            self._set_cache(cache_key, data)
                        return data
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        logger.error(f"JSON解析或解码失败 ({self._mask_url(url)}): {e}")
                        return None
                    except aiohttp.ClientPayloadError as e:
                        logger.error(f"响应 payload 错误 ({self._mask_url(url)}): {e}")
                        return None
                else:
                    logger.error(f"请求失败 ({self._mask_url(url)}): HTTP {resp.status}")
                    return None
        except aiohttp.ClientError as e:
            logger.error(f"网络请求失败 ({self._mask_url(url)}): {e}")
            return None
        except Exception as e:
            logger.error(f"获取数据失败 ({self._mask_url(url)}): {e}")
            return None
        finally:
            if lock:
                lock.release()
    
    # 新增：下载图片到内存
    async def _download_image(self, url: str) -> Optional[bytes]:
        """下载图片到内存"""
        try:
            if self._session is None:
                return None
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    logger.error(f"下载图片失败: HTTP {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
            return None
    
    # ==================== 时间工具 ====================
    
    def _get_beijing_time(self) -> datetime:
        """获取北京时间"""
        return datetime.now(self.BEIJING_TZ)
    
    # ==================== 核心逻辑方法 ====================
    
    async def _get_debris_info_data(self) -> Dict:
        """获取碎石信息数据 - 本地计算方案（基于国服规律）"""
        now = self._get_beijing_time()
        day = now.day
        day_of_week = now.weekday()
        
        is_first_half = day <= 15
        # 国服碎石规律：上半月周二/周六/周日，下半月周三/周五/周日
        # weekday: 周一=0, 周二=1, 周三=2, 周四=3, 周五=4, 周六=5, 周日=6
        valid_days = [1, 5, 6] if is_first_half else [2, 4, 6]
        
        if day_of_week not in valid_days:
            return {"has_debris": False}
        
        # 碎石地图轮换规律（基于日期）
        maps = ["暮土", "禁阁", "云野", "雨林", "霞谷"]
        map_name = maps[(day - 1) % len(maps)]
        
        # 碎石类型：周一到周四=黑石，周五/周六/周日=红石
        # weekday: 周一=0, 周二=1, 周三=2, 周四=3, 周五=4, 周六=5, 周日=6
        if day_of_week in [4, 5, 6]:  # 周五、周六、周日
            debris_type = "红石"
        else:  # 周一到周四
            debris_type = "黑石"
        
        # 各地图不同日期的具体位置 (key 是 weekday)
        locations = {
            "云野": {1: "蝴蝶平原", 2: "仙乡", 4: "云顶浮石", 5: "幽光山洞", 6: "圣岛"},
            "雨林": {1: "荧光森林", 2: "密林遗迹", 4: "大树屋", 5: "雨林神殿", 6: "秘密花园"},
            "霞谷": {1: "滑冰场", 2: "滑冰场", 4: "圆梦村", 5: "圆梦村", 6: "雪隐峰"},
            "暮土": {1: "边陲荒漠", 2: "远古战场", 4: "黑水港湾", 5: "巨兽荒原", 6: "失落方舟"},
            "禁阁": {1: "星光沙漠", 2: "星光沙漠", 4: "星光沙漠·一隅", 5: "星光沙漠·一隅", 6: "星光沙漠·一隅"}
        }
        
        location = locations.get(map_name, {}).get(day_of_week, "未知位置")
        
        return {
            "has_debris": True,
            "map_name": map_name,
            "location": location,
            "debris_type": debris_type,
            "times": ["10:08", "14:08", "22:08"]  # 国服标准时间
        }
    
    def _format_debris_result(self, data: Dict) -> str:
        """格式化碎石信息结果"""
        if not data.get("has_debris"):
            return "💎 今日碎石信息\n\n今日无碎石"
        
        result = f"💎 今日碎石信息\n\n"
        result += f"📍 地图: {data['map_name']}\n"
        result += f"📍 位置: {data['location']}\n"
        result += f"🔷 类型: {data['debris_type']}\n\n"
        
        # 使用 API 返回的时间
        times = data.get("times", [])
        if times:
            result += f"⏰ 坠落时间:\n"
            for t in times:
                result += f"   • {t}\n"
            result += "\n"
        else:
            # 默认时间（备用）
            result += f"⏰ 坠落时间:\n"
            result += f"   • 10:08 (持续约50分钟)\n"
            result += f"   • 14:08 (持续约50分钟)\n"
            result += f"   • 22:08 (持续约50分钟)\n\n"
        
        result += f"🎁 奖励: 升华蜡烛\n"
        result += f"💡 完成碎石任务可以获得升华蜡烛奖励"
        
        # 如果有路线图链接
        route_url = data.get("route_url")
        if route_url:
            result += f"\n\n🗺️ 查看路线: {route_url}"
        
        return result
    
    def _get_month_debris_data(self, year: int, month: int) -> List[Dict]:
        """获取指定月份的碎石信息列表"""
        import calendar
        
        # 获取该月天数
        _, days_in_month = calendar.monthrange(year, month)
        
        debris_list = []
        for day in range(1, days_in_month + 1):
            date = datetime(year, month, day)
            day_of_week = date.weekday()
            is_first_half = day <= 15
            
            # 判断是否有碎石：上半月周二/周六/周日，下半月周三/周五/周日
            valid_days = [1, 5, 6] if is_first_half else [2, 4, 6]
            
            if day_of_week not in valid_days:
                continue
            
            # 计算碎石信息
            maps = ["暮土", "禁阁", "云野", "雨林", "霞谷"]
            map_name = maps[(day - 1) % len(maps)]
            
            # 碎石类型：周一到周四=黑石，周五/周六/周日=红石
            if day_of_week in [4, 5, 6]:
                debris_type = "红石"
            else:
                debris_type = "黑石"
            
            locations = {
                "云野": {1: "蝴蝶平原", 2: "仙乡", 4: "云顶浮石", 5: "幽光山洞", 6: "圣岛"},
                "雨林": {1: "荧光森林", 2: "密林遗迹", 4: "大树屋", 5: "雨林神殿", 6: "秘密花园"},
                "霞谷": {1: "滑冰场", 2: "滑冰场", 4: "圆梦村", 5: "圆梦村", 6: "雪隐峰"},
                "暮土": {1: "边陲荒漠", 2: "远古战场", 4: "黑水港湾", 5: "巨兽荒原", 6: "失落方舟"},
                "禁阁": {1: "星光沙漠", 2: "星光沙漠", 4: "星光沙漠·一隅", 5: "星光沙漠·一隅", 6: "星光沙漠·一隅"}
            }
            
            location = locations.get(map_name, {}).get(day_of_week, "未知位置")
            
            debris_list.append({
                "date": date.strftime("%m月%d日"),
                "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][day_of_week],
                "map_name": map_name,
                "location": location,
                "debris_type": debris_type
            })
        
        return debris_list
    
    def _format_month_debris_result(self, year: int, month: int, debris_list: List[Dict]) -> str:
        """格式化整月碎石信息"""
        result = f"💎 {year}年{month}月碎石日历\n\n"
        
        if not debris_list:
            result += "本月无碎石数据"
            return result
        
        # 按日期排序
        debris_list.sort(key=lambda x: x["date"])
        
        for debris in debris_list:
            icon = "⚫" if debris["debris_type"] == "黑石" else "🔴"
            result += f"{icon} {debris['date']} ({debris['weekday']})\n"
            result += f"   {debris['map_name']} - {debris['location']} ({debris['debris_type']})\n\n"
        
        result += "⏰ 坠落时间: 10:08 / 14:08 / 22:08\n"
        result += "💡 黑石奖励烛火，红石奖励升华蜡烛"
        
        return result
    
    async def _get_season_progress_data(self) -> Optional[Dict]:
        """获取季节进度数据"""
        url = f"{self.RESOURCES_BASE}/json/SkyChildrenoftheLight/GameProgress.json"
        return await self._fetch_json(url, use_cache=True, cache_key="season_progress")
    
    def _format_season_result(self, data: Optional[Dict]) -> str:
        """格式化季节进度结果"""
        if not data:
            return "❌ 获取季节信息失败，请稍后重试"
        
        season = data.get("season", {})
        season_name = season.get("name", "未知季节")
        start_date = season.get("startDate", "")
        end_date = season.get("endDate", "")
        required_true = season.get("requiredCandlesTrue", 0)
        required_false = season.get("requiredCandlesFalse", 0)
        
        now = self._get_beijing_time()
        remaining = "未知"
        days = 0
        
        if end_date and isinstance(end_date, str):
            try:
                date_str = end_date.strip()
                if not date_str:
                    remaining = "未知"
                else:
                    date_part = date_str.split()[0]
                    date_part = date_part.replace("-", "/")
                    
                    end = datetime.strptime(date_part, "%Y/%m/%d")
                    end = end.replace(tzinfo=self.BEIJING_TZ)
                    diff = end - now
                    
                    if diff.total_seconds() <= 0:
                        remaining = "已结束"
                        days = 0
                    else:
                        days = diff.days
                        hours = diff.seconds // 3600
                        minutes = (diff.seconds % 3600) // 60
                        remaining = f"{days}天{hours}时{minutes}分" if days > 0 else f"{hours}时{minutes}分"
            except (ValueError, IndexError, AttributeError) as e:
                logger.warning(f"季节结束时间解析失败 '{end_date}': {e}")
                remaining = "未知"
                days = 0
        
        result = f"🌸 当前季节: {season_name}\n"
        if start_date:
            result += f"📅 开始时间: {start_date}\n"
        if end_date:
            result += f"📅 结束时间: {end_date}\n"
        result += f"⏰ 剩余时间: {remaining}\n"
        
        if days > 0:
            days_with = (required_true + 5) // 6
            days_without = (required_false + 4) // 5
            result += f"\n📊 毕业所需天数:\n"
            result += f"   有季卡: 约{days_with}天 ({required_true}根季节蜡烛)\n"
            result += f"   无季卡: 约{days_without}天 ({required_false}根季节蜡烛)"
        
        return result
    
    async def _get_traveling_spirit_data(self) -> Optional[Dict]:
        """获取复刻先祖数据 - 修复月份回退逻辑"""
        url = f"{self.RESOURCES_BASE}/json/SkyChildrenoftheLight/RegressionRecords.json"
        records = await self._fetch_json(url, use_cache=True, cache_key="traveling_spirit")
        
        if not records:
            return None
        
        now = self._get_beijing_time()
        current_year = now.year
        
        year_data = None
        for record in records:
            if record.get("year") == current_year:
                year_data = record
                break
        
        if not year_data:
            return None
        
        year_record = year_data.get("yearRecord", [])
        if not year_record:
            return None
        
        # 按月份倒序排列，从高月份到低月份查找第一个有数据的月份
        sorted_months = sorted(year_record, key=lambda x: x.get("month", 0), reverse=True)
        
        for month_data in sorted_months:
            month_record = month_data.get("monthRecord", [])
            if month_record:  # 找到第一个有数据的月份
                # 按日期排序取最新记录
                sorted_records = sorted(month_record, key=lambda x: x.get("day", 0))
                latest = sorted_records[-1]
                
                # 修正：确保字典正确闭合
                return {
                    "spirit_name": latest.get("name", "未知先祖"),
                    "spirit_day": latest.get("day", 0),
                    "month": month_data.get("month", 0),
                    "year": current_year
                }
        
        # 如果今年所有月份都没数据，返回 None
        return None
    
    def _format_traveling_spirit_result(self, data: Optional[Dict]) -> str:
        """格式化复刻先祖结果"""
        if not data:
            return "暂无复刻数据"
        
        result = f"🎭 当前复刻先祖: {data['spirit_name']}\n\n"
        result += f"📅 到达时间: {data['year']}年{data['month']}月{data['spirit_day']}日\n"
        result += f"⏰ 停留时间: 约4天\n\n"
        result += f"💡 发送「复刻兑换图」查看兑换物品详情"
        return result
    
    async def _get_server_status_data(self) -> Optional[Dict]:
        """获取服务器状态数据"""
        return await self._fetch_json(self.SERVER_STATUS_API, use_cache=False)
    
    def _format_server_status_result(self, data: Optional[Dict]) -> str:
        """格式化服务器状态结果"""
        if data is None:
            return "❌ 获取服务器状态失败，可能正在维护更新"
        
        ret = data.get("ret", 0)
        pos = data.get("pos", 0)
        wait_time = data.get("wait_time", 0)
        
        if ret != 1:
            return "✅ 当前光遇服务器畅通，无需排队"
        
        hours = wait_time // 3600
        minutes = (wait_time % 3600) // 60
        seconds = wait_time % 60
        
        if hours > 0:
            time_display = f"{hours}时{minutes}分{seconds}秒"
        elif minutes > 0:
            time_display = f"{minutes}分{seconds}秒"
        else:
            time_display = f"{seconds}秒"
        
        result = f"⏳ 当前光遇服务器排队中\n\n"
        result += f"👥 排队人数: {pos}位\n"
        result += f"⏰ 预计等待时间: {time_display}"
        return result
    
    async def _get_wing_count_data(self) -> Optional[List]:
        """获取光翼统计数据"""
        return await self._fetch_json(self.WING_API, use_cache=True, cache_key="wing_count")
    
    def _format_wing_count_result(self, data: Optional[List]) -> str:
        """格式化光翼统计结果"""
        if not data:
            return "❌ 获取光翼数据失败，请稍后重试"
        
        category_map = {
            "晨岛": "晨",
            "云野": "云",
            "雨林": "雨",
            "霞谷": "霞",
            "暮土": "暮",
            "禁阁": "禁",
            "暴风眼": "暴",
            "复刻永久": "复刻永久",
            "普通永久": "普通永久"
        }
        
        counts = {v: 0 for v in category_map.values()}
        
        for item in data:
            key = category_map.get(item.get("一级标签", ""))
            if key:
                counts[key] += 1
        
        reissue = counts.get("复刻永久", 0)
        normal = counts.get("普通永久", 0)
        
        result = f"🪽 光遇全图光翼统计\n\n"
        result += f"📊 总光翼数量: {len(data)}\n"
        result += f"   永久翼: {reissue + normal}个\n"
        result += f"   (复刻先祖: {reissue}个, 常驻先祖: {normal}个)\n\n"
        
        result += "📍 各图光翼数量:\n"
        for map_name, key in category_map.items():
            if key not in ["复刻永久", "普通永久"]:
                result += f"   {map_name}: {counts[key]}个\n"
        
        result += "\n💡 数据来源: 网易大神"
        return result
    
    # ==================== 图片URL生成（内部使用，对外不暴露key）====================
    
    def _get_daily_task_image_url(self) -> str:
        """获取每日任务图片URL（内部使用）"""
        rand = random.randint(0, 1000000)
        return f"{self.SKY_API_BASE}/sc/scrw?key={self.sky_api_key}&num={rand}"
    
    def _get_season_candle_image_url(self) -> str:
        """获取季节蜡烛图片URL（内部使用）"""
        rand = random.randint(0, 1000000)
        return f"{self.SKY_API_BASE}/sc/scjl?key={self.sky_api_key}&num={rand}"
    
    def _get_big_candle_image_url(self) -> str:
        """获取大蜡烛图片URL（内部使用）"""
        rand = random.randint(0, 1000000)
        return f"{self.SKY_API_BASE}/sc/scdl?key={self.sky_api_key}&num={rand}"
    
    def _get_magic_image_url(self) -> str:
        """获取免费魔法图片URL（内部使用）"""
        rand = random.randint(0, 1000000)
        return f"{self.SKY_API_BASE}/mf/magic?key={self.sky_api_key}&num={rand}"
    
    # 新增：统一的图片查询 handler
    async def _handle_image_query(self, query_type: str) -> Tuple[str, Optional[bytes]]:
        """
        统一处理图片查询，返回 (标题, 图片数据)
        下载图片到内存，避免 URL 泄露 API key
        """
        # 检查 API key
        if not self.sky_api_key:
            return "❌ 管理员未配置 sky_api_key，请联系管理员配置", None
        
        url_map = {
            "daily_task": (self._get_daily_task_image_url(), "🌟 光遇今日每日任务"),
            "season_candle": (self._get_season_candle_image_url(), "🕯️ 光遇今日季节蜡烛位置"),
            "big_candle": (self._get_big_candle_image_url(), "🕯️ 光遇今日大蜡烛位置"),
            "magic": (self._get_magic_image_url(), "✨ 光遇今日免费魔法")
        }
        
        if query_type not in url_map:
            return "❌ 未知的查询类型", None
        
        url, title = url_map[query_type]
        image_data = await self._download_image(url)
        
        if image_data is None:
            return f"{title}\n❌ 图片下载失败，请稍后重试", None
        
        return title, image_data
    
    # ==================== LLM工具函数（已优化描述和命名）====================
    
    @filter.llm_tool(name="get_daily_task_image")
    async def tool_get_daily_tasks(self, event: AstrMessageEvent):
        """
        获取光遇今日每日任务攻略图片。
        
        当用户询问以下内容时必须调用此工具：
        - "今天每日任务是什么"、"今日任务"、"查看今天的任务"
        - "每日任务图片"、"任务攻略"、"今天的每日任务"
        - "光遇今日任务"、"今天要做哪些任务"
        """
        title, image_data = await self._handle_image_query("daily_task")
        
        if image_data is None:
            yield event.plain_result(title)
            return
        
        chain = [
            Comp.Plain(title),
            Comp.Image.fromBytes(image_data)
        ]
        yield event.chain_result(chain)
    
    @filter.llm_tool(name="get_season_candles_location")
    async def tool_get_season_candles(self, event: AstrMessageEvent):
        """
        获取光遇今日季节蜡烛位置图片。
        
        当用户询问以下内容时必须调用此工具：
        - "季节蜡烛在哪"、"季蜡位置"、"今天季节蜡烛"
        - "季节蜡烛图片"、"查看季节蜡烛位置"
        - "光遇季蜡"、"季节蜡烛刷新点"
        """
        title, image_data = await self._handle_image_query("season_candle")
        
        if image_data is None:
            yield event.plain_result(title)
            return
        
        chain = [
            Comp.Plain(title),
            Comp.Image.fromBytes(image_data)
        ]
        yield event.chain_result(chain)
    
    @filter.llm_tool(name="get_big_candles_location")
    async def tool_get_big_candles(self, event: AstrMessageEvent):
        """
        获取光遇今日大蜡烛位置图片。
        
        当用户询问以下内容时必须调用此工具：
        - "大蜡烛在哪"、"今天大蜡烛位置"、"大蜡烛图片"
        - "查看大蜡烛"、"光遇大蜡烛"、"大蜡烛刷新点"
        - "今天哪些图有大蜡烛"、"全图大蜡烛位置"
        """
        title, image_data = await self._handle_image_query("big_candle")
        
        if image_data is None:
            yield event.plain_result(title)
            return
        
        chain = [
            Comp.Plain(title),
            Comp.Image.fromBytes(image_data)
        ]
        yield event.chain_result(chain)
    
    @filter.llm_tool(name="get_free_magic_today")
    async def tool_get_free_magic(self, event: AstrMessageEvent):
        """
        获取光遇今日免费魔法领取信息图片。
        
        当用户询问以下内容时必须调用此工具：
        - "今天免费魔法是什么"、"今日魔法"、"免费魔法"
        - "领取魔法"、"今天可以领什么魔法"、"魔法商店"
        """
        title, image_data = await self._handle_image_query("magic")
        
        if image_data is None:
            yield event.plain_result(title)
            return
        
        chain = [
            Comp.Plain(title),
            Comp.Image.fromBytes(image_data)
        ]
        yield event.chain_result(chain)
    
    @filter.llm_tool(name="get_season_progress")
    async def tool_get_season_progress(self, event: AstrMessageEvent):
        """
        获取当前季节进度、剩余天数和毕业所需蜡烛信息。
        
        当用户询问以下内容时必须调用此工具：
        - "当前季节进度"、"季节还剩多少天"、"本赛季信息"
        - "毕业需要多少蜡烛"、"季节什么时候结束"
        - "现在是什么季节"、"查看季节时间"
        """
        data = await self._get_season_progress_data()
        result = self._format_season_result(data)
        yield event.plain_result(result)
    
    @filter.llm_tool(name="get_today_debris_info")
    async def tool_get_debris_info(self, event: AstrMessageEvent):
        """
        获取今日碎石（黑石/红石）坠落位置和类型信息。
        
        当用户询问以下内容时必须调用此工具：
        - "今天碎石在哪"、"碎石位置"、"黑石在哪"、"红石在哪"
        - "今日碎石"、"查看碎石"、"碎石坠落地点"
        - "今天有没有碎石"、"碎石在哪个图"
        """
        data = await self._get_debris_info_data()
        result = self._format_debris_result(data)
        yield event.plain_result(result)
    
    @filter.llm_tool(name="get_month_debris_calendar")
    async def tool_get_month_debris(self, event: AstrMessageEvent, month: Optional[int] = None):
        """
        获取指定月份的碎石日历，显示整月的碎石安排。
        
        当用户询问以下内容时必须调用此工具：
        - "这个月碎石安排"、"整月碎石"、"碎石日历"
        - "这月有哪些碎石"、"查看月历"、"月度碎石"
        - "什么时候有红石"、"什么时候有黑石"
        
        参数说明:
            month: 月份（1-12），不传则使用当前月份
        """
        now = self._get_beijing_time()
        year = now.year
        query_month = month if month else now.month
        
        if query_month < 1 or query_month > 12:
            yield event.plain_result("❌ 月份必须在 1-12 之间")
            return
        
        debris_list = self._get_month_debris_data(year, query_month)
        result = self._format_month_debris_result(year, query_month, debris_list)
        yield event.plain_result(result)
    
    @filter.llm_tool(name="get_traveling_spirit_info")
    async def tool_get_traveling_spirit(self, event: AstrMessageEvent):
        """
        获取当前复刻先祖（旅行先祖）信息。
        
        当用户询问以下内容时必须调用此工具：
        - "当前复刻先祖是谁"、"这周四复刻"、"本周复刻"
        - "复刻先祖信息"、"现在复刻的是什么"、"旅行先祖"
        """
        data = await self._get_traveling_spirit_data()
        result = self._format_traveling_spirit_result(data)
        yield event.plain_result(result)
    
    @filter.llm_tool(name="get_sacrifice_guide")
    async def tool_get_sacrifice_info(self, event: AstrMessageEvent):
        """
        获取献祭（伊甸之眼）相关信息指南。
        
        当用户询问以下内容时必须调用此工具：
        - "献祭信息"、"伊甸之眼"、"升华蜡烛怎么获得"
        - "献祭刷新时间"、"暴风眼攻略"、"每周献祭"
        """
        yield event.plain_result(SACRIFICE_INFO_TEXT)
    
    @filter.llm_tool(name="get_grandma_dinner_time")
    async def tool_get_grandma_schedule(self, event: AstrMessageEvent):
        """
        获取雨林老奶奶用餐时间和挂机烛火信息。
        
        当用户询问以下内容时必须调用此工具：
        - "老奶奶时间"、"奶奶吃饭时间"、"雨林老奶奶"
        - "挂机收烛火"、"老奶奶开饭时间"、"用餐时间"
        """
        yield event.plain_result(GRANDMA_SCHEDULE_TEXT)
    
    @filter.llm_tool(name="get_total_wings_count")
    async def tool_get_wing_total_count(self, event: AstrMessageEvent):
        """
        获取光遇全图光翼的总数统计（静态数据，所有玩家通用）。
        
        当用户询问以下内容时必须调用此工具：
        - "光翼总数"、"全图多少光翼"、"最多多少光翼"
        - "各图光翼数量"、"光翼统计"、"全图光翼分布"
        """
        data = await self._get_wing_count_data()
        result = self._format_wing_count_result(data)
        yield event.plain_result(result)
    
    @filter.llm_tool(name="query_personal_wings_progress")
    async def tool_query_user_wings(self, event: AstrMessageEvent, sky_id: Optional[str] = None):
        """
        查询用户个人的光翼收集进度，包括每个地图已收集和未收集的详细统计。
        
        当用户询问以下内容时必须调用此工具：
        - "我有多少光翼"、"查询我的光翼"、"我的光翼进度"
        - "我还差多少光翼"、"光翼收集情况"、"查看我的翅膀"
        
        参数说明:
            sky_id: 可选的光遇游戏短ID（数字）。如不提供且用户已绑定ID，则自动查询绑定ID；如未绑定会提示先绑定。
        """
        if not self.wing_query_key:
            yield event.plain_result("❌ 管理员未配置 wing_query_key，请联系管理员配置光翼查询API密钥")
            return
        
        user_id = event.get_sender_id()
        
        # 如果没有提供 sky_id，尝试获取绑定的当前ID
        if sky_id is None:
            user_data = await self._get_user_sky_data(user_id)
            
            if "_error" in user_data:
                yield event.plain_result(f"❌ 数据异常：{user_data['_error']}")
                return
            
            sky_id = user_data.get("current_id")
            if not sky_id:
                if not user_data["ids"]:
                    yield event.plain_result("⚠️ 您还没有绑定任何ID！\n使用「光遇绑定 <ID>」来绑定\n\n💡 Tips：这里需要绑定游戏内短ID哦")
                else:
                    yield event.plain_result("⚠️ 请先使用「光遇切换 <序号>」设置当前ID！")
                return
        
        # URL 编码用户输入，防止参数污染
        encoded_id = quote(str(sky_id), safe='')
        url = f"{self.WING_QUERY_API}?key={self.wing_query_key}&id={encoded_id}&type=json"
        data = await self._fetch_json(url, use_cache=False)
        
        if not data or not data.get("success"):
            error_msg = data.get("message", "未知错误") if data else "网络请求失败"
            yield event.plain_result(f"❌ 查询失败：{error_msg}")
            return
        
        statistics = data.get("statistics", {})
        role_id = data.get("roleId", "未知")
        timestamp = data.get("timestamp", "")
        
        # 格式化时间戳
        time_str = timestamp
        if "T" in timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                pass
        
        result = f"🪽 光翼查询结果\n"
        result += f"📍 ID: {role_id}\n"
        result += f"🕐 数据时间: {time_str}\n\n"
        
        total = statistics.get("total", 0)
        collected = statistics.get("collected", 0)
        uncollected = statistics.get("uncollected", 0)
        
        result += f"📊 光翼统计:\n"
        result += f"   总数: {total}\n"
        result += f"   已收集: {collected}\n"
        result += f"   未收集: {uncollected}\n\n"
        
        # 各地图详细统计
        map_stats = statistics.get("map_statistics", {})
        if map_stats:
            result += "📍 各地图光翼详情:\n"
            result += self._format_wing_map_stats(map_stats)
        
        # 计算总进度百分比
        if total > 0:
            percentage = (collected / total) * 100
            result += f"\n📈 总进度: {percentage:.1f}% ({collected}/{total})"
        
        yield event.plain_result(result)
    
    @filter.llm_tool(name="get_server_queue_status")
    async def tool_get_server_status(self, event: AstrMessageEvent):
        """
        获取光遇服务器当前排队状态和等待时间。
        
        当用户询问以下内容时必须调用此工具：
        - "服务器状态"、"光遇排队"、"服务器排队"
        - "现在需要排队吗"、"服务器炸了吗"、"排队多久"
        """
        data = await self._get_server_status_data()
        result = self._format_server_status_result(data)
        yield event.plain_result(result)
    
    # ==================== 光遇ID绑定功能 ====================
    
    @filter.command("光遇绑定")
    async def bind_sky_id(self, event: AstrMessageEvent, sky_id: str):
        """绑定光遇ID"""
        user_id = event.get_sender_id()
        
        # 使用用户级锁，防止并发修改
        async with self._get_user_lock(user_id):
            user_data = await self._get_user_sky_data(user_id)
            
            if "_error" in user_data:
                yield event.plain_result(f"❌ 数据异常：{user_data['_error']}")
                return
            
            if sky_id in user_data["ids"]:
                yield event.plain_result(f"⚠️ ID {sky_id} 已经绑定过了！")
                return
            
            user_data["ids"].append(sky_id)
            if not user_data["current_id"]:
                user_data["current_id"] = sky_id
            
            await self._save_user_sky_data(user_id, user_data)
            yield event.plain_result(f"✅ 绑定成功！当前ID: {sky_id}\n\n💡 使用「光翼查询」查询该ID的光翼信息")
    
    @filter.command("光遇切换")
    async def switch_sky_id(self, event: AstrMessageEvent, index: int):
        """切换当前光遇ID"""
        user_id = event.get_sender_id()
        
        async with self._get_user_lock(user_id):
            user_data = await self._get_user_sky_data(user_id)
            
            if "_error" in user_data:
                yield event.plain_result(f"❌ 数据异常：{user_data['_error']}")
                return
            
            if not user_data["ids"]:
                yield event.plain_result("⚠️ 您还没有绑定任何ID！\n使用「光遇绑定 <ID>」来绑定")
                return
            
            if index < 1 or index > len(user_data["ids"]):
                yield event.plain_result(f"序号无效！请输入1-{len(user_data['ids'])}之间的数字。")
                return
            
            user_data["current_id"] = user_data["ids"][index - 1]
            await self._save_user_sky_data(user_id, user_data)
            yield event.plain_result(f"✅ 已切换到ID: {user_data['current_id']}")
    
    @filter.command("光遇删除")
    async def delete_sky_id(self, event: AstrMessageEvent, index: int):
        """删除绑定的光遇ID"""
        user_id = event.get_sender_id()
        
        async with self._get_user_lock(user_id):
            user_data = await self._get_user_sky_data(user_id)
            
            if "_error" in user_data:
                yield event.plain_result(f"❌ 数据异常：{user_data['_error']}")
                return
            
            if not user_data["ids"]:
                yield event.plain_result("⚠️ 您还没有绑定任何ID！")
                return
            
            if index < 1 or index > len(user_data["ids"]):
                yield event.plain_result(f"序号无效！请输入1-{len(user_data['ids'])}之间的数字。")
                return
            
            deleted_id = user_data["ids"].pop(index - 1)
            if user_data["current_id"] == deleted_id:
                user_data["current_id"] = user_data["ids"][0] if user_data["ids"] else None
            
            await self._save_user_sky_data(user_id, user_data)
            yield event.plain_result(f"✅ 已删除ID: {deleted_id}")
    
    @filter.command("光遇ID列表")
    async def list_sky_ids(self, event: AstrMessageEvent):
        """列出所有绑定的光遇ID"""
        user_id = event.get_sender_id()
        user_data = await self._get_user_sky_data(user_id)
        
        if "_error" in user_data:
            yield event.plain_result(f"❌ 数据异常：{user_data['_error']}")
            return
        
        if not user_data["ids"]:
            yield event.plain_result("⚠️ 您还没有绑定任何ID！\n使用「光遇绑定 <ID>」来绑定\n\n💡 Tips：这里需要绑定游戏内短ID哦")
            return
        
        result = ["📋 已绑定的ID列表：\n"]
        for i, sky_id in enumerate(user_data["ids"], 1):
            marker = " (当前)" if sky_id == user_data["current_id"] else ""
            result.append(f"{i}. {sky_id}{marker}")
        
        yield event.plain_result("\n".join(result))
    
    # ==================== 光翼查询功能 ====================
    
    def _format_wing_map_stats(self, map_stats: Dict) -> str:
        """格式化光翼地图统计为可读文本"""
        if not map_stats:
            return ""
        
        lines = []
        map_order = ["晨岛", "云野", "雨林", "霞谷", "暮土", "禁阁", "暴风眼", "破晓季"]
        
        sorted_maps = []
        for map_name in map_order:
            if map_name in map_stats:
                sorted_maps.append((map_name, map_stats[map_name]))
        
        for map_name, map_data in map_stats.items():
            if map_name not in map_order:
                sorted_maps.append((map_name, map_data))
        
        for map_name, map_data in sorted_maps:
            if isinstance(map_data, dict):
                total = map_data.get("total", 0)
                collected = map_data.get("collected", 0)
                uncollected = map_data.get("uncollected", 0)
                
                if uncollected == 0 and total > 0:
                    uncollected = total - collected
                
                if uncollected == 0:
                    status = "✅"
                    detail = "已拿满"
                else:
                    status = "❌"
                    detail = f"缺{uncollected}个"
                
                line = f"   {status} {map_name}: {collected}/{total}个 ({detail})"
                lines.append(line)
            else:
                lines.append(f"   • {map_name}: {map_data}个")
        
        return "\n".join(lines) + "\n" if lines else ""
    
    @filter.command("光翼查询")
    async def query_wings(self, event: AstrMessageEvent, sky_id: Optional[str] = None):
        """查询光翼信息"""
        # 检查 API key
        if not self.wing_query_key:
            yield event.plain_result("❌ 管理员未配置 wing_query_key，请联系管理员配置光翼查询API密钥")
            return
        
        user_id = event.get_sender_id()
        
        if sky_id is None:
            user_data = await self._get_user_sky_data(user_id)
            
            if "_error" in user_data:
                yield event.plain_result(f"❌ 数据异常：{user_data['_error']}")
                return
            
            sky_id = user_data.get("current_id")
            if not sky_id:
                if not user_data["ids"]:
                    yield event.plain_result("⚠️ 您还没有绑定任何ID！\n使用「光遇绑定 <ID>」来绑定\n\n💡 Tips：这里需要绑定游戏内短ID哦")
                else:
                    yield event.plain_result("⚠️ 请先使用「光遇切换 <序号>」设置当前ID！")
                return
        
        # URL 编码用户输入，防止参数污染
        encoded_id = quote(str(sky_id), safe='')
        url = f"{self.WING_QUERY_API}?key={self.wing_query_key}&id={encoded_id}&type=json"
        data = await self._fetch_json(url, use_cache=False)
        
        if not data or not data.get("success"):
            error_msg = data.get("message", "未知错误") if data else "网络请求失败"
            yield event.plain_result(f"❌ 查询失败：{error_msg}")
            return
        
        statistics = data.get("statistics", {})
        role_id = data.get("roleId", "未知")
        timestamp = data.get("timestamp", "")
        
        # 格式化时间戳
        time_str = timestamp
        if "T" in timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                pass
        
        result = f"🪽 光翼查询结果\n"
        result += f"📍 ID: {role_id}\n"
        result += f"🕐 数据时间: {time_str}\n\n"
        
        total = statistics.get("total", 0)
        collected = statistics.get("collected", 0)
        uncollected = statistics.get("uncollected", 0)
        
        result += f"📊 光翼统计:\n"
        result += f"   总数: {total}\n"
        result += f"   已收集: {collected}\n"
        result += f"   未收集: {uncollected}\n\n"
        
        # 各地图详细统计
        map_stats = statistics.get("map_statistics", {})
        if map_stats:
            result += "📍 各地图光翼详情:\n"
            result += self._format_wing_map_stats(map_stats)
        
        # 计算总进度百分比
        if total > 0:
            percentage = (collected / total) * 100
            result += f"\n📈 总进度: {percentage:.1f}% ({collected}/{total})"
        
        yield event.plain_result(result)
    
    @filter.command("光翼统计")
    async def count_wings(self, event: AstrMessageEvent):
        """获取全图光翼统计"""
        data = await self._get_wing_count_data()
        result = self._format_wing_count_result(data)
        yield event.plain_result(result)
    
    # ==================== 信息查询命令（复用统一的 handler）====================
    
    @filter.command("每日任务")
    async def daily_tasks(self, event: AstrMessageEvent):
        """获取每日任务图片"""
        title, image_data = await self._handle_image_query("daily_task")
        
        if image_data is None:
            yield event.plain_result(title)
            return
        
        chain = [
            Comp.Plain(title),
            Comp.Image.fromBytes(image_data)
        ]
        yield event.chain_result(chain)
    
    @filter.command("季节蜡烛")
    async def season_candles(self, event: AstrMessageEvent):
        """获取季节蜡烛位置图片"""
        title, image_data = await self._handle_image_query("season_candle")
        
        if image_data is None:
            yield event.plain_result(title)
            return
        
        chain = [
            Comp.Plain(title),
            Comp.Image.fromBytes(image_data)
        ]
        yield event.chain_result(chain)
    
    @filter.command("大蜡烛")
    async def big_candles(self, event: AstrMessageEvent):
        """获取大蜡烛位置图片"""
        title, image_data = await self._handle_image_query("big_candle")
        
        if image_data is None:
            yield event.plain_result(title)
            return
        
        chain = [
            Comp.Plain(title),
            Comp.Image.fromBytes(image_data)
        ]
        yield event.chain_result(chain)
    
    @filter.command("免费魔法")
    async def free_magic(self, event: AstrMessageEvent):
        """获取免费魔法图片"""
        title, image_data = await self._handle_image_query("magic")
        
        if image_data is None:
            yield event.plain_result(title)
            return
        
        chain = [
            Comp.Plain(title),
            Comp.Image.fromBytes(image_data)
        ]
        yield event.chain_result(chain)
    
    @filter.command("季节进度")
    async def season_progress(self, event: AstrMessageEvent):
        """获取季节进度信息"""
        data = await self._get_season_progress_data()
        result = self._format_season_result(data)
        yield event.plain_result(result)
    
    @filter.command("碎石信息")
    async def debris_info(self, event: AstrMessageEvent):
        """获取今日碎石信息"""
        data = await self._get_debris_info_data()
        result = self._format_debris_result(data)
        yield event.plain_result(result)
    
    @filter.command("碎石日历")
    async def month_debris(self, event: AstrMessageEvent, month: int = 0):
        """获取整月碎石日历
        用法: 碎石日历 [月份]
        示例: 碎石日历 或 碎石日历 3
        """
        now = self._get_beijing_time()
        year = now.year
        query_month = month if month > 0 else now.month
        
        if query_month < 1 or query_month > 12:
            yield event.plain_result("❌ 月份必须在 1-12 之间")
            return
        
        debris_list = self._get_month_debris_data(year, query_month)
        result = self._format_month_debris_result(year, query_month, debris_list)
        yield event.plain_result(result)

    @filter.command("测试推送")
    async def test_push(self, event: AstrMessageEvent):
        """测试推送功能 - 手动触发碎石和老奶奶提醒"""
        yield event.plain_result("🔄 正在测试推送功能...")

        # 测试碎石推送
        if self.push_groups:
            await self._push_debris_info()
            yield event.plain_result("✅ 碎石信息已推送到配置的群组")
        else:
            yield event.plain_result("❌ 未配置 push_groups")

        # 测试老奶奶提醒
        if self.push_groups and self.enable_grandma_reminder:
            await self._push_grandma_reminder()
            yield event.plain_result("✅ 老奶奶提醒已推送到配置的群组")
        else:
            yield event.plain_result("❌ 未配置 push_groups 或老奶奶提醒未启用")

    @filter.command("复刻先祖")
    async def traveling_spirit(self, event: AstrMessageEvent):
        """获取复刻先祖信息"""
        data = await self._get_traveling_spirit_data()
        result = self._format_traveling_spirit_result(data)
        yield event.plain_result(result)
    
    @filter.command("献祭信息")
    async def sacrifice_info(self, event: AstrMessageEvent):
        """获取献祭信息"""
        yield event.plain_result(SACRIFICE_INFO_TEXT)
    
    @filter.command("老奶奶时间")
    async def grandma_schedule(self, event: AstrMessageEvent):
        """获取老奶奶用餐时间"""
        yield event.plain_result(GRANDMA_SCHEDULE_TEXT)
    
    @filter.command("光遇状态")
    async def server_status(self, event: AstrMessageEvent):
        """获取光遇服务器状态"""
        data = await self._get_server_status_data()
        result = self._format_server_status_result(data)
        yield event.plain_result(result)
    
    # ==================== 定时任务（修复版）====================
    
    async def _scheduler_loop(self):
        """定时任务调度器 - 使用 Cron 表达式精确触发"""
        logger.info("定时任务调度器启动")
        
        # 初始化 cron 迭代器
        if CRONITER_AVAILABLE:
            self._cron_iters = {}
            base_time = self._get_beijing_time()
            
            for task_name, cron_expr in self.CRON_SCHEDULES.items():
                # 检查任务是否启用
                if task_name.startswith("grandma") and not self.enable_grandma_reminder:
                    continue
                if task_name == "sacrifice" and not self.enable_sacrifice_reminder:
                    continue
                if task_name == "debris" and not self.enable_debris_reminder:
                    continue
                if task_name == "daily_task" and not self.enable_daily_task_push:
                    continue
                
                try:
                    itr = croniter(cron_expr, base_time)
                    next_time = itr.get_next(datetime)
                    # 保存迭代器和下次执行时间
                    self._cron_iters[task_name] = {"itr": itr, "next_time": next_time}
                    logger.info(f"[Cron] {task_name}: {cron_expr} -> 下次执行: {next_time}")
                except Exception as e:
                    logger.error(f"[Cron] 初始化 {task_name} 失败: {e}")

            # 使用 croniter 的调度循环
            await self._croniter_loop()
        else:
            # 备用方案：使用精确的时间检查
            logger.info("使用备用定时方案（建议安装 croniter: pip install croniter）")
            await self._backup_scheduler_loop()
    
    async def _croniter_loop(self):
        """使用 croniter 的精确调度循环"""
        # 从 _cron_iters 获取已初始化的迭代器和下次执行时间
        next_exec_times = {}
        for task_name, data in self._cron_iters.items():
            next_exec_times[task_name] = data["next_time"]
            logger.info(f"[Cron] {task_name} 下次执行: {next_exec_times[task_name]}")

        while self._running:
            try:
                now = self._get_beijing_time()

                for task_name, data in list(self._cron_iters.items()):
                    next_time = next_exec_times.get(task_name)

                    if next_time and now >= next_time:
                        # 更新下次执行时间
                        itr = data["itr"]
                        next_exec_times[task_name] = itr.get_next(datetime)
                        data["next_time"] = next_exec_times[task_name]
                        logger.info(f"[Cron] {task_name} 下次执行时间更新为: {next_exec_times[task_name]}")

                        # 检查是否已经执行过（使用分钟级精度去重）
                        exec_key = f"{task_name}_{now.strftime('%Y-%m-%d_%H:%M')}"
                        if exec_key not in self._last_executed:
                            self._last_executed[exec_key] = now.strftime('%Y-%m-%d')
                            logger.info(f"[定时任务触发] {task_name} at {now}")

                            # 执行对应任务
                            if task_name == "daily_task":
                                self._create_tracked_task(self._push_daily_tasks())
                            elif task_name.startswith("grandma"):
                                self._create_tracked_task(self._push_grandma_reminder())
                            elif task_name == "sacrifice":
                                self._create_tracked_task(self._push_sacrifice_reminder())
                            elif task_name == "debris":
                                self._create_tracked_task(self._push_debris_info())

                # 睡眠到下一秒，精确检查
                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Cron] 调度循环出错: {e}")
                await asyncio.sleep(5)
    
    async def _backup_scheduler_loop(self):
        """备用调度方案（不使用 croniter）- 精确到秒级检查"""
        logger.info("备用调度器启动")
        
        # 记录上次执行的时间戳（精确到秒）
        last_executed_seconds = {}
        
        while self._running:
            try:
                now = self._get_beijing_time()
                current_time_key = now.strftime("%Y-%m-%d_%H:%M:%S")
                current_date = now.strftime("%Y-%m-%d")
                
                # ===== 老奶奶提醒（提前5分钟：7:55, 9:55, 11:55, 15:55, 17:55, 19:55） =====
                if self.enable_grandma_reminder:
                    if now.hour in [7, 9, 11, 15, 17, 19] and now.minute == 55 and now.second == 0:
                        key = f"grandma_{current_time_key}"
                        if key not in last_executed_seconds:
                            last_executed_seconds[key] = True
                            logger.info(f"[定时任务触发] 老奶奶提醒 at {now}")
                            self._create_tracked_task(self._push_grandma_reminder())
                
                # ===== 献祭提醒 =====
                if self.enable_sacrifice_reminder:
                    if now.weekday() == 5 and now.hour == 0 and now.minute == 0 and now.second == 0:
                        key = f"sacrifice_{current_time_key}"
                        if key not in last_executed_seconds:
                            last_executed_seconds[key] = True
                            logger.info(f"[定时任务触发] 献祭提醒 at {now}")
                            self._create_tracked_task(self._push_sacrifice_reminder())
                
                # ===== 碎石提醒 =====
                if self.enable_debris_reminder:
                    target_hour, target_min = map(int, self.daily_task_push_time.split(':'))
                    if now.hour == target_hour and now.minute == target_min and now.second == 0:
                        key = f"debris_{current_time_key}"
                        if key not in last_executed_seconds:
                            last_executed_seconds[key] = True
                            logger.info(f"[定时任务触发] 碎石提醒 at {now}")
                        self._create_tracked_task(self._push_debris_info())
                
                # ===== 每日任务推送 =====
                if self.enable_daily_task_push:
                    target_hour, target_min = map(int, self.daily_task_push_time.split(':'))
                    if now.hour == target_hour and now.minute == target_min and now.second == 0:
                        key = f"daily_task_{current_time_key}"
                        if key not in last_executed_seconds:
                            last_executed_seconds[key] = True
                            logger.info(f"[定时任务触发] 每日任务 at {now}")
                            self._create_tracked_task(self._push_daily_tasks())
                
                # 清理旧记录（每小时整点清理）
                if now.minute == 0 and now.second == 0:
                    cutoff = now.strftime("%Y-%m-%d_%H")
                    last_executed_seconds = {
                        k: v for k, v in last_executed_seconds.items() 
                        if k.startswith(cutoff[:13])  # 保留当前小时的数据
                    }
                
                # 精确睡眠到下一秒
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Backup] 调度循环出错: {e}")
                await asyncio.sleep(5)
    
    async def _push_daily_tasks(self):
        """推送每日任务 - 下载图片后发送，避免 URL 泄露 key"""
        if not self.push_groups:
            logger.warning("[推送] 未配置 push_groups，跳过每日任务推送")
            return
        
        # 检查 API key
        if not self.sky_api_key:
            logger.error("[推送] sky_api_key 未配置，无法推送每日任务")
            return
        
        # 下载图片到内存
        image_url = self._get_daily_task_image_url()
        image_data = await self._download_image(image_url)
        
        if image_data is None:
            logger.error("[推送] 每日任务图片下载失败，取消推送")
            return
        
        logger.info(f"[推送] 开始发送每日任务到 {len(self.push_groups)} 个群组")
        
        async def send_to_group(group_id: str):
            try:
                unified_msg_origin = self._build_unified_msg_origin(group_id)
                
                # 发送图片数据而不是 URL ，避免 key 泄露
                chain = MessageChain()
                chain.chain = [
                    Comp.Plain("🌟 光遇今日每日任务"),
                    Comp.Image.fromBytes(image_data)  # 使用 fromBytes 发送内存中的图片
                ]
                await self.context.send_message(unified_msg_origin, chain)
                logger.info(f"[推送] 每日任务已发送到群组 {group_id}")
            except Exception as e:
                logger.error(f"[推送] 推送每日任务到群组 {group_id} 失败: {e}")
                # 降级时也不发送包含 key 的 URL ，只发送文字提示
                try:
                    unified_msg_origin = self._build_unified_msg_origin(group_id)
                    await self.context.send_message(
                        unified_msg_origin,
                        "🌟 光遇今日每日任务\n\n⚠️ 图片发送失败，请使用「每日任务」命令手动查询"
                    )
                except Exception as e2:
                    logger.error(f"[推送] 降级发送也失败: {e2}")
        
        tasks = [send_to_group(gid) for gid in self.push_groups]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[推送] 每日任务推送完成")
    
    async def _push_grandma_reminder(self):
        """推送老奶奶用餐提醒"""
        if not self.push_groups:
            logger.warning("[推送] 未配置 push_groups，跳老奶奶提醒")
            return
        
        message = "🍲 老奶奶还有5分钟开饭！\n\n"
        message += "📍 位置: 雨林隐藏图（秘密花园）\n"
        message += "⏰ 用餐时间约30分钟\n"
        message += "💡 带上火盆或火把可以自动收集烛火哦~"
        
        logger.info(f"[推送] 开始发送老奶奶提醒到 {len(self.push_groups)} 个群组")

        async def send_to_group(group_id: str):
            try:
                unified_msg_origin = self._build_unified_msg_origin(group_id)
                chain = MessageChain()
                chain.chain = [Comp.Plain(message)]
                await self.context.send_message(unified_msg_origin, chain)
                logger.info(f"[推送] 老奶奶提醒已发送到群组 {group_id}")
            except Exception as e:
                logger.error(f"[推送] 推送老奶奶提醒到群组 {group_id} 失败: {e}")

        tasks = [send_to_group(gid) for gid in self.push_groups]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[推送] 老奶奶提醒推送完成")
    
    async def _push_sacrifice_reminder(self):
        """推送献祭刷新提醒"""
        if not self.push_groups:
            logger.warning("[推送] 未配置 push_groups，跳过献祭提醒")
            return
        
        message = "🔥 献祭已刷新！\n\n"
        message += "📅 每周六凌晨00:00刷新\n"
        message += "💡 记得去暴风眼献祭获取升华蜡烛~"
        
        logger.info(f"[推送] 开始发送献祭提醒到 {len(self.push_groups)} 个群组")

        async def send_to_group(group_id: str):
            try:
                unified_msg_origin = self._build_unified_msg_origin(group_id)
                chain = MessageChain()
                chain.chain = [Comp.Plain(message)]
                await self.context.send_message(unified_msg_origin, chain)
                logger.info(f"[推送] 献祭提醒已发送到群组 {group_id}")
            except Exception as e:
                logger.error(f"[推送] 推送献祭提醒到群组 {group_id} 失败: {e}")

        tasks = [send_to_group(gid) for gid in self.push_groups]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[推送] 献祭提醒推送完成")
    
    async def _push_debris_info(self):
        """推送碎石信息"""
        if not self.push_groups:
            logger.warning("[推送] 未配置 push_groups，跳过碎石提醒")
            return
        
        data = await self._get_debris_info_data()
        
        if not data.get("has_debris"):
            message = "💎 今日碎石信息\n\n"
            message += "今日无碎石"
        else:
            message = f"💎 今日碎石信息\n\n"
            message += f"📍 地图: {data['map_name']}\n"
            message += f"📍 位置: {data['location']}\n"
            message += f"🔷 类型: {data['debris_type']}\n\n"
            message += f"⏰ 坠落时间:\n"
            message += f"   • 10:08 (持续约50分钟)\n"
            message += f"   • 14:08 (持续约50分钟)\n"
            message += f"   • 22:08 (持续约50分钟)\n\n"
            message += f"🎁 奖励: 升华蜡烛\n"
            message += f"💡 完成碎石任务可以获得升华蜡烛奖励~"
        
        logger.info(f"[推送] 开始发送碎石信息到 {len(self.push_groups)} 个群组")

        async def send_to_group(group_id: str):
            try:
                unified_msg_origin = self._build_unified_msg_origin(group_id)
                chain = MessageChain()
                chain.chain = [Comp.Plain(message)]
                await self.context.send_message(unified_msg_origin, chain)
                logger.info(f"[推送] 碎石信息已发送到群组 {group_id}")
            except Exception as e:
                logger.error(f"[推送] 推送碎石信息到群组 {group_id} 失败: {e}")

        tasks = [send_to_group(gid) for gid in self.push_groups]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[推送] 碎石信息推送完成")
    
    # ==================== 菜单命令 ====================
    
    @filter.command("光遇菜单")
    async def sky_menu(self, event: AstrMessageEvent):
        """光遇菜单"""
        menu = """🌟 光遇助手菜单

📋 信息查询:
• 每日任务 - 获取今日每日任务图片
• 季节蜡烛 - 获取季节蜡烛位置图片
• 大蜡烛 - 获取大蜡烛位置图片
• 免费魔法 - 获取今日免费魔法图片
• 季节进度 - 查看当前季节进度
• 碎石信息 - 查看今日碎石信息
• 碎石日历 [月份] - 查看整月碎石安排
• 复刻先祖 - 查看当前复刻先祖
• 献祭信息 - 查看献祭相关信息
• 老奶奶时间 - 查看老奶奶用餐时间
• 光遇状态 - 查看光遇服务器排队状态

🪽 光翼查询:
• 光遇绑定 <ID> - 绑定光遇ID
• 光遇切换 <序号> - 切换当前ID
• 光遇删除 <序号> - 删除绑定的ID
• 光遇ID列表 - 查看所有绑定的ID
• 光翼查询 - 查询当前ID的光翼
• 光翼查询 <ID> - 查询指定ID的光翼
• 光翼统计 - 查看全图光翼统计

📏 身高查询:
• 绑定身高 <备注> <光遇长ID> <好友码> - 绑定身高查询所需信息
• 身高查询 [备注] - 查询指定账号的身高
• 身高列表 - 列出所有绑定的账号
• 身高解绑 [备注] - 解除身高信息绑定

💡 提示: 可以直接用自然语言与我对话查询光遇信息！"""
        
        yield event.plain_result(menu)
    
    # ==================== 身高查询功能 ====================
    
    def _get_height_data_path(self) -> Path:
        """获取身高数据存储路径"""
        return self.sky_bindings_dir / "height_data.json"
    
    async def _load_height_data(self) -> dict:
        """加载身高数据"""
        data_path = self._get_height_data_path()
        async with self._file_lock:
            if data_path.exists():
                try:
                    with open(data_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # 数据迁移：将旧格式转换为新格式
                    migrated_data = {}
                    for user_id, user_data in data.items():
                        if isinstance(user_data, dict):
                            # 检查是否是新格式（值为字典的字典）
                            if user_data and isinstance(list(user_data.values())[0], dict) and "sky_id" in list(user_data.values())[0]:
                                # 已经是新格式
                                migrated_data[user_id] = user_data
                            elif "sky_id" in user_data:
                                # 旧格式，需要迁移
                                migrated_data[user_id] = {
                                    "default": user_data
                                }
                            else:
                                # 空数据或其他格式
                                migrated_data[user_id] = user_data
                        else:
                            migrated_data[user_id] = user_data
                    return migrated_data
                except Exception as e:
                    logger.error(f"[身高查询] 加载数据失败: {e}")
                    return {}
            return {}
    
    async def _save_height_data(self, data: dict):
        """保存身高数据"""
        data_path = self._get_height_data_path()
        async with self._file_lock:
            try:
                with open(data_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"[身高查询] 保存数据失败: {e}")
    
    async def _query_height(self, sky_id: str, friend_code: str) -> dict:
        """查询身高数据"""
        height_key = self.config.get("height_query_key", "")
        if not height_key:
            return {"code": 403, "msg": "身高查询API密钥未配置"}
        
        url = f"https://api.t1qq.com/api/sky/sc/sg?key={height_key}&cx={sky_id}&code={friend_code}"
        logger.info(f"[身高查询] 请求URL: {url}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.api_timeout)) as resp:
                    if resp.status == 200:
                        # 手动读取文本并解析JSON，因为API返回的Content-Type可能是text/json
                        text = await resp.text()
                        try:
                            result = json.loads(text)
                            logger.info(f"[身高查询] API响应: {result}")
                            return result
                        except json.JSONDecodeError as e:
                            logger.error(f"[身高查询] JSON解析失败: {e}, 响应内容: {text[:200]}")
                            return {"code": 0, "msg": "API响应解析失败"}
                    else:
                        logger.error(f"[身高查询] API请求失败，状态码: {resp.status}")
                        return {"code": 0, "msg": f"API请求失败，状态码: {resp.status}"}
        except asyncio.TimeoutError:
            logger.error("[身高查询] API请求超时")
            return {"code": 0, "msg": "API请求超时"}
        except Exception as e:
            logger.error(f"[身高查询] API请求异常: {e}")
            return {"code": 0, "msg": f"API请求异常: {str(e)}"}
    
    def _format_height_result(self, data: dict) -> str:
        """格式化身高查询结果"""
        if data.get("code") != 200:
            error_messages = {
                0: "接口请求失败，过段时间再来查询吧",
                201: "请绑定正确的光遇ID",
                202: "未填写好友邀请码或已失效，请重新绑定",
                400: "Key余额不足，请联系管理员",
                403: "Key错误，获取数据失败",
                405: "服务器繁忙，请稍后再试",
                406: "ID与邀请码不匹配，请输入同ID下的同邀请码",
                500: "查询失败，服务器错误"
            }
            return f"❌ {error_messages.get(data.get('code'), data.get('msg', '未知错误'))}"
        
        # 身高数据 - 直接在最外层
        height_info = data.get("data", {})
        scale = height_info.get("scale", "未知")  # 体型值
        height = height_info.get("height", "未知")  # 身高值
        current_height = height_info.get("currentHeight", "未知")  # 当前身高
        max_height = height_info.get("maxHeight", "未知")  # 最高身高
        min_height = height_info.get("minHeight", "未知")  # 最矮身高
        height_desc = height_info.get("heightDesc", "")  # 身高描述
        
        # 评分数据 - 直接在最外层
        score_info = data.get("score", {})
        scale_score = score_info.get("scaleScore", "未知")
        height_score = score_info.get("heightScore", "未知")
        current_height_score = score_info.get("currentHeightScore", "未知")
        max_height_score = score_info.get("maxHeightScore", "未知")
        min_height_score = score_info.get("minHeightScore", "未知")
        
        # 装扮数据 - 直接在最外层，过滤掉"未知装扮"
        adorn_info = data.get("adorn", {})
        cloak = adorn_info.get("cloak", "")  # 斗篷
        prop = adorn_info.get("prop", "")  # 背饰
        neck = adorn_info.get("neck", "")  # 项链
        mask = adorn_info.get("mask", "")  # 面具
        horn = adorn_info.get("horn", "")  # 头饰
        hair = adorn_info.get("hair", "")  # 发型
        pants = adorn_info.get("pants", "")  # 裤子
        
        # 动作数据 - 直接在最外层
        action_info = data.get("action", {})
        voice = action_info.get("voice", "未知")  # 叫声
        attitude = action_info.get("attitude", "未知")  # 站姿
        
        # 构建装扮数据字符串（显示所有数据，包括未知装扮）
        adorn_items = []
        if cloak:
            adorn_items.append(f"• 斗篷: {cloak}")
        if prop:
            adorn_items.append(f"• 背饰: {prop}")
        if neck:
            adorn_items.append(f"• 项链: {neck}")
        if mask:
            adorn_items.append(f"• 面具: {mask}")
        if horn:
            adorn_items.append(f"• 头饰: {horn}")
        if hair:
            adorn_items.append(f"• 发型: {hair}")
        if pants:
            adorn_items.append(f"• 裤子: {pants}")
        
        adorn_str = "\n".join(adorn_items) if adorn_items else "• 暂无装扮数据"
        
        result = f"""📏 光遇身高查询结果

📊 身高数据:
• 体型值(胖瘦): {scale}
• 身高值(高矮): {height}
• 当前身高: {current_height}
• 最高身高: {max_height}
• 最矮身高: {min_height}

⭐ 身高评分:
• 体型值: {scale_score}
• 身高值: {height_score}
• 当前身高: {current_height_score}
• 最高身高: {max_height_score}
• 最矮身高: {min_height_score}

👗 装扮数据:
{adorn_str}

🎭 动作数据:
• 叫声: {voice}
• 站姿: {attitude}"""
        
        return result
    
    @filter.command("绑定身高")
    async def bind_height(self, event: AstrMessageEvent):
        """绑定身高查询信息
        用法: 绑定身高 <备注> <光遇长ID> <好友码>
        示例: 绑定身高 大号 ABCD-1234-EFGH-5678 123456789
        """
        user_id = str(event.get_sender_id())
        message = event.message_str.strip()
        
        # 解析参数
        parts = message.split()
        if len(parts) < 4:
            yield event.plain_result("❌ 格式错误\n正确格式: 绑定身高 <备注> <光遇长ID> <好友码>\n\n说明:\n• 备注: 给这个账号起的名字，如大号、小号等\n• 光遇长ID: 游戏内设置-账号-长ID\n• 好友码: 游戏内邀请好友的二维码对应的码\n\n示例: 绑定身高 大号 ABCD-1234-EFGH-5678 123456789")
            return
        
        remark = parts[1]
        sky_id = parts[2]
        friend_code = parts[3]
        
        # 加载数据
        height_data = await self._load_height_data()
        
        # 初始化用户数据
        if user_id not in height_data:
            height_data[user_id] = {}
        
        # 保存账号数据
        height_data[user_id][remark] = {
            "sky_id": sky_id,
            "friend_code": friend_code,
            "bind_time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        await self._save_height_data(height_data)
        
        yield event.plain_result(f"✅ 身高信息绑定成功！\n\n备注: {remark}\n光遇ID: {sky_id}\n好友码: {friend_code}\n\n现在可以使用「身高查询 {remark}」命令查询该账号的身高。")
    
    @filter.command("身高查询")
    async def query_height(self, event: AstrMessageEvent):
        """查询身高
        用法: 身高查询 [备注]
        示例: 身高查询 大号
        如果不指定备注，则查询第一个绑定的账号
        """
        user_id = str(event.get_sender_id())
        message = event.message_str.strip()
        logger.info(f"[身高查询] 用户 {user_id} 发起查询")
        
        # 解析参数
        parts = message.split()
        remark = parts[1] if len(parts) > 1 else None
        
        # 加载数据
        height_data = await self._load_height_data()
        logger.info(f"[身高查询] 当前绑定数据: {height_data}")
        
        if user_id not in height_data or not height_data[user_id]:
            yield event.plain_result("❌ 你还没有绑定身高信息\n请使用「绑定身高 <备注> <光遇长ID> <好友码>」命令进行绑定")
            return
        
        user_accounts = height_data[user_id]
        
        # 如果没有指定备注，使用第一个账号
        if remark is None:
            if len(user_accounts) == 1:
                remark = list(user_accounts.keys())[0]
            else:
                # 显示所有绑定的账号列表
                account_list = "\n".join([f"• {r}" for r in user_accounts.keys()])
                yield event.plain_result(f"你绑定了多个账号，请指定备注查询:\n{account_list}\n\n示例: 身高查询 大号")
                return
        
        # 检查备注是否存在
        if remark not in user_accounts:
            account_list = "\n".join([f"• {r}" for r in user_accounts.keys()])
            yield event.plain_result(f"❌ 未找到备注为「{remark}」的账号\n你绑定的账号有:\n{account_list}")
            return
        
        account_info = user_accounts[remark]
        sky_id = account_info.get("sky_id")
        friend_code = account_info.get("friend_code")
        
        logger.info(f"[身高查询] 用户 {user_id} 查询账号「{remark}」: sky_id={sky_id}, friend_code={friend_code}")
        
        if not sky_id or not friend_code:
            yield event.plain_result(f"❌ 账号「{remark}」的绑定信息不完整，请重新绑定\n使用「绑定身高 {remark} <光遇长ID> <好友码>」命令")
            return
        
        # 发送查询中提示
        yield event.plain_result(f"🔍 正在查询「{remark}」的身高数据，请稍候...")
        
        # 查询身高
        result = await self._query_height(sky_id, friend_code)
        logger.info(f"[身高查询] 查询结果: {result}")
        
        # 格式化结果
        formatted_result = self._format_height_result(result)
        
        yield event.plain_result(f"📌 账号: {remark}\n{formatted_result}")
    
    @filter.command("身高解绑")
    async def unbind_height(self, event: AstrMessageEvent):
        """解除身高信息绑定
        用法: 身高解绑 [备注]
        如果不指定备注，则解绑所有账号
        """
        user_id = str(event.get_sender_id())
        message = event.message_str.strip()
        
        # 解析参数
        parts = message.split()
        remark = parts[1] if len(parts) > 1 else None
        
        # 加载数据
        height_data = await self._load_height_data()
        
        if user_id not in height_data or not height_data[user_id]:
            yield event.plain_result("❌ 你还没有绑定身高信息")
            return
        
        user_accounts = height_data[user_id]
        
        if remark is None:
            # 解绑所有账号
            del height_data[user_id]
            await self._save_height_data(height_data)
            yield event.plain_result("✅ 所有身高信息已解绑")
        else:
            # 解绑指定账号
            if remark not in user_accounts:
                account_list = "\n".join([f"• {r}" for r in user_accounts.keys()])
                yield event.plain_result(f"❌ 未找到备注为「{remark}」的账号\n你绑定的账号有:\n{account_list}")
                return
            
            del user_accounts[remark]
            
            # 如果没有账号了，删除用户数据
            if not user_accounts:
                del height_data[user_id]
            
            await self._save_height_data(height_data)
            yield event.plain_result(f"✅ 账号「{remark}」的身高信息已解绑")
    
    @filter.command("身高列表")
    async def list_height_accounts(self, event: AstrMessageEvent):
        """列出所有绑定的身高账号"""
        user_id = str(event.get_sender_id())
        
        # 加载数据
        height_data = await self._load_height_data()
        
        if user_id not in height_data or not height_data[user_id]:
            yield event.plain_result("❌ 你还没有绑定身高信息\n请使用「绑定身高 <备注> <光遇长ID> <好友码>」命令进行绑定")
            return
        
        user_accounts = height_data[user_id]
        
        account_list = []
        for remark, info in user_accounts.items():
            sky_id = info.get("sky_id", "未知")
            bind_time = info.get("bind_time", "未知")
            account_list.append(f"• {remark}\n  光遇ID: {sky_id}\n  绑定时间: {bind_time}")
        
        result = "📋 你绑定的身高账号:\n\n" + "\n\n".join(account_list)
        yield event.plain_result(result)
    
    # ==================== LLM 工具：身高查询 ====================
    
    @filter.llm_tool(name="query_sky_height")
    async def tool_query_height(self, event: AstrMessageEvent, account_name: str = ""):
        """查询光遇身高数据
        
        当用户想要查询身高、测量身高、查看身高数据时调用此工具。
        用户可能会说："查询我的身高"、"看看XX的身高"（XX是用户绑定的账号备注）
        
        Args:
            account_name (str): 要查询的账号备注名。从用户消息中提取，如用户说"查询大号身高"则传入"大号"，
                         说"看看小号的身高"则传入"小号"。如果不确定具体是哪个账号，可以先调用 list_sky_height_accounts 获取账号列表
        """
        user_id = str(event.get_sender_id())
        
        # 加载数据
        height_data = await self._load_height_data()
        
        if user_id not in height_data or not height_data[user_id]:
            yield event.plain_result("❌ 你还没有绑定身高信息\n请使用「绑定身高 <备注> <光遇长ID> <好友码>」命令进行绑定")
            return
        
        user_accounts = height_data[user_id]
        
        # 如果没有指定账号名
        if not account_name:
            if len(user_accounts) == 1:
                account_name = list(user_accounts.keys())[0]
            else:
                # 显示所有绑定的账号列表
                account_list = "\n".join([f"• {r}" for r in user_accounts.keys()])
                yield event.plain_result(f"你绑定了多个账号，请告诉我你想查询哪个:\n{account_list}")
                return
        
        # 尝试匹配账号（支持模糊匹配）
        matched_remark = None
        
        # 精确匹配
        if account_name in user_accounts:
            matched_remark = account_name
        else:
            # 模糊匹配：查找包含关系
            for remark in user_accounts.keys():
                if account_name in remark or remark in account_name:
                    matched_remark = remark
                    break
        
        if matched_remark is None:
            account_list = "\n".join([f"• {r}" for r in user_accounts.keys()])
            yield event.plain_result(f"❌ 未找到「{account_name}」对应的账号\n你绑定的账号有:\n{account_list}")
            return
        
        account_info = user_accounts[matched_remark]
        sky_id = account_info.get("sky_id")
        friend_code = account_info.get("friend_code")
        
        if not sky_id or not friend_code:
            yield event.plain_result(f"❌ 账号「{matched_remark}」的绑定信息不完整，请重新绑定")
            return
        
        # 查询身高
        result = await self._query_height(sky_id, friend_code)
        
        # 格式化结果
        formatted_result = self._format_height_result(result)
        
        yield event.plain_result(f"📌 账号: {matched_remark}\n{formatted_result}")