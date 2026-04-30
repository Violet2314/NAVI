import { useState, useCallback, useEffect, useRef } from 'react'
import {
  RobotOutlined,
  AppstoreOutlined,
  BgColorsOutlined,
  DragOutlined,
  EyeOutlined,
  MessageOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  UndoOutlined,
  DownOutlined,
} from '@ant-design/icons'

interface ModelInfo {
  id: string
  name: string
  src: string
}

interface CompanionConfig {
  modelScale: number
  offsetY: number
  winW: number
  winH: number
}

const C = {
  bg: 'linear-gradient(135deg, #e0f7fa 0%, #f0fdf4 55%, #fef9c3 100%)',
  card: 'rgba(255,255,255,0.82)',
  cardBorder: 'rgba(6,182,212,0.20)',
  primary: '#0891b2',
  text: '#0f3b47',
  textSub: '#4e7d8a',
  textMute: '#93bfc9',
  shadow: '0 4px 24px rgba(6,182,212,0.12)',
}


const MODEL_STORAGE_KEY = 'navi:live2d:selected-model'
const CONFIG_STORAGE_KEY = 'navi:live2d:config'
const DEFAULT_CONFIG: CompanionConfig = { modelScale: 1.0, offsetY: 0, winW: 380, winH: 600 }

function loadConfig(): CompanionConfig {
  try {
    const s = localStorage.getItem(CONFIG_STORAGE_KEY)
    if (s) return { ...DEFAULT_CONFIG, ...JSON.parse(s) }
  } catch {}
  return DEFAULT_CONFIG
}

export function Live2DPage() {
  const [companionOpen, setCompanionOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [selectedId, setSelectedId] = useState<string>(
    () => localStorage.getItem(MODEL_STORAGE_KEY) ?? 'hiyori'
  )
  const [config, setConfig] = useState<CompanionConfig>(loadConfig)
  const [configOpen, setConfigOpen] = useState(false)
  const emitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── 页面加载时检测伴侣窗口是否已存在（解决切页面回来状态丢失） ──
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow' as any)
        const win = await WebviewWindow.getByLabel('live2d-companion')
        if (!cancelled && win) {
          // 检查窗口是否可见（isVisible 返回 Promise<boolean>）
          const visible = await win.isVisible()
          if (visible) setCompanionOpen(true)
        }
      } catch {}
    })()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    // 优先读 build 时生成的静态 manifest（/live2d/models.json），
    // 这样即使 Python 后端没起来，角色列表也能自动呈现（公开目录 -> 自动生成）。
    // 失败才 fallback 到后端 API。
    let cancelled = false
    const applyModels = (data: ModelInfo[]) => {
      if (cancelled || !Array.isArray(data) || data.length === 0) return
      setModels(data)
      if (!data.find(m => m.id === selectedId)) {
        setSelectedId(data[0].id)
      }
    }
    fetch('/live2d/models.json', { cache: 'no-store' })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error('no manifest'))))
      .then(applyModels)
      .catch(() => {
        fetch('http://localhost:8000/api/live2d/models')
          .then(r => r.json())
          .then(applyModels)
          .catch(console.error)
      })
    return () => { cancelled = true }
  }, [])

  const selectedModel = models.find(m => m.id === selectedId) ?? models[0]

  const emitModelChange = useCallback(async (src: string) => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { emitTo } = await import('@tauri-apps/api/event' as any)
      await emitTo('live2d-companion', 'live2d:model_change', { src })
    } catch {}
  }, [])

  const emitConfigChange = useCallback(async (cfg: CompanionConfig) => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { emitTo } = await import('@tauri-apps/api/event' as any)
      await emitTo('live2d-companion', 'live2d:config_change', cfg)
    } catch {}
  }, [])

  const handleModelSelect = useCallback((id: string) => {
    setSelectedId(id)
    localStorage.setItem(MODEL_STORAGE_KEY, id)
    const model = models.find(m => m.id === id)
    if (model) emitModelChange(model.src)
  }, [models, emitModelChange])

  const updateConfig = useCallback((partial: Partial<CompanionConfig>) => {
    setConfig(prev => {
      const next = { ...prev, ...partial }
      localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(next))
      if (emitTimerRef.current) clearTimeout(emitTimerRef.current)
      emitTimerRef.current = setTimeout(() => emitConfigChange(next), 80)
      return next
    })
  }, [emitConfigChange])

  const openCompanion = useCallback(async () => {
    if (loading) return
    setLoading(true)
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow' as any)
      let win = await WebviewWindow.getByLabel('live2d-companion')
      if (!win) {
        win = new WebviewWindow('live2d-companion', {
          url: 'http://localhost:1420/live2d.html',
          title: '',
          decorations: false,
          transparent: true,
          shadow: false,
          alwaysOnTop: true,
          skipTaskbar: true,
          width: config.winW,
          height: config.winH,
          resizable: false,
          x: 1400, y: 100,
        })
        await new Promise<void>((resolve, reject) => {
          win.once('tauri://created', () => resolve())
          win.once('tauri://error', (e: unknown) => reject(e))
        })
      } else {
        await win.show()
        await win.setFocus()
      }
      if (selectedModel) await emitModelChange(selectedModel.src)
      await emitConfigChange(config)
      setCompanionOpen(true)
    } catch (err) {
      console.error('[Live2DPage] 无法打开伴侣窗口:', err)
    }
    setLoading(false)
  }, [loading, selectedModel, config, emitModelChange, emitConfigChange])

  const closeCompanion = useCallback(async () => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow' as any)
      const win = await WebviewWindow.getByLabel('live2d-companion')
      if (win) {
        // 彻底关闭窗口，下次 openCompanion 会重新创建
        // 避免 hide/show 导致 WebSocket 重复连接、motionManager hook 叠加引起模型抽搐
        try {
          await win.close()
        } catch {
          // close() 失败则 fallback 到 hide()
          await win.hide()
        }
      }
      setCompanionOpen(false)
    } catch (err) {
      console.error('[Live2DPage] 无法关闭伴侣窗口:', err)
    }
  }, [])

  // 滑块通用组件
  const Slider = ({
    label, value, min, max, step, format, onChange,
  }: {
    label: string; value: number; min: number; max: number; step: number
    format: (v: number) => string; onChange: (v: number) => void
  }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
      <span style={{ fontSize: 11, color: C.textSub, minWidth: 58, flexShrink: 0 }}>{label}</span>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(Number(e.target.value))}
        style={{ flex: 1, accentColor: C.primary, cursor: 'pointer' }}
      />
      <span style={{ fontSize: 11, color: C.text, minWidth: 36, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        {format(value)}
      </span>
    </div>
  )

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      gap: 16,
    }}>

      {/* 主卡片 */}
      <div style={{ background: 'var(--bg-app)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: '28px 36px', textAlign: 'center', maxWidth: 460, width: '100%' }}>
        {/* 图标 */}
        <div style={{
          width: 64, height: 64, borderRadius: '50%', margin: '0 auto 16px',
          background: 'var(--primary-light)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          border: '1px solid var(--border)',
        }}>
          <RobotOutlined style={{ fontSize: 28, color: 'var(--primary)' }} />
        </div>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>Navi 桌宠伴侣</div>
        <div style={{ fontSize: 12, color: 'var(--text-sub)', marginBottom: 22, lineHeight: 1.8 }}>
          唤出 Live2D 伴侣，她会始终陪伴在你的屏幕角落<br />
          跟随鼠标、自动眨眼、感知你的对话情绪
        </div>

        {!companionOpen ? (
          <button onClick={openCompanion} disabled={loading} style={{
            background: loading ? 'var(--bg-hover)' : 'var(--primary)',
            color: loading ? 'var(--text-muted)' : '#fff',
            border: 'none', borderRadius: 8,
            padding: '9px 24px', fontSize: 13, fontWeight: 600,
            cursor: loading ? 'not-allowed' : 'pointer',
            transition: 'opacity 0.15s',
            display: 'flex', alignItems: 'center', gap: 8, margin: '0 auto',
          }}>
            {loading
              ? <LoadingOutlined style={{ fontSize: 13 }} />
              : <RobotOutlined style={{ fontSize: 13 }} />
            }
            {loading ? '召唤中...' : '唤出伴侣'}
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
            <div style={{ background: 'var(--green-bg)', color: 'var(--green)', borderRadius: 8, padding: '7px 14px', fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6, border: '1px solid var(--green)' }}>
              <CheckCircleOutlined />
              伴侣已在屏幕上
            </div>
            <button onClick={closeCompanion} style={{ background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red)', borderRadius: 8, padding: '7px 14px', fontSize: 12, fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}>
              <CloseCircleOutlined />
              收起
            </button>
          </div>
        )}
      </div>

      {/* 模型选择 + 外观调节 */}
      <div style={{ background: 'var(--bg-app)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: '16px 20px', maxWidth: 460, width: '100%' }}>
        {/* 角色选择 */}
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
          <AppstoreOutlined style={{ color: 'var(--primary)' }} />
          选择角色
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 7, marginBottom: 14 }}>
          {models.map(model => (
            <button
              key={model.id}
              onClick={() => handleModelSelect(model.id)}
              style={{
                background: selectedId === model.id ? 'var(--primary-light)' : 'var(--bg-hover)',
                border: selectedId === model.id ? '1.5px solid var(--primary)' : '1px solid var(--border)',
                borderRadius: 8, padding: '7px 5px',
                fontSize: 11, fontWeight: selectedId === model.id ? 700 : 500,
                color: selectedId === model.id ? 'var(--primary)' : 'var(--text-sub)',
                cursor: 'pointer', transition: 'all 0.12s', textAlign: 'center',
              }}
            >
              {model.name}
            </button>
          ))}
        </div>

        {/* 外观调节折叠 */}
        <button
          onClick={() => setConfigOpen(v => !v)}
          style={{
            width: '100%', background: 'none', border: '1px solid var(--border)',
            borderRadius: 7, padding: '6px 10px', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            fontSize: 11, color: 'var(--text-sub)', fontWeight: 600,
          }}
        >
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <BgColorsOutlined />
            外观调节
          </span>
          <DownOutlined style={{ fontSize: 10, transition: 'transform 0.2s', transform: configOpen ? 'rotate(180deg)' : 'none' }} />
        </button>

        {configOpen && (
          <div style={{ marginTop: 10, padding: '10px 4px 4px' }}>
            <Slider label="模型缩放" value={config.modelScale} min={0.3} max={3.0} step={0.05}
              format={v => `${v.toFixed(2)}x`} onChange={v => updateConfig({ modelScale: v })} />
            <Slider label="上移偏移" value={config.offsetY} min={-0.5} max={0.8} step={0.02}
              format={v => `${(v * 100).toFixed(0)}%`} onChange={v => updateConfig({ offsetY: v })} />
            <Slider label="窗口宽度" value={config.winW} min={200} max={1920} step={10}
              format={v => `${v}px`} onChange={v => updateConfig({ winW: v })} />
            <Slider label="窗口高度" value={config.winH} min={300} max={1440} step={10}
              format={v => `${v}px`} onChange={v => updateConfig({ winH: v })} />
            <button
              onClick={() => updateConfig(DEFAULT_CONFIG)}
              style={{
                marginTop: 6, fontSize: 10, color: 'var(--text-muted)', background: 'none',
                border: '1px solid var(--border)', borderRadius: 6, padding: '3px 10px',
                cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
              }}
            >
              <UndoOutlined style={{ fontSize: 10 }} />
              重置默认
            </button>
          </div>
        )}
      </div>

      {/* 提示 */}
      <div style={{ background: 'var(--bg-app)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: '12px 18px', maxWidth: 460, width: '100%' }}>
        <div style={{ fontSize: 11, color: 'var(--text-sub)', lineHeight: 2.2, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <DragOutlined style={{ color: 'var(--primary)', width: 14 }} />
            <span><strong style={{ color: 'var(--text)' }}>拖拽</strong>角色可移动位置</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <EyeOutlined style={{ color: 'var(--primary)', width: 14 }} />
            <span><strong style={{ color: 'var(--text)' }}>鼠标靠近</strong>时她会用眼神追随你</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <MessageOutlined style={{ color: 'var(--primary)', width: 14 }} />
            <span><strong style={{ color: 'var(--text)' }}>对话时</strong>她会根据回复内容变换表情</span>
          </div>
        </div>
      </div>
    </div>
  )
}