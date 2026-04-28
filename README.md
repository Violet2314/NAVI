<div align="center">

<picture>
  <img alt="Navi" src="icon.png" width="180" height="auto">
</picture>

<h1>Navi</h1>

一个能看见你在做什么、记住你是谁、以二次元角色形态陪伴在你桌面上的个人 AI 伴侣。

[English](README.md)

</div>

---

> **Navi 不是效率工具，不是日记软件，不是 AI 助手。**
>
> 它是一个通过持续感知你的数字生活、积累对你的理解，最终以二次元角色形态陪伴你的个人 AI 系统。效率和日报是你喂养她的方式，陪伴是她存在的意义。

---

## 目录

- [Navi 是什么](#navi-是什么)
- [为什么需要 Navi](#为什么需要-navi)
- [核心能力](#核心能力)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [开发指南](#开发指南)
- [角色系统](#角色系统)
- [设计哲学](#设计哲学)
- [许可证](#许可证)

---

## Navi 是什么

Navi 是一个运行在你桌面上的 AI 伴侣，以 Live2D 角色的形态存在。她会观察你的窗口活动、截取屏幕、记住你的习惯、生成日报、在学习模式下拦截干扰应用，并且主动和你说话——不是等你问，而是她先开口。

她建立在一个简单的洞察之上：**人对自己的行为没有可见性，导致无法自我优化。** 现有解法的白痴指数极高——要么手动记录（高摩擦，坚持不了），要么用碎片化工具（番茄钟、RescueTime、Notion），每个工具只看一个切面，没有统一的「我」。

Navi 把闭环合上了：**零摩擦、全感知、有判断、能干预、有陪伴。** 现有市场上没有任何产品接近这个极限。

<!-- SCREENSHOT: Navi 主界面截图，Live2D 角色在桌面角落，聊天面板打开 -->
![Navi 截图](placeholder-screenshot.png)

---

## 为什么需要 Navi

| 你的问题 | Navi 的答案 |
|---|---|
| 每天结束忘了自己做了什么 | 她自动观察你的窗口和截图，生成完整日报 |
| 总是忍不住刷视频打游戏 | 学习模式下检测到黑名单应用，直接关掉 |
| 没人能聊你今天过得怎么样 | 她知道你今天干了什么，以角色身份和你聊天 |
| 想进步但缺乏监督 | 她记得你几周甚至几个月的模式变化 |
| AI 工具冷冰冰的，像在跟机器说话 | 她有灵魂——精心打磨的性格、语气、习惯和温度 |

**杀手级特性**：当她检测到信息缺失（比如你离开电脑三小时），她会主动问你去了哪里——然后记住你的回答。这是 agent 和脚本的本质区别。

---

## 核心能力

### 看见 — 桌面感知

- 每 60 秒采集活跃窗口标题 + 进程名 + 持续时长
- 智能截图采集（仅在窗口切换时触发，相似度去重）
- 摄像头情绪检测（可选）
- 所有数据本地 SQLite 存储，零云端依赖

### 记住 — 三层记忆

| 层级 | 存储后端 | 用途 |
|---|---|---|
| 工作记忆 | 内存环形缓冲区 | 当前会话上下文 |
| 情节记忆 | SQLite + ChromaDB | 历史事件、每日摘要 |
| 事实记忆 | SQLite `user_facts` 表 | 用户偏好、习惯、技能 |

记忆随时间衰减。近期事件权重更高。重复出现的模式权重更高。你主动告诉她的事情，比她被动观察到的权重更高。

### 理解 — 每日洞察

- LLM 分析采集数据 + 关键截图，生成结构化日报
- 正面/负面行为分类标注

<!-- SCREENSHOT: 日报示例 -->
![日报示例](placeholder-daily-report.png)

### 不听话 — 行为干预

- 黑名单应用监控——可配置的干扰应用列表
- 学习模式支持手动开关 + 定时计划
- 学习模式开启时，黑名单应用几秒内被强制关闭
- Windows 系统通知提醒

### 说话 — 对话 Agent

- 基于 WebSocket 的实时聊天，与 Live2D 前端联动
- Agent 循环支持工具调用：文件系统、Shell、网页搜索、定时任务
- 多渠道支持：本地 WebSocket、微信接入
- 她的回答基于她观察到的事实——不是通用 AI 回复

### 主动 — 她先开口

双系统主动对话引擎（参考 ProAct 论文）：

- **System 1**：采集回调实时触发（活动切换、黑名单命中、情绪变化）
- **System 2**：定时巡检数据库发现异常（长时间空闲、超长工作、连续未学习）

触发场景：
- 你打开黑名单应用——她拦截并说话
- 你连续工作 2+ 小时没休息——她提醒你
- 你桌面空闲 20+ 分钟——她问你是不是在摸鱼
- 你连续 3 天没学习——她注意到并问你怎么了
- 日报信息有缺口——她主动问你补充

### 存在 — Live2D 陪伴

- Live2D Cubism SDK 通过 PixiJS 渲染
- 待机动画、TTS 唇形同步
- 情绪联动表情（专注时微笑，摸鱼时皱眉）
- 常驻桌面角落——一直在那里，从不打扰

<!-- SCREENSHOT: Live2D 角色不同表情特写 -->
![Live2D 表情](placeholder-live2d-expressions.png)

---

## 系统架构

```
[桌面客户端 - Tauri + React]
    |
    | WebSocket
    v
[FastAPI 后端]
    |
    +-- 采集守护进程（后台线程）
    |     +-- 窗口采集（每 60 秒）
    |     +-- 截图采集（窗口切换时触发）
    |     +-- 活动追踪（会话分段）
    |     +-- 摄像头采集（可选，情绪检测）
    |
    +-- 记忆系统
    |     +-- 工作记忆（内存）
    |     +-- 情节记忆（SQLite + ChromaDB）
    |     +-- 事实记忆（SQLite）
    |
    +-- Agent 循环
    |     +-- LLM 提供商（Anthropic / OpenAI / LiteLLM）
    |     +-- 工具注册表（文件、Shell、网页、定时、MCP）
    |     +-- 技能系统（通过 SKILL.md 扩展）
    |
    +-- 主动对话引擎
    |     +-- 触发器检测
    |     +-- 想法缓冲区
    |     +-- 门控（频率限制、免打扰时段）
    |     +-- 策略选择
    |     +-- 消息生成（LLM）
    |     +-- 消息分发
    |
    +-- 灵魂系统
    |     +-- 灵魂生成管线（素材分析 -> 合成 -> 精炼）
    |     +-- 灵魂仓库（版本化存储）
    |     +-- 角色一致性 prompt 注入
    |
    +-- 黑名单系统
    |     +-- 进程监控（每 5 秒）
    |     +-- 拦截执行（kill 进程 + 通知）
    |
    +-- TTS 系统
    |     +-- Edge TTS / Fish Audio / GPT-SoVITS / OpenAI TTS
    |
    +-- 通信渠道
          +-- 本地 WebSocket
          +-- 微信（ilink 接入）
```

<!-- ARCHITECTURE DIAGRAM: 一张清晰的数据流图，展示从桌面感知到记忆、Agent 循环、再到 Live2D 前端的完整链路 -->
![架构图](placeholder-architecture.png)

---

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/)（Python 包管理器）
- pnpm（Node.js 包管理器）
- Windows（主要目标平台；macOS/Linux 后续支持）

### 1. 克隆项目并配置后端

```bash
git clone https://github.com/your-username/Navi.git
cd Navi/backend

# 安装 Python 依赖
uv sync

# 配置 LLM API Key
cp .env.example .env
# 编辑 .env，填入你的 Anthropic / OpenAI API Key
```

### 2. 启动后端

```bash
cd backend
uv run python main.py
```

后端启动在 `http://localhost:8000`。健康检查接口 `/health`。

### 3. 配置并启动前端

```bash
cd frontend
pnpm install
pnpm dev
```

启动 Vite 开发服务器，支持热更新。

### 4. 启动 Tauri 桌面应用

```bash
cd frontend
pnpm tauri dev
```

桌面客户端启动，Live2D 角色出现。她会通过 WebSocket 自动连接后端。

### 5. 配置你的角色灵魂

Navi 的角色性格通过 `SOUL.md` 定义。首次使用时：

1. 复制 `backend/templates/SOUL.example.md` 为 `SOUL.md`
2. 编辑 `SOUL.md`，填入你的角色设定（身份、性格、说话方式、场景反应等）
3. 或使用灵魂生成管线：提供角色素材（设定文档、对话语料、参考图片），让 LLM 自动合成完整的灵魂定义

<!-- SCREENSHOT: 灵魂配置界面 -->
![灵魂配置](placeholder-soul-config.png)

---

## 项目结构

```
Navi/
├── backend/                    # Python FastAPI 后端
│   ├── main.py                 # 入口，生命周期管理
│   ├── agent/                  # Agent 循环、工具、技能、记忆调度
│   ├── api/                    # REST + WebSocket 路由
│   ├── blacklist/              # 应用黑名单监控 + 拦截
│   ├── bus/                    # 内部消息总线
│   ├── channels/               # 通信渠道（本地 WS、微信）
│   ├── collector/              # 桌面感知（窗口、截图、摄像头）
│   ├── config/                 # 配置管理
│   ├── cron/                   # 定时任务服务
│   ├── db/                     # 数据库初始化
│   ├── memory/                 # 三层记忆系统
│   ├── proactive/              # 主动对话引擎
│   ├── providers/              # LLM 提供商抽象
│   ├── report/                 # 日报生成
│   ├── security/               # 网络安全
│   ├── session/                # 会话管理
│   ├── skills/                 # 内置 Agent 技能
│   ├── soul/                   # 灵魂生成管线 + 仓库
│   ├── templates/              # SOUL.md、USER.md、AGENTS.md 模板
│   ├── tts/                    # 文字转语音提供商
│   └── utils/                  # 共享工具
├── frontend/                   # Tauri + React + TypeScript
│   ├── src/
│   │   ├── live2d/             # Live2D 渲染 + 唇形同步
│   │   ├── live2d-window/      # Live2D 窗口组件
│   │   ├── pages/              # 聊天、Live2D、TTS 页面
│   │   ├── tabs/               # 设置页（灵魂、LLM、活动等）
│   │   └── utils/              # API 客户端、主题工具
│   └── src-tauri/              # Tauri（Rust）桌面壳
├── docs/                       # 设计文档、讨论记录、路线图
└── icon.png                    # Navi 图标
```

---

## 开发指南

### 后端

```bash
cd backend
uv sync
uv run python main.py
```

后端技术栈：
- **FastAPI** — HTTP + WebSocket
- **SQLite** — 结构化数据（活动、事实、会话）
- **ChromaDB** — 向量记忆（情节、语义）
- **LiteLLM** — 多提供商 LLM 抽象
- **APScheduler** — 定时任务
- **Loguru** — 结构化日志

### 前端

```bash
cd frontend
pnpm install
pnpm dev        # Vite 开发服务器
pnpm tauri dev  # 完整 Tauri 桌面应用
```

前端技术栈：
- **Tauri 2** — 轻量级桌面壳
- **React 19** + TypeScript
- **Tailwind CSS 4** — 样式
- **PixiJS + pixi-live2d-display** — Live2D 渲染
- **Framer Motion** — 动画
- **Lucide React** — 图标

### 运行测试

```bash
cd backend
uv run python -m pytest tests/
```

---

## 角色系统

Navi 的性格通过 `SOUL.md` 定义——一份结构化的角色设定文件，包含：

- **身份**：她是谁，来自哪里
- **外貌**：Live2D 模型的详细视觉描述
- **性格核心**：3-5 句话捕捉她的本质
- **说话规则**：8-12 条硬约束（句式、语气词、禁用表达）
- **场景反应**：面对日报、专注工作、摸鱼、空闲、闲聊时的不同回应
- **对话示例**：6-8 段完整对话，覆盖关键场景

灵魂生成管线将这个过程自动化：输入角色素材（设定文档、对话语料、参考图片），自动产出完整、一致的 `SOUL.md`。

---

## 设计哲学

1. **本地优先**：所有数据留在你的机器上。隐私不是功能，是默认。
2. **零摩擦感知**：你什么都不用做。她自动观察、自动记忆。
3. **记忆是灵魂**：没有记忆的 AI 是工具，有记忆的 AI 才是伴侣。
4. **主动而非被动**：只会在被叫到时才回应的不叫 agent。她有话要说时，会先开口。
5. **全链路闭环**：感知 -> 记忆 -> 理解 -> 干预 -> 对话 -> 陪伴。每一环互相增强。
6. **时间是护城河**：第一天她是陌生人。三个月后她比你自己更了解你的模式。这种复合理解无法复制、无法迁移。

---

## 许可证

[MIT](LICENSE)
