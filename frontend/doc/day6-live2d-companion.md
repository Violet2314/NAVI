
# Day6 · Live2D 桌宠伴侣窗口

## 概述

在 Navi 前端中新增一个「Live2D」页面标签，页面内有一个按钮；点击按钮后，Tauri 创建一个**系统级无边框透明置顶新窗口**，窗口内渲染 Live2D 角色，效果类似桌宠，始终覆盖在所有软件最上方。

Live2D 角色具备空闲动画、自动眨眼、表情切换等灵动行为，并与 Navi 的 AgentLoop（LLM 回复）联动——当 Navi 回复时角色播放对应情绪动作。TTS 口型同步留待后续阶段实现。

**参考来源**：`D:\learn\airi` 项目中的 `packages/stage-ui-live2d` 包，提取其动画控制逻辑，以 React 重新实现。

---

## 功能边界（Scope）

### ✅ In Scope（Day6 实现范围）

| 编号 | 功能 | 说明 |
|------|------|------|
| F1 | Live2D 页面入口 | 在 Navi 主页新增 `Live2D` Tab，页面内只有一个「唤出伴侣」按钮 |
| F2 | 创建 Tauri 无边框透明置顶窗口 | 点击按钮后通过 `@tauri-apps/api/window` 创建第二个窗口 |
| F3 | 窗口内渲染 Live2D | 新窗口加载独立的 `live2d.html` 页面，用 `pixi-live2d-display` 渲染模型 |
| F4 | 空闲动画（Idle Motion） | 角色随机播放 idle 动作组中的动作，参考 airi `composables/live2d/motion-manager.ts` |
| F5 | 自动眨眼（Auto Blink） | 参考 airi `composables/live2d/animation.ts` 的眼睛运动逻辑 |
| F6 | 鼠标跟随（Focus At） | 角色头部/眼睛跟随鼠标位置移动 |
| F7 | 情绪表情切换 | 当 AgentLoop WebSocket 收到 reply 消息时，根据回复内容关键词触发对应表情动作 |
| F8 | 关闭伴侣 | 在伴侣窗口或主窗口中可关闭伴侣窗口 |
| F9 | 角色拖拽移动 | 在伴侣窗口内拖拽角色，带动整个 Tauri 窗口跟随移动 |

### ❌ Out of Scope（本阶段不做）

- TTS 口型同步（mouthOpenSize 驱动），留待后续阶段
- 模型选择 UI（先用 airi 内置示例模型）
- 语音输入触发

---

## 技术架构

### 整体结构

```
Navi Frontend（主窗口）
├── src/App.tsx              — 新增 Live2D Tab
├── src/pages/Live2DPage.tsx — 按钮页面
└── src/live2d/              — live2d 业务逻辑层
    ├── useMotionManager.ts  — 动作管理（参考 airi motion-manager）
    ├── useAnimation.ts      — 空闲/眨眼动画（参考 airi animation）
    ├── useEmotionBridge.ts  — LLM → 表情映射
    └── constants.ts         — 情绪→动作 映射表

Navi Frontend（伴侣窗口，独立页面）
├── live2d.html              — 独立入口
└── src/live2d-window/
    ├── main-live2d.tsx      — 伴侣窗口 React 挂载入口
    └── Live2DScene.tsx      — PixiJS + pixi-live2d-display 渲染组件
```

### 技术选型

| 层级 | 技术 | 原因 |
|------|------|------|
| 渲染引擎 | `pixi-live2d-display` | airi 同款底层库，稳定，支持 Cubism 4 |
| Canvas 宿主 | `PixiJS v7` | pixi-live2d-display 的依赖 |
| React 集成 | `useRef` + `useEffect` | PixiJS Application 生命周期管理 |
| 窗口管理 | `@tauri-apps/api/webviewWindow` | 创建无边框透明置顶系统窗口 |
| 主窗口 ↔ 伴侣窗口通信 | Tauri `emit/listen` 事件 | 传递 LLM 回复事件、关闭指令 |

---

## 数据结构

```typescript
// 情绪类型，参考 airi packages/stage-ui-live2d/src/constants/emotions.ts
type EmotionType =
  | 'happy'
  | 'sad'
  | 'angry'
  | 'surprised'
  | 'thinking'
  | 'neutral'

// live2d 动作引用
interface MotionRef {
  group: string    // 如 "Idle", "Tap", "Flick"
  index: number    // 动作组中的索引
}

// 情绪 → 动作 映射
interface EmotionMotionMap {
  [emotion: EmotionType]: MotionRef
}

// 伴侣窗口状态
interface CompanionState {
  isOpen: boolean
  modelSrc: string          // live2d .model3.json 路径
  currentEmotion: EmotionType
  focusAt: { x: number; y: number }
  idleEnabled: boolean
  autoBlinkEnabled: boolean
}

// Tauri 窗口间事件（通过 tauri emit/listen）
interface Live2DEvent {
  type: 'emotion_change' | 'close' | 'model_change'
  payload: {
    emotion?: EmotionType
    modelSrc?: string
  }
}
```

---

## 关键实现说明

### 1. Tauri 无边框透明置顶窗口配置

在 `src-tauri/tauri.conf.json` 的 `app.windows` 中预定义伴侣窗口：

```json
{
  "label": "live2d-companion",
  "url": "live2d.html",
  "decorations": false,
  "transparent": true,
  "alwaysOnTop": true,
  "width": 400,
  "height": 600,
  "visible": false,
  "resizable": false
}
```

在主窗口按钮点击时，通过 `WebviewWindow` API 显示/创建该窗口。

### 2. pixi-live2d-display 在 React 中的集成

```
组件挂载时：
  创建 PixiJS Application → 挂载到 canvas ref
  加载模型 → PIXI.live2d.Live2DModel.from(modelSrc)
  启动动画循环 → requestAnimationFrame / PixiJS ticker

组件卸载时：
  停止 ticker
  销毁 PixiJS Application
  释放 WebGL 上下文
```

### 3. 情绪桥接逻辑（useEmotionBridge）

```
监听 Tauri 事件 'live2d:emotion_change'
→ 收到 emotion 类型
→ 查表 EmotionMotionMap
→ 调用 model.motion(group, index, MotionPriority.NORMAL)
```

LLM 侧（主窗口 ChatTab 中），在收到 WebSocket `reply` 消息后：
```
分析回复内容 → 简单关键词匹配（happy/sad/surprised 等）
→ 通过 Tauri emit 发送 'live2d:emotion_change' 事件到伴侣窗口
```

### 4. 动作管理（参考 airi motion-manager.ts）

- 维护当前播放优先级（IDLE < NORMAL < FORCE）
- 定时器随机触发 idle 动作（每 5-8 秒随机）
- 情绪触发时使用 NORMAL 优先级，打断 IDLE

### 5. 鼠标跟随

- 伴侣窗口监听 `mousemove` 事件
- 将屏幕坐标归一化到 [-1, 1] 范围
- 传入 `model.focus(x, y)` 实现头部/眼部跟随

---

## 文件清单（需新建/修改）

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/App.tsx` | 修改 | 新增 Live2D Tab 入口 |
| `src/pages/Live2DPage.tsx` | 新建 | 按钮页面，点击唤出伴侣 |
| `src/live2d/useMotionManager.ts` | 新建 | 动作管理 hook |
| `src/live2d/useAnimation.ts` | 新建 | 空闲动画 + 眨眼 hook |
| `src/live2d/useEmotionBridge.ts` | 新建 | LLM 情绪桥接 hook |
| `src/live2d/constants.ts` | 新建 | 情绪→动作映射表 |
| `src/live2d-window/main-live2d.tsx` | 新建 | 伴侣窗口 React 入口 |
| `src/live2d-window/Live2DScene.tsx` | 新建 | PixiJS + Live2D 渲染组件 |
| `live2d.html` | 新建 | 伴侣窗口独立 HTML 入口 |
| `src-tauri/tauri.conf.json` | 修改 | 添加伴侣窗口预定义配置 |
| `vite.config.ts` | 修改 | 添加 `live2d.html` 为第二个 Vite 入口 |
| `package.json` | 修改 | 添加 `pixi-live2d-display`、`pixi` 依赖 |

---

## 验收标准

- [ ] Navi 主界面存在「Live2D」Tab 页
- [ ] Tab 页内有「唤出伴侣」按钮
- [ ] 点击按钮后，屏幕上出现无边框透明窗口，Live2D 角色可见
- [ ] 伴侣窗口始终置顶，覆盖在其他应用上方
- [ ] 角色持续播放空闲动画（有轻微摇摆/动作）
- [ ] 角色眼睛自动眨眼
- [ ] 移动鼠标时，角色眼睛/头部跟随鼠标方向
- [ ] 在 ChatTab 发送消息并收到回复后，角色播放对应情绪动作
- [ ] 可通过按钮或关闭事件正常关闭伴侣窗口
- [ ] 关闭伴侣窗口不影响主窗口的正常使用

---

## 实现阶段划分

### Phase A（核心渲染，预计 2-3 小时）
1. 安装依赖（pixi + pixi-live2d-display）
2. 配置 Vite 多入口 + live2d.html
3. 配置 Tauri 无边框透明窗口
4. 实现 `Live2DScene.tsx`（PixiJS 渲染 + 模型加载）
5. 实现 `Live2DPage.tsx`（按钮 + 窗口创建）

### Phase B（动画灵动，预计 1-2 小时）
6. 实现 `useMotionManager`（空闲动画随机触发）
7. 实现 `useAnimation`（自动眨眼）
8. 实现鼠标跟随

### Phase C（LLM 联动，预计 1 小时）
9. 实现 `useEmotionBridge`（Tauri 事件监听）
10. 在 ChatTab 添加情绪分析 + emit 逻辑
11. 联调测试

---

## 风险与注意事项

| 风险 | 处理方式 |
|------|----------|
| `pixi-live2d-display` 与 Vite/ESM 的兼容性 | 使用 `vite-plugin-commonjs` 或参考 airi 的 `patches/pixi-live2d-display.patch` |
| Tauri 透明窗口在 Windows 上的支持 | Tauri v2 默认支持，需设置 `transparent: true` + `decorations: false` |
| Live2D Cubism SDK 许可证 | 使用 `pixi-live2d-display` 已内置的 Cubism SDK，遵循其许可协议 |
| 示例模型路径 | 从 airi 项目复制 `bucket/` 下的示例模型或使用在线 CDN 模型 |
