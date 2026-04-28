/**
 * Live2D Lip Sync Engine
 * 基于 wLipSync (MFCC 元音分析) 驱动 Live2D 口型
 * 参考 airi/packages/model-driver-lipsync
 */

import { createWLipSyncNode, type Profile } from 'wlipsync'
// profile.json 包含 MFCC 校准数据（AEIOU + Silence 的频谱特征）
import wlipsyncProfile from './wlipsync-profile.json'

// ─── 常量 ────────────────────────────────────────────────────────────────────
const RAW_KEYS = ['A', 'E', 'I', 'O', 'U', 'S'] as const
type RawKey = typeof RAW_KEYS[number]
export type VowelKey = 'A' | 'E' | 'I' | 'O' | 'U'

/** S(silence) 映射到 I，避免嘴巴猛然合拢 */
const RAW_TO_VOWEL: Record<RawKey, VowelKey> = {
  A: 'A', E: 'E', I: 'I', O: 'O', U: 'U', S: 'I',
}

// ─── 接口 ────────────────────────────────────────────────────────────────────
export interface LipSyncEngine {
  /** wLipSync AudioWorkletNode，把音频源 connect 到这里 */
  node: AudioNode
  /** 获取每个元音的权重 (0-1)，已按音量缩放 */
  getVowelWeights: () => Record<VowelKey, number>
  /** 获取综合张嘴程度 (0-1) */
  getMouthOpen: () => number
  /** 便捷方法：把 AudioNode 连接到 lip sync 节点 */
  connectSource: (source: AudioNode) => void
  /** 断开所有连接并清理 */
  destroy: () => void
}

export interface LipSyncOptions {
  /** 元音权重上限，默认 0.7 */
  cap?: number
  /** 音量乘数，默认 0.9 */
  volumeScale?: number
  /** 音量曲线指数，默认 0.7 */
  volumeExponent?: number
  /** 嘴型更新最小间隔 ms，默认 40 (~25fps) */
  mouthUpdateIntervalMs?: number
  /** 平滑插值窗口 ms，默认 120 */
  mouthLerpWindowMs?: number
}

// ─── 工厂函数 ────────────────────────────────────────────────────────────────
/**
 * 创建 Live2D 口型同步引擎
 * 内部使用 wLipSync AudioWorklet 做 MFCC 分析，输出 AEIOU 元音权重
 */
export async function createLipSyncEngine(
  audioContext: AudioContext,
  options: LipSyncOptions = {},
): Promise<LipSyncEngine> {
  const node = await createWLipSyncNode(audioContext, wlipsyncProfile as unknown as Profile)

  const cap = options.cap ?? 0.7
  const volumeScale = options.volumeScale ?? 0.9
  const volumeExponent = options.volumeExponent ?? 0.7
  const mouthUpdateIntervalMs = options.mouthUpdateIntervalMs ?? 40
  const mouthLerpWindowMs = options.mouthLerpWindowMs ?? 120

  const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now())

  let lastRawMouthOpen = 0
  let lastRawUpdateMs = 0
  let smoothedMouthOpen = 0
  let lastSmoothedMs = 0

  const getVowelWeights = (): Record<VowelKey, number> => {
    const projected: Record<VowelKey, number> = { A: 0, E: 0, I: 0, O: 0, U: 0 }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const n = node as any
    const amp = Math.min((n.volume ?? 0) * volumeScale, 1) ** volumeExponent

    for (const raw of RAW_KEYS) {
      const vowel = RAW_TO_VOWEL[raw]
      const rawVal = n.weights?.[raw] ?? 0
      projected[vowel] = Math.max(projected[vowel], Math.min(cap, rawVal * amp))
    }
    return projected
  }

  const computeMouthOpen = () => {
    const weights = Object.values(getVowelWeights())
    return weights.length ? Math.max(...weights) : 0
  }

  const maybeUpdateRawMouthOpen = (timestamp: number) => {
    if (lastRawUpdateMs === 0 || mouthUpdateIntervalMs <= 0 || timestamp - lastRawUpdateMs >= mouthUpdateIntervalMs) {
      lastRawMouthOpen = computeMouthOpen()
      lastRawUpdateMs = timestamp
    }
  }

  const getSmoothedMouthOpen = (timestamp: number) => {
    if (lastSmoothedMs === 0 || mouthLerpWindowMs <= 0) {
      smoothedMouthOpen = lastRawMouthOpen
      lastSmoothedMs = timestamp
      return smoothedMouthOpen
    }
    const alpha = Math.min(1, (timestamp - lastSmoothedMs) / mouthLerpWindowMs)
    smoothedMouthOpen += (lastRawMouthOpen - smoothedMouthOpen) * alpha
    lastSmoothedMs = timestamp
    return smoothedMouthOpen
  }

  // 初始化嘴型状态
  const initialTimestamp = now()
  lastRawMouthOpen = computeMouthOpen()
  lastRawUpdateMs = initialTimestamp
  smoothedMouthOpen = lastRawMouthOpen
  lastSmoothedMs = initialTimestamp

  const getMouthOpen = () => {
    const timestamp = now()
    maybeUpdateRawMouthOpen(timestamp)
    return getSmoothedMouthOpen(timestamp)
  }

  const connectSource = (source: AudioNode) => {
    try {
      source.connect(node)
    } catch (error) {
      console.error('[LipSync] failed to connect source:', error)
    }
  }

  const destroy = () => {
    try {
      node.disconnect()
    } catch { /* ignore */ }
  }

  return { node, getVowelWeights, getMouthOpen, connectSource, destroy }
}
