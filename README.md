# 

<div align="center">


# Sky 光遇助手

_✨ 你的光遇游戏小助手 ✨_

[![Plugin Version](https://img.shields.io/badge/Latest_Version-v1.0.0-blue.svg?style=for-the-badge&color=76bad9)](https://github.com/wzq10314/astrbot_plugin_sky)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-ff69b4?style=for-the-badge)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)
[![](https://img.shields.io/badge/在GitHub中查看仓库-white?style=for-the-badge&color=24292e&logo=github)](https://github.com/wzq10314/astrbot_plugin_sky)

<img src="https://count.getloli.com/@astrbot_plugin_sky?name=astrbot_plugin_sky&theme=booru-jaypee&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto" alt="count" />

</div>

> **🙏 特别致谢**
> 
> 本插件使用 AI 工具移植自 [**Tlon-Sky**](https://gitee.com/Tloml-Starry/Tlon-Sky)，感谢原作者 [**Tloml-Starry**](https://gitee.com/Tloml-Starry) 的杰出贡献！
> 
> 后续将持续关注并移植原项目的更新内容，为光遇玩家提供更好的服务。

---

## 📦 安装方法

在 AstrBot 插件市场搜索 **sky**，点击安装即可。

或手动安装：

```bash
# 克隆仓库到插件目录
cd /AstrBot/data/plugins
git clone https://github.com/wzq10314/astrbot_plugin_sky

# 控制台重启AstrBot
```

依赖（`aiocqhttp`）会由 AstrBot 自动安装。

---

## 🤝 这是干嘛用的？

这是一个**为光遇(Sky: Children of the Light)玩家打造的智能助手插件**。

无论你是想了解今日任务、查询光翼收集进度，还是想知道服务器排队情况——只需在 QQ、微信、Telegram 等聊天平台发送一条消息，即可快速获取光遇游戏相关信息。

**支持自然语言交互**，直接问"今天光遇有什么任务？"，AI 会自动调用相应功能回复你。

> **一句话总结**：光遇玩家的随身游戏助手。

---

## 💡 功能特色

### 🎯 智能查询
- **每日任务**：自动获取今日每日任务图片
- **蜡烛位置**：季节蜡烛、大蜡烛位置一键查询
- **免费魔法**：每日免费魔法信息
- **季节进度**：当前季节剩余时间、毕业所需天数
- **碎石信息**：今日碎石位置、类型、坠落时间
- **复刻先祖**：当前复刻先祖信息
- **献祭信息**：献祭刷新时间、奖励说明
- **老奶奶时间**：雨林老奶奶用餐时间表
- **服务器状态**：光遇服务器排队状态监控

### 🪽 光翼追踪
- **ID 绑定**：绑定游戏内短ID
- **进度查询**：查询指定ID的光翼收集情况
- **全图统计**：查看全图光翼分布统计

### 🤖 自然语言交互
- **LLM 工具集成**：支持通过自然语言触发查询
- **无需记忆指令**：直接说话即可查询

### ⏰ 定时推送
- **每日任务**：定时推送任务图片
- **老奶奶提醒**：用餐时间自动提醒
- **献祭提醒**：每周六刷新提醒
- **碎石提醒**：每日碎石位置提醒

---

## ⚙️ 配置

安装后在 AstrBot 管理面板的插件配置页填写：

![WebUI 配置界面](webui_preview.png)

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `llm_provider_id` | LLM Provider ID（用于自然语言交互） | 空 |
| `enable_daily_task_push` | 启用每日任务推送 | true |
| `daily_task_push_time` | 每日任务推送时间（HH:MM） | 08:00 |
| `push_groups` | 推送群组列表（UMO格式） | [] |
| `enable_grandma_reminder` | 启用老奶奶干饭提醒 | true |
| `enable_sacrifice_reminder` | 启用献祭刷新提醒 | true |
| `enable_debris_reminder` | 启用碎石提醒 | true |
| `api_timeout` | API请求超时时间（秒） | 10 |
| `cache_duration` | 数据缓存时间（分钟） | 30 |

> [!NOTE]
> 以上配置情况仅供参考，请仔细阅读插件配置页面中各个字段的说明，以插件配置中的说明为准。

> [!TIP]
> **推送群组格式**：UMO 格式，如 `aiocqhttp:GroupMessage:123456`

---

## 🖼️ 效果展示

<table align="center" width="100%">
  <tr>
    <td align="center" width="50%" valign="top">
      <p><b>每日任务</b></p>
      <img src="https://api.t1qq.com/api/sky/sc/scrw?key=qw36BL4Oiq8Kmpefl3bkpIs5IY&num=1" alt="每日任务" width="100%">
    </td>
    <td align="center" width="50%" valign="top">
      <p><b>季节蜡烛</b></p>
      <img src="https://api.t1qq.com/api/sky/sc/scjl?key=qw36BL4Oiq8Kmpefl3bkpIs5IY&num=1" alt="季节蜡烛" width="100%">
    </td>
  </tr>
</table>

---

## ⌨️ 指令大全

### 📋 信息查询

| 指令 | 说明 |
|------|------|
| `每日任务` | 获取今日每日任务图片 |
| `季节蜡烛` | 获取季节蜡烛位置图片 |
| `大蜡烛` | 获取大蜡烛位置图片 |
| `免费魔法` | 获取今日免费魔法图片 |
| `季节进度` | 查看当前季节进度和剩余时间 |
| `碎石信息` | 查看今日碎石位置和类型 |
| `复刻先祖` | 查看当前复刻先祖信息 |
| `献祭信息` | 查看献祭刷新时间和奖励 |
| `老奶奶时间` | 查看老奶奶用餐时间表 |
| `光遇状态` | 查看光遇服务器排队状态 |

### 🪽 光翼查询

| 指令 | 说明 |
|------|------|
| `光遇绑定 <ID>` | 绑定光遇游戏内短ID |
| `光遇切换 <序号>` | 切换当前绑定的ID |
| `光遇删除 <序号>` | 删除绑定的ID |
| `光遇ID列表` | 查看所有绑定的ID |
| `光翼查询` | 查询当前ID的光翼收集情况 |
| `光翼查询 <ID>` | 查询指定ID的光翼 |
| `光翼统计` | 查看全图光翼统计 |

### 🌟 其他

| 指令 | 说明 |
|------|------|
| `光遇菜单` | 显示完整菜单 |

---

## 🗣️ 自然语言交互

本插件支持通过 LLM 自然语言触发，无需记忆指令：

| 你说 | 插件响应 |
|------|---------|
| "今天光遇有什么任务？" | 自动调用 `get_sky_daily_tasks` 返回任务图片 |
| "季节蜡烛在哪里？" | 自动调用 `get_sky_season_candles` 返回蜡烛位置 |
| "光遇服务器状态怎么样？" | 自动调用 `get_sky_server_status` 返回排队信息 |
| "老奶奶什么时候开饭？" | 自动调用 `get_sky_grandma_schedule` 返回时间表 |
| "现在是什么季节？" | 自动调用 `get_sky_season_progress` 返回季节进度 |
| "光翼有多少个？" | 自动调用 `get_sky_wing_count` 返回光翼统计 |

---

## 📁 插件结构

```
astrbot_plugin_sky/
├── main.py              # 插件入口：指令处理、LLM工具、定时任务
├── _conf_schema.json    # 配置 schema（WebUI配置界面）
├── metadata.yaml        # 插件元信息
├── logo.png             # 插件Logo
├── webui_preview.png    # WebUI预览图
└── data/                # 数据存储目录
    └── sky_bindings/    # 光遇ID绑定数据
```

---

## 📌 功能清单

- ✅ 每日任务图片查询
- ✅ 季节蜡烛位置查询
- ✅ 大蜡烛位置查询
- ✅ 免费魔法查询
- ✅ 季节进度查询
- ✅ 碎石信息查询
- ✅ 复刻先祖查询
- ✅ 献祭信息查询
- ✅ 老奶奶用餐时间查询
- ✅ 光遇服务器状态查询
- ✅ 光翼收集进度查询（需绑定ID）
- ✅ 全图光翼统计
- ✅ 光遇ID绑定管理
- ✅ 定时推送提醒（任务、老奶奶、献祭、碎石）
- ✅ 自然语言交互（LLM工具）
- ✅ WebUI配置界面

---

## 📝 TODO

- [ ] 光遇图鉴查询
- [ ] 先祖位置查询
- [ ] 装扮搭配推荐
- [ ] 身高测量功能
- [ ] 更多游戏数据查询

---

## 🙏 致谢

- [**Tlon-Sky**](https://gitee.com/Tloml-Starry/Tlon-Sky) — 本插件功能原型来源，感谢原作者 [**Tloml-Starry**](https://gitee.com/Tloml-Starry) 的杰出贡献
- [**AstrBot**](https://github.com/Soulter/AstrBot) — 跨平台聊天机器人框架
- 光遇数据 API 由社区提供支持

---

## 👥 贡献指南

- 🌟 Star 本项目
- 🐛 提交 Issue 报告问题
- 💡 提出新功能建议
- 🔧 提交 Pull Request 改进代码

---

## Star History

<a href="https://www.star-history.com/#AstrBot-Devs/astrbot_plugin_sky&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=AstrBot-Devs/astrbot_plugin_sky&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=AstrBot-Devs/astrbot_plugin_sky&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=AstrBot-Devs/astrbot_plugin_sky&type=date&legend=top-left" />
 </picture>
</a>

---

## 📄 许可证

MIT License

欢迎提交 Issue 和 Pull Request 来改进这个插件！

---

<div align="center">

**Made with ❤️ for Sky Players**

</div>