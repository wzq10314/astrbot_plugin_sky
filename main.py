"""
AstrBot 光遇(Sky)插件
通过LLM自然语言交互查询光遇游戏信息、光遇ID绑定、定时推送提醒
API来源: https://gitee.com/Tloml-Starry/Tlon-Sky
"""
import asyncio
import json
import random
import time
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import quote
from zoneinfo import ZoneInfo  # [修复] Python 3.9+ 推荐时区处理方式

import aiohttp
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import AstrBotConfig, logger
import astrbot.api.message_components as Comp


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
    
    # [修复] 使用 zoneinfo 处理时区（Python 3.9+ 最佳实践）
    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
    
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 从配置读取 API Key
        self.sky_api_key = config.get("sky_api_key", "")
        self.wing_query_key = config.get("wing_query_key", "")
        
        # 推送配置
        self.enable_daily_task_push = config.get("enable_daily_task_push", True)
        self.daily_task_push_time = config.get("daily_task_push_time", "08:00")
        self.push_groups = config.get("push_groups", [])
        # [修复] 新增平台配置，支持多平台适配，默认 aiocqhttp (QQ)
        self.push_platform = config.get("push_platform", "aiocqhttp")
        self.enable_grandma_reminder = config.get("enable_grandma_reminder", True)
        self.enable_sacrifice_reminder = config.get("enable_sacrifice_reminder", True)
        self.enable_debris_reminder = config.get("enable_debris_reminder", True)
        
        # API配置
        self.api_timeout = config.get("api_timeout", 10)
        self.cache_duration = config.get("cache_duration", 30)
        
        # 数据缓存
        self._cache: Dict[str, Dict] = {}
        self._cache_time: Dict[str, float] = {}
        # [修复] 引入缓存锁，防止缓存击穿
        self._cache_locks: Dict[str, asyncio.Lock] = {}
        
        # 使用 StarTools 获取数据目录
        plugin_data_dir = StarTools.get_data_dir()
        self.sky_bindings_dir = plugin_data_dir / "sky_bindings"
        self.sky_bindings_dir.mkdir(parents=True, exist_ok=True)
        
        # 文件写入锁，防止并发写入导致数据损坏
        self._file_lock = asyncio.Lock()
        
        # 共享的 ClientSession
        self._session: Optional[aiohttp.ClientSession] = None
        
        # 定时任务
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running = False
        
        # [修复] 使用集合跟踪活跃的推送子任务，便于统一取消
        self._active_push_tasks: Set[asyncio.Task] = set()
        
        # [修复] 记录每个任务的执行状态，使用更高效的存储结构
        # 格式: {task_type: last_executed_date_str}
        self._last_executed: Dict[str, str] = {}
        
        logger.info("光遇插件已加载")
    
    async def initialize(self):
        """插件加载时自动调用"""
        # 创建共享的 ClientSession
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.api_timeout)
        )
        self._running = True
        
        # 启动定时任务调度器（只要有任意一个提醒功能开启就启动）
        if (self.enable_daily_task_push or self.enable_grandma_reminder or 
            self.enable_sacrifice_reminder or self.enable_debris_reminder):
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            logger.info("光遇定时任务调度器已启动")
    
    async def terminate(self):
        """插件关闭时自动调用"""
        self._running = False
        
        # 取消调度器主任务
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        
        # [修复] 取消所有活跃的推送子任务
        if self._active_push_tasks:
            logger.info(f"正在取消 {len(self._active_push_tasks)} 个未完成的推送任务...")
            for task in list(self._active_push_tasks):
                if not task.done():
                    task.cancel()
            
            # 等待所有任务完成或取消
            if self._active_push_tasks:
                await asyncio.gather(*self._active_push_tasks, return_exceptions=True)
            self._active_push_tasks.clear()
        
        # 关闭 ClientSession
        if self._session:
            await self._session.close()
            self._session = None
        
        logger.info("光遇插件已终止")
    
    # [修复] 辅助方法：创建受跟踪的推送任务
    def _create_tracked_task(self, coro) -> asyncio.Task:
        """创建被跟踪的异步任务，确保可以统一取消"""
        task = asyncio.create_task(coro)
        self._active_push_tasks.add(task)
        
        # 任务完成时自动从集合中移除
        def cleanup(t):
            self._active_push_tasks.discard(t)
        
        task.add_done_callback(cleanup)
        return task
    
    # [修复] 辅助方法：清理过期的 _last_executed 记录
    def _cleanup_last_executed(self, current_date: str):
        """清理非当天的执行记录，防止内存无限增长"""
        # 只保留当天的记录
        keys_to_remove = [
            key for key in self._last_executed.keys() 
            if not key.endswith(f"_{current_date}")
        ]
        for key in keys_to_remove:
            del self._last_executed[key]
    
    # [修复] 辅助方法：构造 unified_msg_origin
    def _build_unified_msg_origin(self, group_id: str) -> str:
        """构造统一消息来源标识符，支持多平台适配"""
        if ":" in str(group_id):
            # 如果已经是 unified_msg_origin 格式，直接使用
            return str(group_id)
        
        # 根据配置的平台构造
        platform = self.push_platform
        # 默认使用 GroupMessage 类型，如需支持私聊可扩展配置
        return f"{platform}:GroupMessage:{group_id}"
    
    # ==================== 数据文件操作 ====================
    
    def _get_sky_binding_file(self, user_id: str) -> Path:
        """获取用户光遇ID绑定文件路径"""
        return self.sky_bindings_dir / f"{user_id}.json"
    
    async def _load_json(self, file_path: Path, default: Optional[dict] = None) -> dict:
        """加载JSON文件（异步安全）
        
        [修复] 区分"文件不存在"和"读取错误"：
        - 文件不存在：返回默认值（初始化新用户）
        - 读取错误：抛出异常，避免误覆盖数据
        """
        if default is None:
            default = {}
        
        # 文件不存在，返回默认值（新用户初始化）
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
                # 使用临时文件写入，防止写入过程中断导致数据损坏
                temp_path = file_path.with_suffix('.tmp')
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                # 原子性替换
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
            # 数据文件损坏，返回空数据但不自动覆盖，让用户知道
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
    
    # [修复] 获取或创建缓存锁，防止缓存击穿
    def _get_cache_lock(self, key: str) -> asyncio.Lock:
        """获取指定缓存键的锁，用于防止缓存击穿"""
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
        """从URL获取JSON数据
        [修复] 扩大异常捕获范围，处理编码错误和连接中断
        """
        # 检查缓存
        if use_cache and cache_key:
            cached = self._get_cache(cache_key)
            if cached is not None:
                return cached
        
        # [修复] 使用锁防止缓存击穿
        lock = self._get_cache_lock(cache_key) if use_cache and cache_key else None
        
        if lock:
            await lock.acquire()
        
        try:
            # 双重检查（获取锁后再次检查缓存）
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
                        # 设置缓存
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
    
    # ==================== 时间工具 ====================
    
    def _get_beijing_time(self) -> datetime:
        """获取北京时间"""
        return datetime.now(self.BEIJING_TZ)
    
    # ==================== 核心逻辑方法 ====================
    
    async def _get_debris_info_data(self) -> Dict:
        """获取碎石信息数据"""
        now = self._get_beijing_time()
        day = now.day
        day_of_week = now.weekday()
        
        is_first_half = day <= 15
        valid_days = [2, 6, 0] if is_first_half else [3, 5, 0]
        
        if day_of_week not in valid_days:
            return {"has_debris": False}
        
        maps = ["暮土", "禁阁", "云野", "雨林", "霞谷"]
        map_name = maps[(day - 1) % len(maps)]
        
        if day_of_week == 0:
            debris_type = "红石" if is_first_half else "黑石"
        elif day_of_week in [2, 3]:
            debris_type = "黑石"
        else:
            debris_type = "红石"
        
        locations = {
            "云野": {2: "蝴蝶平原", 3: "仙乡", 5: "云顶浮石", 6: "幽光山洞", 0: "圣岛"},
            "雨林": {2: "荧光森林", 3: "密林遗迹", 5: "大树屋", 6: "雨林神殿", 0: "秘密花园"},
            "霞谷": {2: "滑冰场", 3: "滑冰场", 5: "圆梦村", 6: "圆梦村", 0: "雪隐峰"},
            "暮土": {2: "边陲荒漠", 3: "远古战场", 5: "黑水港湾", 6: "巨兽荒原", 0: "失落方舟"},
            "禁阁": {2: "星光沙漠", 3: "星光沙漠", 5: "星光沙漠·一隅", 6: "星光沙漠·一隅", 0: "星光沙漠·一隅"}
        }
        
        location = locations.get(map_name, {}).get(day_of_week, "未知位置")
        
        return {
            "has_debris": True,
            "map_name": map_name,
            "location": location,
            "debris_type": debris_type
        }
    
    def _format_debris_result(self, data: Dict) -> str:
        """格式化碎石信息结果"""
        if not data.get("has_debris"):
            return "💎 今日碎石信息\n\n今日无碎石"
        
        result = f"💎 今日碎石信息\n\n"
        result += f"📍 地图: {data['map_name']}\n"
        result += f"📍 位置: {data['location']}\n"
        result += f"🔷 类型: {data['debris_type']}\n\n"
        result += f"⏰ 坠落时间:\n"
        result += f"   • 07:08 (持续约50分钟)\n"
        result += f"   • 13:08 (持续约50分钟)\n"
        result += f"   • 19:08 (持续约50分钟)\n\n"
        result += f"🎁 奖励: 升华蜡烛\n"
        result += f"💡 完成碎石任务可以获得升华蜡烛奖励"
        return result
    
    async def _get_season_progress_data(self) -> Optional[Dict]:
        """获取季节进度数据"""
        url = f"{self.RESOURCES_BASE}/json/SkyChildrenoftheLight/GameProgress.json"
        return await self._fetch_json(url, use_cache=True, cache_key="season_progress")
    
    def _format_season_result(self, data: Optional[Dict]) -> str:
        """格式化季节进度结果
        [修复] 加强时间解析的异常处理
        """
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
        
        # [修复] 严密的时间解析异常处理
        if end_date and isinstance(end_date, str):
            try:
                # 处理多种可能的日期格式
                date_str = end_date.strip()
                if not date_str:
                    remaining = "未知"
                else:
                    # 尝试提取日期部分（处理 "2024/01/01 00:00:00" 或 "2024-01-01" 等格式）
                    date_part = date_str.split()[0]
                    # 统一替换分隔符为 /
                    date_part = date_part.replace("-", "/")
                    
                    end = datetime.strptime(date_part, "%Y/%m/%d")
                    # 设置时区为北京时间
                    end = end.replace(tzinfo=self.BEIJING_TZ)
                    diff = end - now
                    
                    # 检查是否已结束
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
        """获取复刻先祖数据
        [修复] 对 monthRecord 按日期排序，不依赖源数据顺序
        """
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
        
        # 按月份排序，获取最新月份
        sorted_months = sorted(year_record, key=lambda x: x.get("month", 0), reverse=True)
        if not sorted_months:
            return None
        
        latest_month = sorted_months[0]
        month_record = latest_month.get("monthRecord", [])
        
        if not month_record:
            return None
        
        # [修复] 按日期排序，确保取到最新的先祖，不依赖源数据顺序
        sorted_records = sorted(month_record, key=lambda x: x.get("day", 0))
        latest = sorted_records[-1]
        
        return {
            "spirit_name": latest.get("name", "未知先祖"),
            "spirit_day": latest.get("day", 0),
            "month": latest_month.get("month", 0),
            "year": current_year
        }
    
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
    
    # ==================== 图片URL生成（统一处理）====================
    
    def _get_daily_task_image_url(self) -> str:
        """获取每日任务图片URL"""
        rand = random.randint(0, 1000000)
        return f"{self.SKY_API_BASE}/sc/scrw?key={self.sky_api_key}&num={rand}"
    
    def _get_season_candle_image_url(self) -> str:
        """获取季节蜡烛图片URL"""
        rand = random.randint(0, 1000000)
        return f"{self.SKY_API_BASE}/sc/scjl?key={self.sky_api_key}&num={rand}"
    
    def _get_big_candle_image_url(self) -> str:
        """获取大蜡烛图片URL"""
        rand = random.randint(0, 1000000)
        return f"{self.SKY_API_BASE}/sc/scdl?key={self.sky_api_key}&num={rand}"
    
    def _get_magic_image_url(self) -> str:
        """获取免费魔法图片URL"""
        rand = random.randint(0, 1000000)
        return f"{self.SKY_API_BASE}/mf/magic?key={self.sky_api_key}&num={rand}"
    
    # ==================== LLM工具函数 ====================
    
    @filter.llm_tool(name="get_sky_daily_tasks")
    async def tool_get_daily_tasks(self, event: AstrMessageEvent):
        """获取光遇今日每日任务图片
        
        当用户询问"今天有什么任务"、"每日任务是什么"、"光遇任务"时使用此工具。
        """
        yield event.plain_result("🌟 光遇今日每日任务")
        yield event.image_result(self._get_daily_task_image_url())
    
    @filter.llm_tool(name="get_sky_season_candles")
    async def tool_get_season_candles(self, event: AstrMessageEvent):
        """获取光遇季节蜡烛位置图片
        
        当用户询问"季节蜡烛在哪里"、"季蜡位置"、"季节蜡烛"时使用此工具。
        """
        yield event.plain_result("🕯️ 光遇今日季节蜡烛位置")
        yield event.image_result(self._get_season_candle_image_url())
    
    @filter.llm_tool(name="get_sky_big_candles")
    async def tool_get_big_candles(self, event: AstrMessageEvent):
        """获取光遇大蜡烛位置图片
        
        当用户询问"大蜡烛在哪里"、"大蜡位置"、"大蜡烛"时使用此工具。
        """
        yield event.plain_result("🕯️ 光遇今日大蜡烛位置")
        yield event.image_result(self._get_big_candle_image_url())
    
    @filter.llm_tool(name="get_sky_free_magic")
    async def tool_get_free_magic(self, event: AstrMessageEvent):
        """获取光遇免费魔法图片
        
        当用户询问"今天有什么魔法"、"免费魔法"、"魔法"时使用此工具。
        """
        yield event.plain_result("✨ 光遇今日免费魔法")
        yield event.image_result(self._get_magic_image_url())
    
    @filter.llm_tool(name="get_sky_season_progress")
    async def tool_get_season_progress(self, event: AstrMessageEvent):
        """获取当前季节进度信息
        
        当用户询问"现在是什么季节"、"季节还有多久结束"、"季节进度"时使用此工具。
        """
        data = await self._get_season_progress_data()
        result = self._format_season_result(data)
        yield event.plain_result(result)
    
    @filter.llm_tool(name="get_sky_debris_info")
    async def tool_get_debris_info(self, event: AstrMessageEvent):
        """获取今日碎石信息
        
        当用户询问"今天碎石在哪里"、"碎石是什么类型"、"碎石"时使用此工具。
        """
        data = await self._get_debris_info_data()
        result = self._format_debris_result(data)
        yield event.plain_result(result)
    
    @filter.llm_tool(name="get_sky_traveling_spirit")
    async def tool_get_traveling_spirit(self, event: AstrMessageEvent):
        """获取复刻先祖信息
        
        当用户询问"复刻先祖是谁"、"复刻有什么物品"、"复刻"时使用此工具。
        """
        data = await self._get_traveling_spirit_data()
        result = self._format_traveling_spirit_result(data)
        yield event.plain_result(result)
    
    @filter.llm_tool(name="get_sky_sacrifice_info")
    async def tool_get_sacrifice_info(self, event: AstrMessageEvent):
        """获取献祭相关信息
        
        当用户询问"献祭什么时候刷新"、"献祭有什么奖励"、"献祭"时使用此工具。
        """
        yield event.plain_result(SACRIFICE_INFO_TEXT)
    
    @filter.llm_tool(name="get_sky_grandma_schedule")
    async def tool_get_grandma_schedule(self, event: AstrMessageEvent):
        """获取老奶奶用餐时间表
        
        当用户询问"老奶奶什么时候开饭"、"老奶奶在哪里"、"老奶奶"时使用此工具。
        """
        yield event.plain_result(GRANDMA_SCHEDULE_TEXT)
    
    @filter.llm_tool(name="get_sky_wing_count")
    async def tool_get_wing_count(self, event: AstrMessageEvent):
        """获取光遇全图光翼统计
        
        当用户询问"光翼有多少个"、"全图光翼"、"光翼统计"时使用此工具。
        """
        data = await self._get_wing_count_data()
        result = self._format_wing_count_result(data)
        yield event.plain_result(result)
    
    @filter.llm_tool(name="get_sky_server_status")
    async def tool_get_server_status(self, event: AstrMessageEvent):
        """获取光遇服务器状态
        
        当用户询问"光遇服务器状态"、"光遇排队"、"服务器"时使用此工具。
        """
        data = await self._get_server_status_data()
        result = self._format_server_status_result(data)
        yield event.plain_result(result)
    
    # ==================== 光遇ID绑定功能 ====================
    
    @filter.command("光遇绑定")
    async def bind_sky_id(self, event: AstrMessageEvent, sky_id: str):
        """绑定光遇ID"""
        user_id = event.get_sender_id()
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
        # 定义地图顺序，让显示更有序
        map_order = ["晨岛", "云野", "雨林", "霞谷", "暮土", "禁阁", "暴风眼", "破晓季"]
        
        # 先按固定顺序排列存在的地图
        sorted_maps = []
        for map_name in map_order:
            if map_name in map_stats:
                sorted_maps.append((map_name, map_stats[map_name]))
        
        # 添加其他未在顺序列表中的地图
        for map_name, map_data in map_stats.items():
            if map_name not in map_order:
                sorted_maps.append((map_name, map_data))
        
        for map_name, map_data in sorted_maps:
            if isinstance(map_data, dict):
                total = map_data.get("total", 0)
                collected = map_data.get("collected", 0)
                uncollected = map_data.get("uncollected", 0)
                
                # 计算未收集（如果没有uncollected字段，用total-collected）
                if uncollected == 0 and total > 0:
                    uncollected = total - collected
                
                # 使用emoji标记状态
                if uncollected == 0:
                    status = "✅"
                    detail = "已拿满"
                else:
                    status = "❌"
                    detail = f"缺{uncollected}个"
                
                line = f"   {status} {map_name}: {collected}/{total}个 ({detail})"
                lines.append(line)
            else:
                # 处理简单数值格式
                lines.append(f"   • {map_name}: {map_data}个")
        
        return "\n".join(lines) + "\n" if lines else ""
    
    @filter.command("光翼查询")
    async def query_wings(self, event: AstrMessageEvent, sky_id: Optional[str] = None):
        """查询光翼信息"""
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
    
    # ==================== 信息查询命令（复用核心逻辑）====================
    
    @filter.command("每日任务")
    async def daily_tasks(self, event: AstrMessageEvent):
        """获取每日任务图片"""
        yield event.plain_result("🌟 光遇今日每日任务")
        yield event.image_result(self._get_daily_task_image_url())
    
    @filter.command("季节蜡烛")
    async def season_candles(self, event: AstrMessageEvent):
        """获取季节蜡烛位置图片"""
        yield event.plain_result("🕯️ 光遇今日季节蜡烛位置")
        yield event.image_result(self._get_season_candle_image_url())
    
    @filter.command("大蜡烛")
    async def big_candles(self, event: AstrMessageEvent):
        """获取大蜡烛位置图片"""
        yield event.plain_result("🕯️ 光遇今日大蜡烛位置")
        yield event.image_result(self._get_big_candle_image_url())
    
    @filter.command("免费魔法")
    async def free_magic(self, event: AstrMessageEvent):
        """获取免费魔法图片"""
        yield event.plain_result("✨ 光遇今日免费魔法")
        yield event.image_result(self._get_magic_image_url())
    
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
    
    # ==================== 定时任务 ====================
    
    async def _scheduler_loop(self):
        """定时任务调度器（动态计算睡眠时间，避免时间漂移）"""
        last_date = None
        
        while self._running:
            try:
                now = self._get_beijing_time()
                current_date = now.strftime("%Y-%m-%d")
                current_minute = now.minute
                current_hour = now.hour
                
                # [修复] 日期变化时清理过期记录，防止内存无限增长
                if last_date != current_date:
                    if last_date is not None:
                        self._cleanup_last_executed(current_date)
                    last_date = current_date
                
                # [修复] 每日任务推送 - 使用"时间窗口"检查（>= 目标时间），避免精确匹配漏触发
                if self.enable_daily_task_push:
                    task_key = f"daily_task_{current_date}"
                    target_hour, target_min = map(int, self.daily_task_push_time.split(':'))
                    
                    # 检查是否已经到了或过了推送时间，且今天未执行
                    is_time_reached = (current_hour > target_hour or 
                                      (current_hour == target_hour and current_minute >= target_min))
                    
                    if is_time_reached and self._last_executed.get(task_key) != current_date:
                        self._last_executed[task_key] = current_date
                        self._create_tracked_task(self._push_daily_tasks())
                
                # [修复] 老奶奶提醒 - 使用"时间窗口"检查（整点后1分钟内都算）
                if self.enable_grandma_reminder:
                    if current_hour in [8, 10, 12, 16, 18, 20]:
                        grandma_key = f"grandma_{current_date}_{current_hour}"
                        # 整点后1分钟内都算，防止跳过整点
                        if current_minute <= 1 and self._last_executed.get(grandma_key) != current_date:
                            self._last_executed[grandma_key] = current_date
                            self._create_tracked_task(self._push_grandma_reminder())
                
                # [修复] 献祭刷新提醒（周六00:00）- 使用"时间窗口"检查（00:00-00:01）
                if self.enable_sacrifice_reminder:
                    if now.weekday() == 5 and current_hour == 0:  # 周六
                        sacrifice_key = f"sacrifice_{current_date}"
                        # 00:00到00:01之间都算
                        if current_minute <= 1 and self._last_executed.get(sacrifice_key) != current_date:
                            self._last_executed[sacrifice_key] = current_date
                            self._create_tracked_task(self._push_sacrifice_reminder())
                
                # [修复] 碎石提醒（每天08:00）- 使用"时间窗口"检查（08:00-08:01）
                if self.enable_debris_reminder:
                    if current_hour == 8:
                        debris_key = f"debris_{current_date}"
                        # 08:00到08:01之间都算
                        if current_minute <= 1 and self._last_executed.get(debris_key) != current_date:
                            self._last_executed[debris_key] = current_date
                            self._create_tracked_task(self._push_debris_info())
                
                # [修复] 使用微秒级精度计算睡眠时间，避免 59.9 秒导致的死循环空转
                now = self._get_beijing_time()
                sleep_seconds = 60.1 - (now.second + now.microsecond / 1_000_000.0)
                if sleep_seconds < 0.1:  # 防止负数或过小值
                    sleep_seconds = 60.1
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时任务出错: {e}")
                # 异常时也使用修正后的睡眠时间计算
                now = self._get_beijing_time()
                sleep_seconds = 60.1 - (now.second + now.microsecond / 1_000_000.0)
                if sleep_seconds < 0.1:
                    sleep_seconds = 1  # 异常时至少休息1秒，避免快速重试
                await asyncio.sleep(sleep_seconds)
    
    async def _push_daily_tasks(self):
        """推送每日任务（并发发送，避免阻塞）"""
        if not self.push_groups:
            return
        
        image_url = self._get_daily_task_image_url()
        
        async def send_to_group(group_id: str):
            try:
                # [修复] 构造 unified_msg_origin，支持多平台适配
                unified_msg_origin = self._build_unified_msg_origin(group_id)
                
                # 使用 MessageChain 构建消息
                chain = MessageChain()
                chain.chain = [
                    Comp.Plain("🌟 光遇今日每日任务"),
                    Comp.Image.fromURL(image_url)
                ]
                await self.context.send_message(unified_msg_origin, chain)
            except Exception as e:
                logger.error(f"推送每日任务到群组 {group_id} 失败: {e}")
                # 降级方案：发送纯文本链接
                try:
                    unified_msg_origin = self._build_unified_msg_origin(group_id)
                    await self.context.send_message(
                        unified_msg_origin,
                        f"🌟 光遇今日每日任务\n\n图片链接：{image_url}"
                    )
                except Exception as e2:
                    logger.error(f"降级发送文本也失败: {e2}")
        
        # 并发发送给所有群组
        tasks = [send_to_group(gid) for gid in self.push_groups]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _push_grandma_reminder(self):
        """推送老奶奶用餐提醒（并发发送，避免阻塞）"""
        if not self.push_groups:
            return
        
        message = "🍲 老奶奶开饭啦！\n\n"
        message += "📍 位置: 雨林隐藏图\n"
        message += "⏰ 用餐时间约30分钟\n"
        message += "💡 带上火盆或火把可以自动收集烛火哦~"
        
        async def send_to_group(group_id: str):
            try:
                # [修复] 构造 unified_msg_origin，支持多平台适配
                unified_msg_origin = self._build_unified_msg_origin(group_id)
                await self.context.send_message(unified_msg_origin, message)
            except Exception as e:
                logger.error(f"推送老奶奶提醒到群组 {group_id} 失败: {e}")
        
        tasks = [send_to_group(gid) for gid in self.push_groups]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _push_sacrifice_reminder(self):
        """推送献祭刷新提醒（并发发送，避免阻塞）"""
        if not self.push_groups:
            return
        
        message = "🔥 献祭已刷新！\n\n"
        message += "📅 每周六凌晨00:00刷新\n"
        message += "💡 记得去暴风眼献祭获取升华蜡烛~"
        
        async def send_to_group(group_id: str):
            try:
                # [修复] 构造 unified_msg_origin，支持多平台适配
                unified_msg_origin = self._build_unified_msg_origin(group_id)
                await self.context.send_message(unified_msg_origin, message)
            except Exception as e:
                logger.error(f"推送献祭提醒到群组 {group_id} 失败: {e}")
        
        tasks = [send_to_group(gid) for gid in self.push_groups]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _push_debris_info(self):
        """推送碎石信息（并发发送，避免阻塞）"""
        if not self.push_groups:
            return
        
        data = await self._get_debris_info_data()
        if not data.get("has_debris"):
            return
        
        message = f"💎 今日碎石信息\n\n"
        message += f"📍 地图: {data['map_name']}\n"
        message += "💡 完成碎石任务可以获得升华蜡烛奖励~"
        
        async def send_to_group(group_id: str):
            try:
                # [修复] 构造 unified_msg_origin，支持多平台适配
                unified_msg_origin = self._build_unified_msg_origin(group_id)
                await self.context.send_message(unified_msg_origin, message)
            except Exception as e:
                logger.error(f"推送碎石信息到群组 {group_id} 失败: {e}")
        
        tasks = [send_to_group(gid) for gid in self.push_groups]
        await asyncio.gather(*tasks, return_exceptions=True)
    
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

💡 提示: 可以直接用自然语言与我对话查询光遇信息！"""
        
        yield event.plain_result(menu)