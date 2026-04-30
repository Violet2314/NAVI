import { useEffect, useRef, useCallback } from 'react'
import * as PIXI from 'pixi.js'
// cubism4 包同时支持 Cubism 3 和 4（Version:3 的 model3.json 用的是同一个 live2dcubismcore.min.js）
import { Live2DModel, MotionPriority } from 'pixi-live2d-display/cubism4'
import type { EmotionType } from '../live2d/constants'
import { EMOTION_EXPRESSION, EMOTION_MOTION_GROUP, buildEmotionMotionMap } from '../live2d/constants'

Live2DModel.registerTicker(PIXI.Ticker)

// ─── airi: eye-motions.ts randomSaccadeInterval ──────────────────────────────
const EYE_SACCADE_INT_STEP = 400
const EYE_SACCADE_INT_P: [number, number][] = [
  [0.075, 800], [0.110, 0], [0.125, 0], [0.140, 0], [0.125, 0],
  [0.050, 0],   [0.040, 0], [0.030, 0], [0.020, 0], [1.000, 0],
]
for (let i = 1; i < EYE_SACCADE_INT_P.length; i++) {
  EYE_SACCADE_INT_P[i][0] += EYE_SACCADE_INT_P[i - 1][0]
  EYE_SACCADE_INT_P[i][1]  = EYE_SACCADE_INT_P[i - 1][1] + EYE_SACCADE_INT_STEP
}
function randomSaccadeInterval(): number {
  const r = Math.random()
  for (const [p, t] of EYE_SACCADE_INT_P) {
    if (r <= p) return t + Math.random() * EYE_SACCADE_INT_STEP
  }
  return EYE_SACCADE_INT_P[EYE_SACCADE_INT_P.length - 1][1] + Math.random() * EYE_SACCADE_INT_STEP
}

// ─── airi: useLive2DIdleEyeFocus (animation.ts) ──────────────────────────────
// 注意：当外部有鼠标跟踪时（focusTicker），会与此插件产生竞争
// 修复：移除手动 lerp 叠加，只用 focusController，避免抖动
function createIdleEyeFocus() {
  let nextSaccadeAfter = Date.now() + 2000  // 首次延迟 2 秒
  let focusTarget: [number, number] = [0, 0]
  let lastSaccadeAt = Date.now()

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function update(internalModel: any, now: number) {
    // 防止时间戳异常（如系统休眠、切换窗口导致的跳跃）
    if (now < lastSaccadeAt - 1000 || now - lastSaccadeAt > 60000) {
      lastSaccadeAt = now
      nextSaccadeAfter = now + randomSaccadeInterval()
      return  // 跳过这一帧，避免抖动
    }
    
    if (now >= nextSaccadeAfter) {
      // 生成新的随机目标，幅度减小避免抖动
      focusTarget = [
        (Math.random() * 2 - 1) * 0.3,  // x: ±0.3（更温和）
        (Math.random() * 1.4 - 0.7) * 0.3  // y
      ]
      lastSaccadeAt = now
      nextSaccadeAfter = now + randomSaccadeInterval()
      internalModel.focusController?.focus(focusTarget[0], focusTarget[1], false)
    }
    // 让 focusController 平滑过渡，限制最大 delta 避免抽搐
    const delta = Math.min(now - lastSaccadeAt, 50)
    internalModel.focusController?.update(delta)
    // 不再手动叠加 lerp 设置 ParamEyeBallX/Y，让 focusController 和 model.focus() 统一处理
  }
  return { update }
}

// ─── airi: useMotionUpdatePluginAutoEyeBlink ──────────────────────────────────
function createAutoEyeBlinkPlugin() {
  const s = { phase: 'idle' as 'idle' | 'closing' | 'opening', progress: 0, startLeft: 1, startRight: 1, delayMs: 0 }
  const CLOSE = 200, OPEN = 200, MIN = 3000, MAX = 8000
  const c01 = (v: number) => Math.min(1, Math.max(0, v))
  const eOut = (t: number) => 1 - (1 - t) * (1 - t)
  const eIn  = (t: number) => t * t
  function reset() { s.phase = 'idle'; s.progress = 0; s.delayMs = MIN + Math.random() * (MAX - MIN) }
  reset()

  function forcedBlink(dt: number, bL: number, bR: number) {
    if (s.phase === 'idle') {
      s.delayMs = Math.max(0, s.delayMs - dt)
      if (s.delayMs === 0) { s.phase = 'closing'; s.progress = 0; s.startLeft = bL; s.startRight = bR }
      return { l: bL, r: bR }
    }
    if (s.phase === 'closing') {
      s.progress = Math.min(1, s.progress + dt / CLOSE)
      const e = eOut(s.progress)
      const l = c01(s.startLeft * (1 - e)), r = c01(s.startRight * (1 - e))
      if (s.progress >= 1) { s.phase = 'opening'; s.progress = 0 }
      return { l, r }
    }
    s.progress = Math.min(1, s.progress + dt / OPEN)
    const e = eIn(s.progress)
    const l = c01(s.startLeft * e), r = c01(s.startRight * e)
    if (s.progress >= 1) reset()
    return { l, r }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function update(internalModel: any, coreModel: any, isIdle: boolean, timeDelta: number) {
    if (!isIdle) return
    const bL = c01(coreModel.getParameterValueById('ParamEyeLOpen') as number)
    const bR = c01(coreModel.getParameterValueById('ParamEyeROpen') as number)

    if (!internalModel.eyeBlink) {
      // force mode：timeDelta 来自 motionManager.update，单位是秒
      // 限制最大 delta 避免系统休眠/切换窗口后的抽搐
      const raw = Math.max(timeDelta ?? 0, 0)
      const dtSec = Math.min(raw, 0.1)  // 最大 100ms
      const dt = dtSec * 1000  // 转为毫秒
      const { l, r } = forcedBlink(dt, bL, bR)
      coreModel.setParameterValueById('ParamEyeLOpen', l)
      coreModel.setParameterValueById('ParamEyeROpen', r)
      return
    }
    // SDK eyeBlink mode — airi: because we hook motionManager.update, eyeBlink won't auto-run
    internalModel.eyeBlink.updateParameters(coreModel, timeDelta / 1000)
    const bl = c01(coreModel.getParameterValueById('ParamEyeLOpen') as number)
    const br = c01(coreModel.getParameterValueById('ParamEyeROpen') as number)
    coreModel.setParameterValueById('ParamEyeLOpen', c01(bl * bL))
    coreModel.setParameterValueById('ParamEyeROpen', c01(br * bR))
  }
  return { update }
}

// ─── 自动侦测 idleGroup（兼容游戏模型没有标准 "Idle" 组名）────────────────────
function detectIdleGroup(definitions: Record<string, unknown>): string {
  const keys = Object.keys(definitions)
  if (!keys.length) return 'Idle'
  const exact = keys.find(k => k.toLowerCase() === 'idle')
  if (exact) return exact
  const fuzzy = keys.find(k => k.toLowerCase().includes('idle'))
  if (fuzzy) return fuzzy
  if (keys.includes('')) return ''
  return keys[0]
}

// ─── Props ────────────────────────────────────────────────────────────────────
interface Live2DSceneProps {
  modelSrc: string
  pendingEmotion?: EmotionType | null
  onEmotionHandled?: () => void
  modelScale?: number
  /** 正数往上（露出更多），负数往下 */
  offsetY?: number
  /** 口型同步：外部传入的实时张嘴程度 (0-1) */
  mouthOpenValue?: number
  /** 全局鼠标位置（从主窗口转发），归一化 [-1,1] */
  globalMousePos?: { x: number; y: number } | null
}

export function Live2DScene({
  modelSrc,
  pendingEmotion,
  onEmotionHandled,
  modelScale = 1.0,
  offsetY = 0,
  mouthOpenValue = 0,
  globalMousePos: _globalMousePos = null,
}: Live2DSceneProps) {
  const canvasRef        = useRef<HTMLCanvasElement>(null)
  const appRef           = useRef<PIXI.Application | null>(null)
  const modelRef         = useRef<Live2DModel | null>(null)
  const baseScaleRef     = useRef(1)
  const baseYRef         = useRef(0)
  const mousePosRef      = useRef({ x: 0, y: 0 })
  const smoothMouseRef   = useRef({ x: 0, y: 0 })  // 平滑后的鼠标位置
  const expressionMapRef = useRef<Record<string, EmotionType>>({})
  /** 模型加载完成后由 buildEmotionMotionMap 填充，fuzzy 匹配的最终映射 */
  const emotionMotionMapRef = useRef<Record<EmotionType, string>>(EMOTION_MOTION_GROUP)
  /** 模型初始加载时的参数快照，用于情绪恢复 */
  const initialParamsRef = useRef<Float32Array | null>(null)
  /** 口型同步值 ref（供 motionManager.update 闭包读取） */
  const mouthOpenRef = useRef(0)
  mouthOpenRef.current = mouthOpenValue

  // ── 情绪触发（使用 fuzzy motion map）+ 30秒后自动恢复 ───────────────────
  const emotionResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (!pendingEmotion || !modelRef.current) return
    const model = modelRef.current
    const motionGroup = emotionMotionMapRef.current[pendingEmotion]
    console.log(`[Live2D 情绪触发] emotion=${pendingEmotion} → motionGroup="${motionGroup}"`)
    model.motion(motionGroup, undefined, MotionPriority.FORCE)

    const stdExpr = EMOTION_EXPRESSION[pendingEmotion]
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ma = model as any
    if (stdExpr) {
      console.log(`[Live2D 情绪触发] expression="${stdExpr}"`)
      ma.expression?.(stdExpr)
    }
    const rte = Object.entries(expressionMapRef.current).find(([, em]) => em === pendingEmotion)?.[0]
    if (rte && rte !== stdExpr) {
      console.log(`[Live2D 情绪触发] runtime expression="${rte}"`)
      ma.expression?.(rte)
    }
    onEmotionHandled?.()

    // 30 秒后恢复到模型默认加载状态（清除表情覆盖 + 恢复 idle 动画）
    // 注意：不能放在 effect cleanup 里清 timer，因为 onEmotionHandled 会将
    // pendingEmotion 置 null 导致 effect 重新运行 → cleanup 立即清掉定时器
    if (emotionResetTimerRef.current) clearTimeout(emotionResetTimerRef.current)
    if (pendingEmotion !== "neutral" && pendingEmotion !== "normal") {
      emotionResetTimerRef.current = setTimeout(() => {
        emotionResetTimerRef.current = null
        if (!modelRef.current) return
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const internal = (modelRef.current as any).internalModel
        if (!internal) return
        console.log('[Live2D 情绪恢复] 30s 到期，重置到默认加载状态')

        // 1. 清除 expression manager 状态
        try {
          const em = internal.motionManager?.expressionManager ?? internal.expressionManager
          if (em) {
            if (em.resetExpression) em.resetExpression()
            em._currentExpression = null
            em.expressionIndex = -1
          }
        } catch { /* 静默 */ }

        // 2. 从初始快照平滑过渡恢复参数（1.5秒渐变）
        try {
          const cm = internal.coreModel
          const snapshot = initialParamsRef.current
          if (snapshot && cm && typeof cm.setParameterValueByIndex === 'function') {
            // 记录当前参数值作为起点
            const from = new Float32Array(snapshot.length)
            for (let i = 0; i < snapshot.length; i++) {
              from[i] = cm.getParameterValueByIndex(i)
            }
            const DURATION = 1500  // 过渡时长 ms
            const startTime = performance.now()
            const animate = (now: number) => {
              const elapsed = now - startTime
              const t = Math.min(elapsed / DURATION, 1)
              // easeOutCubic: 先快后慢，更自然
              const ease = 1 - Math.pow(1 - t, 3)
              for (let i = 0; i < snapshot.length; i++) {
                const v = from[i] + (snapshot[i] - from[i]) * ease
                cm.setParameterValueByIndex(i, v)
              }
              if (t < 1) {
                requestAnimationFrame(animate)
              } else {
                console.log(`[Live2D 情绪恢复] 参数平滑过渡完成 ✅ (${snapshot.length} 个参数, ${DURATION}ms)`)
              }
            }
            requestAnimationFrame(animate)
            console.log(`[Live2D 情绪恢复] 开始平滑过渡... (${snapshot.length} 个参数)`)
          } else if (snapshot && cm?._model?._parameterValues) {
            cm._model._parameterValues.set(snapshot)
            console.log('[Live2D 情绪恢复] 参数已从快照恢复 ✅ (直写 fallback)')
          } else {
            console.warn('[Live2D 情绪恢复] 无初始快照或无法写入参数')
          }
        } catch (e) { console.warn('[Live2D 情绪恢复] 参数恢复失败:', e) }

        // 3. 停止所有强制动作，idle 会自动恢复
        try {
          const mm = internal.motionManager
          if (mm) {
            if (mm.stopAllMotions) mm.stopAllMotions()
            else if (mm.reset) mm.reset()
            console.log('[Live2D 情绪恢复] motion 已重置 ✅')
          }
        } catch (e) { console.warn('[Live2D 情绪恢复] motion 重置失败:', e) }
      }, 30_000)
    }
  }, [pendingEmotion, onEmotionHandled])

  // 组件卸载时清理定时器（独立 effect，不会被 pendingEmotion 变化触发）
  useEffect(() => {
    return () => {
      if (emotionResetTimerRef.current) clearTimeout(emotionResetTimerRef.current)
    }
  }, [])

  // ── modelScale / offsetY 实时同步（不重建 app）───────────────────────────
  useEffect(() => {
    const model = modelRef.current
    const canvas = canvasRef.current
    if (!model || !canvas) return
    const H = canvas.clientHeight || 600
    const W = canvas.clientWidth  || 380
    model.scale.set(baseScaleRef.current * modelScale)
    model.x = W / 2
    model.y = baseYRef.current - offsetY * H
  }, [modelScale, offsetY])

  // ── airi 式全局光标跟踪 —— 获取屏幕绝对坐标，转为窗口相对像素坐标 ─────
  useEffect(() => {
    let alive = true
    let invokeFn: ((cmd: string) => Promise<unknown>) | null = null
    // 窗口位置（用于将屏幕坐标转为窗口相对坐标）
    let winX = 0
    let winY = 0

    async function init() {
      try {
        const core = await import('@tauri-apps/api/core' as any)
        invokeFn = core.invoke
        // 获取当前窗口位置
        try {
          const tauriWindow = await import('@tauri-apps/api/window' as any)
          const win = tauriWindow.getCurrentWindow()
          const pos = await win.outerPosition()
          winX = pos.x
          winY = pos.y
          // 监听窗口移动，实时更新窗口位置
          win.onMoved?.((event: any) => {
            winX = event.payload.x
            winY = event.payload.y
          })
        } catch { /* ignore */ }
        console.log('[Live2D 鼠标] ✅ 全局光标跟踪已启动 (airi 窗口相对坐标模式)')
      } catch {
        console.warn('[Live2D 鼠标] ⚠️ Tauri invoke 不可用，降级为窗口内跟踪')
      }
    }

    // 50ms 轮询全局光标位置 (~20fps)，转为窗口相对像素坐标
    const timer = setInterval(async () => {
      if (!alive || !invokeFn) return
      try {
        const [cx, cy] = (await invokeFn('get_cursor_position')) as [number, number]
        // airi 式：屏幕坐标 → 窗口相对坐标（像素）
        mousePosRef.current = { x: cx - winX, y: cy - winY }
      } catch { /* ignore */ }
    }, 50)

    init()

    // fallback：窗口内鼠标（非 Tauri 环境），直接用像素坐标
    const localFn = (e: MouseEvent) => {
      if (invokeFn) return // 全局跟踪已接管
      mousePosRef.current = { x: e.clientX, y: e.clientY }
    }
    window.addEventListener('mousemove', localFn)

    return () => {
      alive = false
      clearInterval(timer)
      window.removeEventListener('mousemove', localFn)
    }
  }, [])

  // ── 拖拽窗口 ──────────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const fn = async (e: MouseEvent) => {
      if (e.button !== 0) return
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const w = await import('@tauri-apps/api/window' as any)
        await w.getCurrentWindow().startDragging()
      } catch { /* 非 Tauri 环境忽略 */ }
    }
    canvas.addEventListener('mousedown', fn)
    return () => canvas.removeEventListener('mousedown', fn)
  }, [])

  // 插件工厂，每次 modelSrc 变化时重新创建（保证状态独立）
  const createPlugins = useCallback(() => ({
    idleEyeFocus: createIdleEyeFocus(),
    autoEyeBlink: createAutoEyeBlinkPlugin(),
  }), [])

  // ── 主 effect：初始化 PixiJS + 加载模型 + hook motionManager.update ─────
  useEffect(() => {
    if (!canvasRef.current) return
    const canvas = canvasRef.current
    const W = canvas.clientWidth  || 380
    const H = canvas.clientHeight || 600

    // 用于 cleanup 恢复 motionManager.update 原始函数
    let motionManagerRef: any = null
    let originalUpdateRef: any = null

    // WebGL1 fallback（透明窗口 WebGL2 maxIfStatements=0 问题）
    let app: PIXI.Application
    try {
      app = new PIXI.Application({
        view: canvas, width: W, height: H, backgroundAlpha: 0,
        antialias: true, resolution: window.devicePixelRatio || 1, autoDensity: true,
        powerPreference: 'high-performance',
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        context: canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false }) as any,
      })
    } catch {
      app = new PIXI.Application({ view: canvas, width: W, height: H, backgroundAlpha: 0, forceCanvas: true })
    }
    appRef.current = app

    // resize handler（定义在 loadModel 外，方便 cleanup）
    let cleanupResize = () => {}

    async function loadModel() {
      console.log('[Live2DScene] 加载模型:', modelSrc)
      try {
        const live2dModel = await Live2DModel.from(modelSrc, { autoInteract: false })
        if (!appRef.current) return

        modelRef.current = live2dModel
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        app.stage.addChild(live2dModel as unknown as PIXI.DisplayObject)

        // airi: computeScaleAndPosition (offsetFactor=2.2)
        const initW = live2dModel.width
        const initH = live2dModel.height
        const scale = Math.min(H * 0.95 / initH * 2.2, W * 0.95 / initW * 2.2)
        baseScaleRef.current = scale
        baseYRef.current     = H
        live2dModel.anchor.set(0.5, 0.5)
        live2dModel.scale.set(scale * modelScale)
        live2dModel.x = W / 2
        live2dModel.y = H - offsetY * H

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const internalModel = live2dModel.internalModel as any
        const motionManager = internalModel.motionManager
        const coreModel     = internalModel.coreModel
        const definitions: Record<string, unknown> = motionManager?.definitions ?? {}
        const idleGroup = detectIdleGroup(definitions)
        const allGroups = Object.keys(definitions)
        console.log('[Live2DScene] idleGroup:', idleGroup, '| motionManager.groups:', motionManager?.groups)
        console.log('[Live2DScene] 所有 motion 组:', allGroups)

        // ── 构建 fuzzy emotion → motion 映射 ───────────────────────────────
        emotionMotionMapRef.current = buildEmotionMotionMap(allGroups, idleGroup)
        console.log('[Live2DScene] emotionMotionMap:', emotionMotionMapRef.current)

        // airi: 扫描 availableMotions
        const availableMotions = Object.entries(definitions).flatMap(([motionName, defs]) =>
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          ((defs as any[]) ?? []).map((m: any, idx: number) => ({ motionName, motionIndex: idx, fileName: m.File }))
        )
        console.log('[Live2DScene] availableMotions:', availableMotions)

        // expression 运行时映射
        const EXPR_KW: [string, EmotionType][] = [
          ['angry','angry'], ['sad','sad'], ['shock','surprised'], ['surprise','surprised'],
          ['happy','happy'], ['shy','awkward'], ['awkward','awkward'], ['sweat','awkward'],
          ['think','think'], ['question','question'], ['curious','curious'],
          ['excet','happy'], ['celeb','happy'], ['bang','surprised'],
        ]
        expressionMapRef.current = {}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const exprDefs: any[] = internalModel.expressionManager?.definitions ?? []
        exprDefs.forEach((expr: any) => {
          const name: string = expr.Name ?? ''
          for (const [kw, em] of EXPR_KW) {
            if (name.toLowerCase().includes(kw)) { expressionMapRef.current[name] = em; break }
          }
        })

        // airi: 去掉 idle 动作的 ParamEyeBallX/Y 曲线，让 IdleFocus plugin 接管
        // airi 用 motionManager.groups.idle（内部 index），我们同时支持 idleGroup（字符串）
        const idleGroupIdx = motionManager?.groups?.[idleGroup] ?? motionManager?.groups?.idle
        const idleMotions = (motionManager?.motionGroups?.[idleGroupIdx] ?? []) as any[]
        idleMotions.forEach((motion: any) => {
          motion._motionData?.curves?.forEach((curve: any) => {
            if (curve.id === 'ParamEyeBallX' || curve.id === 'ParamEyeBallY') {
              curve.id = `_${curve.id}`
            }
          })
        })

        // airi: hit → tap_body
        live2dModel.on('hit', (hitAreas: string[]) => {
          if (hitAreas.includes('body'))
            live2dModel.motion(idleGroup, undefined, MotionPriority.NORMAL)
        })

        // ── airi 核心：hook motionManager.update + 插件管道 ──────────────────
        const { idleEyeFocus, autoEyeBlink } = createPlugins()
        let lastUpdateTime = 0

        const originalUpdate = motionManager.update.bind(motionManager) as
          (model: unknown, now: number) => boolean

        // 保存引用供 cleanup 恢复
        motionManagerRef = motionManager
        originalUpdateRef = originalUpdate

        motionManager.update = function (model: unknown, now: number) {
          const timeDelta = lastUpdateTime ? now - lastUpdateTime : 0

          // airi: isIdleMotion 判断（兼容 pixi-live2d-display 内部 state）
          const currentGroup = motionManager.state?.currentGroup
          const isIdle = !currentGroup
            || currentGroup === motionManager.groups?.idle
            || currentGroup === idleGroup

          // ── [original] pixi-live2d-display 的 motion 播放逻辑
          const result = originalUpdate(model, now)

          // ── [post] IdleFocus（airi: useMotionUpdatePluginIdleFocus）
          if (isIdle) {
            idleEyeFocus.update(internalModel, now)
          }

          // ── [post] AutoEyeBlink（airi: useMotionUpdatePluginAutoEyeBlink）
          autoEyeBlink.update(internalModel, coreModel, isIdle, timeDelta)

          // ── [post] LipSync 口型同步（wLipSync MFCC 元音分析驱动）──────────
          // mouthOpenRef.current 由外部 AudioWorklet 实时更新
          const mouthVal = mouthOpenRef.current
          if (mouthVal > 0.01) {
            coreModel.setParameterValueById('ParamMouthOpenY', mouthVal)
          }

          // ⚠️ 鼠标跟随已移到 hook 外面（通过 model.focus()），避免和 idleEyeFocus 竞态

          lastUpdateTime = now
          return result
        }

        // ── airi 式鼠标跟随：在 motionManager.update 外面用 model.focus() ──
        // 这样不会和 idleEyeFocus plugin 在同一帧内竞态
        // airi: watch(focusAt, (value) => { model.focus(value.x, value.y) })
        // React 版：用 PIXI.Ticker 每帧读取 mousePosRef，调用 model.focus()
        // 添加线性插值 (lerp) 让眼睛跟踪更柔和
        const SMOOTH_FACTOR = 0.08  // 越小越柔和，0.08 约 12 帧平滑
        const focusTicker = () => {
          const target = mousePosRef.current
          const smooth = smoothMouseRef.current
          // lerp 插值：当前位置逐渐靠近目标位置
          smooth.x += (target.x - smooth.x) * SMOOTH_FACTOR
          smooth.y += (target.y - smooth.y) * SMOOTH_FACTOR
          // pixi-live2d-display 的 model.focus(x, y) 接受窗口像素坐标
          // 它内部会转换为 focusController 需要的归一化值
          live2dModel.focus(smooth.x, smooth.y)
        }
        app.ticker.add(focusTicker)

        // 播放待机动作（airi: live2dIdleAnimationEnabled 为 true 时）
        // 注意：空字符串 "" 是合法的 motion 组名（游戏模型常见），直接传即可
        // pixi-live2d-display 内部会用 motionManager.groups[idleGroup] 找到正确的 index
        if (idleGroup !== '') {
          live2dModel.motion(idleGroup, undefined, MotionPriority.IDLE)
        } else {
          // 空字符串组：直接用 motionManager 的底层 API 触发第一个 motion
          const groupIdx = motionManager?.groups?.[''] ?? 0
          motionManager?.startMotion?.(groupIdx, 0, MotionPriority.IDLE)
        }

        // resize
        const handleResize = () => {
          if (!appRef.current || !modelRef.current) return
          const W = canvas.clientWidth, H = canvas.clientHeight
          appRef.current.renderer.resize(W, H)
          const iW = live2dModel.width  / live2dModel.scale.x
          const iH = live2dModel.height / live2dModel.scale.y
          const newBase = Math.min(W * 0.95 / iW, H * 0.95 / iH) * 2.2
          baseScaleRef.current = newBase
          baseYRef.current     = H
          live2dModel.scale.set(newBase * modelScale)
          live2dModel.x = W / 2
          live2dModel.y = H - offsetY * H
        }
        window.addEventListener('resize', handleResize)
        cleanupResize = () => {
          window.removeEventListener('resize', handleResize)
          app.ticker.remove(focusTicker)
        }

        // 保存初始参数快照（用于情绪恢复时还原到加载时的状态）
        try {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const cm = (live2dModel as any).internalModel?.coreModel
          if (cm && typeof cm.getParameterCount === 'function') {
            const count = cm.getParameterCount()
            const snapshot = new Float32Array(count)
            for (let i = 0; i < count; i++) {
              snapshot[i] = cm.getParameterValueByIndex(i)
            }
            initialParamsRef.current = snapshot
            console.log(`[Live2DScene] 📸 初始参数快照已保存 (${count} 个参数)`)
          } else if (cm?._model?._parameterValues) {
            initialParamsRef.current = new Float32Array(cm._model._parameterValues)
            console.log(`[Live2DScene] 📸 初始参数快照已保存 (${initialParamsRef.current.length} 个参数)`)
          } else {
            console.warn('[Live2DScene] ⚠️ 无法获取参数快照，coreModel:', cm?.constructor?.name)
          }
        } catch (e) { console.warn('[Live2DScene] 参数快照保存失败:', e) }

        console.log('[Live2DScene] ✅ 模型加载完成')
      } catch (err) {
        console.error('[Live2DScene] 模型加载失败:', err)
      }
    }

    loadModel()

    return () => {
      cleanupResize()
      // ── 恢复 motionManager.update 原始函数，防止 hook 叠加导致抽搐 ──
      if (motionManagerRef && originalUpdateRef) {
        console.log('[Live2DScene] 🧹 恢复 motionManager.update 原始函数')
        motionManagerRef.update = originalUpdateRef
        motionManagerRef = null
        originalUpdateRef = null
      }
      if (appRef.current) {
        appRef.current.ticker.stop()
        appRef.current.destroy(false)
        appRef.current = null
      }
      modelRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelSrc, createPlugins])

  return (
    <canvas
      ref={canvasRef}
      style={{ width: '100%', height: '100%', display: 'block', cursor: 'grab', touchAction: 'none' }}
    />
  )
}