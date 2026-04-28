import { StrictMode, useState, useEffect, useCallback, useRef } from 'react'
import { createRoot } from 'react-dom/client'
import { Live2DScene } from './Live2DScene'
import type { EmotionType } from '../live2d/constants'
import { inferEmotion } from '../live2d/constants'
import { createLipSyncEngine, type LipSyncEngine } from '../live2d/lip-sync'

const MODEL_STORAGE_KEY = 'navi:live2d:selected-model-src'
const CONFIG_STORAGE_KEY = 'navi:live2d:config'
const DEFAULT_MODEL = '/live2d/hiyori_pro_zh/runtime/hiyori_pro_t11.model3.json'

interface CompanionConfig {
  modelScale: number
  offsetY: number
  winW: number
  winH: number
}

const DEFAULT_CONFIG: CompanionConfig = { modelScale: 1.0, offsetY: 0, winW: 380, winH: 600 }

function loadConfig(): CompanionConfig {
  try {
    const s = localStorage.getItem(CONFIG_STORAGE_KEY)
    if (s) return { ...DEFAULT_CONFIG, ...JSON.parse(s) }
  } catch {}
  return DEFAULT_CONFIG
}

function CompanionApp() {
  const [pendingEmotion, setPendingEmotion] = useState<EmotionType | null>(null)
  const [modelSrc, setModelSrc] = useState<string>(
    () => localStorage.getItem(MODEL_STORAGE_KEY) ?? DEFAULT_MODEL
  )
  const [config, setConfig] = useState<CompanionConfig>(loadConfig)
  const [bubble, setBubble] = useState<string | null>(null)
  const [mouthOpen, setMouthOpen] = useState(0)
  const bubbleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── LipSync 引擎 refs ─────────────────────────────────────────────────
  const audioCtxRef = useRef<AudioContext | null>(null)
  const lipSyncRef = useRef<LipSyncEngine | null>(null)
  const lipSyncReadyRef = useRef(false)
  const rafIdRef = useRef(0)

  const handleEmotionHandled = useCallback(() => setPendingEmotion(null), [])

  // 显示文字气泡（自动消失）
  const showBubble = useCallback((text: string, durationMs = 5000) => {
    setBubble(text)
    if (bubbleTimerRef.current) clearTimeout(bubbleTimerRef.current)
    bubbleTimerRef.current = setTimeout(() => setBubble(null), durationMs)
  }, [])

  // ── 初始化 AudioContext + LipSync Engine ──────────────────────────────
  useEffect(() => {
    let alive = true

    async function initLipSync() {
      try {
        const ctx = new AudioContext()
        audioCtxRef.current = ctx

        // 确保 AudioContext 处于 running 状态（用户交互后才能启动）
        if (ctx.state === 'suspended') {
          const resume = () => {
            ctx.resume().catch(() => {})
            window.removeEventListener('click', resume)
            window.removeEventListener('keydown', resume)
          }
          window.addEventListener('click', resume)
          window.addEventListener('keydown', resume)
        }

        const engine = await createLipSyncEngine(ctx)
        if (!alive) { engine.destroy(); return }
        lipSyncRef.current = engine
        lipSyncReadyRef.current = true

        // 把 lip sync 节点连接到 destination（AudioWorklet 需要 audio graph 活跃才能处理）
        engine.node.connect(ctx.destination)

        console.log('[LipSync] ✅ 引擎初始化完成')

        // 启动 RAF 循环，持续读取嘴型值
        function loop() {
          if (!alive) return
          const engine = lipSyncRef.current
          if (engine) {
            const val = engine.getMouthOpen()
            setMouthOpen(val)
          }
          rafIdRef.current = requestAnimationFrame(loop)
        }
        loop()
      } catch (err) {
        console.warn('[LipSync] ⚠️ 初始化失败（wlipsync 可能未安装），将降级为无口型同步:', err)
      }
    }

    initLipSync()

    return () => {
      alive = false
      cancelAnimationFrame(rafIdRef.current)
      lipSyncRef.current?.destroy()
      lipSyncRef.current = null
      lipSyncReadyRef.current = false
      audioCtxRef.current?.close().catch(() => {})
      audioCtxRef.current = null
    }
  }, [])

  // ── 播放 TTS 音频 + 连接到 LipSync 引擎 ──────────────────────────────
  const playTTSAudio = useCallback(async (audioBase64: string, format: string) => {
    const ctx = audioCtxRef.current
    if (!ctx) {
      // fallback: 没有 AudioContext 直接用 Audio 元素播放
      const audio = new Audio(`data:audio/${format};base64,${audioBase64}`)
      audio.play().catch(() => {})
      return
    }

    try {
      // 确保 AudioContext 运行
      if (ctx.state === 'suspended') await ctx.resume()

      // 解码 base64 → ArrayBuffer → AudioBuffer
      const binaryStr = atob(audioBase64)
      const bytes = new Uint8Array(binaryStr.length)
      for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i)
      const audioBuffer = await ctx.decodeAudioData(bytes.buffer)

      // 创建播放源
      const source = ctx.createBufferSource()
      source.buffer = audioBuffer

      // 连接到 lip sync 节点（口型分析）
      const engine = lipSyncRef.current
      if (engine && lipSyncReadyRef.current) {
        engine.connectSource(source)
        console.log('[LipSync] 🎤 音频源已连接到口型分析节点')
      }

      // 同时连接到扬声器（播放音频）
      source.connect(ctx.destination)
      source.start(0)

      source.onended = () => {
        console.log('[LipSync] 🔇 音频播放结束')
        try { source.disconnect() } catch { /* ignore */ }
      }
    } catch (err) {
      console.warn('[TTS/LipSync] 音频播放失败，fallback 到 Audio 元素:', err)
      const audio = new Audio(`data:audio/${format};base64,${audioBase64}`)
      audio.play().catch(() => {})
    }
  }, [])

  // ── Tauri 事件监听 ────────────────────────────────────────────────────
  useEffect(() => {
    const unlistens: (() => void)[] = []

    async function setupListeners() {
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const { listen } = await import('@tauri-apps/api/event' as any)
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const tauriWindow = await import('@tauri-apps/api/window' as any)
        const win = tauriWindow.getCurrentWindow()

        const u1 = await listen('live2d:emotion_change', (event: any) => {
          setPendingEmotion(event.payload.emotion as EmotionType)
        })
        unlistens.push(u1)

        const u2 = await listen('live2d:model_change', (event: any) => {
          const src = event.payload.src as string
          setModelSrc(src)
          localStorage.setItem(MODEL_STORAGE_KEY, src)
        })
        unlistens.push(u2)

        const u3 = await listen('live2d:config_change', async (event: any) => {
          const newCfg: CompanionConfig = { ...DEFAULT_CONFIG, ...event.payload }
          setConfig(newCfg)
          localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(newCfg))
          try {
            const { LogicalSize } = await import('@tauri-apps/api/dpi' as any)
            await win.setSize(new LogicalSize(newCfg.winW, newCfg.winH))
          } catch {}
        })
        unlistens.push(u3)

        // ── 监听 TTS 音频事件（来自 ChatPage）─────────────────────────
        const u4 = await listen('live2d:tts_audio', (event: any) => {
          const { audio, format } = event.payload as { audio: string; format: string }
          console.log('[Live2D WS] 🔊 收到 TTS 音频事件, format=%s, size=%d bytes', format, audio.length)
          playTTSAudio(audio, format)
        })
        unlistens.push(u4)

      } catch {
        // 非 Tauri 环境静默忽略
      }
    }

    setupListeners()
    return () => { unlistens.forEach(u => u()) }
  }, [playTTSAudio])

  // ── WebSocket 连接后端事件流 ──────────────────────────────────────────
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let alive = true

    function connect() {
      if (!alive) return
      console.log('[Live2D WS] 正在连接 ws://localhost:8000/ws/chat?chat_id=live2d_companion ...')
      try {
        ws = new WebSocket('ws://localhost:8000/ws/chat?chat_id=live2d_companion')
        ws.onopen = () => console.log('[Live2D WS] ✅ 已连接后端事件流')
        ws.onmessage = (ev) => {
          try {
            const data = JSON.parse(ev.data)
            console.log('[Live2D WS] 📨 收到消息 type=%s', data.type, data)
            handleBackendEvent(data)
          } catch (e) {
            console.warn('[Live2D WS] ❌ 消息解析失败:', e, ev.data)
          }
        }
        ws.onclose = (e) => {
          console.log('[Live2D WS] 🔌 连接断开 code=%d reason=%s，5秒后重连...', e.code, e.reason)
          if (alive) reconnectTimer = setTimeout(connect, 5000)
        }
        ws.onerror = (e) => {
          console.error('[Live2D WS] ❌ 连接错误', e)
          ws?.close()
        }
      } catch (e) {
        console.error('[Live2D WS] ❌ 创建 WebSocket 失败', e)
        if (alive) reconnectTimer = setTimeout(connect, 5000)
      }
    }

    function handleBackendEvent(data: any) {
      const type = data.type as string

      // ── 后端广播事件 ──
      if (type === 'navi:blacklist_kill') {
        // 黑名单击杀 → 生气表情 + 文字气泡
        const emotion = (data.emotion as EmotionType) || 'angry'
        console.log('[Live2D 情绪] 🔴 黑名单击杀 process=%s → emotion=%s', data.process, emotion)
        setPendingEmotion(emotion)
        showBubble(data.message || `${data.process} 被关掉了！`, 6000)
      }
      else if (type === 'navi:report_done') {
        // 日报生成完成 → 开心表情 + 文字气泡
        const emotion = (data.emotion as EmotionType) || 'happy'
        console.log('[Live2D 情绪] 📋 日报生成完成 → emotion=%s, message=%s', emotion, data.message)
        setPendingEmotion(emotion)
        showBubble(data.message || '日报生成好啦~', 8000)
      }
      else if (type === 'navi:emotion') {
        // 直接触发情绪
        console.log('[Live2D 情绪] 💡 直接触发 emotion=%s, message=%s', data.emotion, data.message)
        setPendingEmotion(data.emotion as EmotionType)
        if (data.message) showBubble(data.message)
      }
      else if (type === 'navi:message') {
        // 通用消息气泡
        console.log('[Live2D 气泡] 💬 通用消息 message=%s duration=%d', data.message || data.content, data.duration)
        showBubble(data.message || data.content || '', data.duration || 5000)
      }
      // ── TTS 音频（来自后端 WebSocket 直推）──────────────────────────
      else if (type === 'tts_audio' && data.audio) {
        console.log('[Live2D WS] 🔊 收到 TTS 音频 (WebSocket), format=%s', data.format)
        playTTSAudio(data.audio, data.format || 'mp3')
      }
      // ── AgentLoop 对话回复也触发表情 + 台词框 ──
      else if (type === 'reply' && data.content) {
        // 优先用 LLM 直接输出的结构化情绪，fallback 到关键词推断
        const emotion: EmotionType = (data.emotion as EmotionType) || inferEmotion(data.content)
        const source = data.emotion ? '🏷️ LLM结构化标签' : '🔍 关键词推断'
        console.log(
          `[Live2D 情绪] 🤖 ${source}: ${emotion} | 内容片段: ${data.content.slice(0, 60)}`
        )
        if (emotion !== 'neutral') {
          console.log('[Live2D 情绪] ✅ 触发情绪 →', emotion)
          setPendingEmotion(emotion)
        } else {
          console.log('[Live2D 情绪] ⚪ 情绪为 neutral，不触发动作')
        }
        // 按 [SPLIT] 拆段，依次显示气泡（每段间隔 2.5s）
        const bubbleParts = data.content
          .split('[SPLIT]')
          .map((s: string) => s.trim())
          .filter(Boolean)
        bubbleParts.forEach((part: string, idx: number) => {
          const display = part.length > 80 ? part.slice(0, 80) + '…' : part
          setTimeout(() => showBubble(display, 4000), idx * 2500)
        })
      }
      else {
        // 未处理的事件类型
        console.log('[Live2D WS] ⚠️ 未处理的事件类型:', type, data)
      }
    }

    connect()
    return () => {
      alive = false
      if (reconnectTimer) clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [showBubble, playTTSAudio])

  return (
    <div style={{ width: '100vw', height: '100vh', background: 'transparent', overflow: 'hidden', position: 'relative' }}>
      <Live2DScene
        modelSrc={modelSrc}
        pendingEmotion={pendingEmotion}
        onEmotionHandled={handleEmotionHandled}
        modelScale={config.modelScale}
        offsetY={config.offsetY}
        mouthOpenValue={mouthOpen}
      />
      {/* 文字气泡 */}
      {bubble && (
        <div style={{
          position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)',
          background: 'linear-gradient(135deg, rgba(255,255,255,0.97), rgba(240,249,255,0.97))',
          borderRadius: 16,
          padding: '10px 18px', maxWidth: '85%', minWidth: 60,
          boxShadow: '0 6px 24px rgba(0,0,0,0.18), 0 2px 8px rgba(6,182,212,0.12)',
          fontSize: 13, color: '#0f3b47', fontWeight: 500, lineHeight: 1.7,
          textAlign: 'center', pointerEvents: 'none',
          animation: 'bubbleFadeIn 0.35s ease-out',
          backdropFilter: 'blur(12px)',
          border: '1.5px solid rgba(6,182,212,0.25)',
          zIndex: 999,
        }}>
          {bubble}
          {/* 小三角箭头指向下方模型 */}
          <div style={{
            position: 'absolute', bottom: -8, left: '50%', transform: 'translateX(-50%)',
            width: 0, height: 0,
            borderLeft: '8px solid transparent',
            borderRight: '8px solid transparent',
            borderTop: '8px solid rgba(255,255,255,0.97)',
            filter: 'drop-shadow(0 2px 2px rgba(0,0,0,0.08))',
          }} />
        </div>
      )}
      <style>{`
        @keyframes bubbleFadeIn {
          from { opacity: 0; transform: translateX(-50%) translateY(-12px) scale(0.95); }
          to { opacity: 1; transform: translateX(-50%) translateY(0) scale(1); }
        }
      `}</style>
    </div>
  )
}

const root = document.getElementById('live2d-root')!
createRoot(root).render(
  <StrictMode>
    <CompanionApp />
  </StrictMode>
)