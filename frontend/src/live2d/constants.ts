// ============================================================
// 情绪类型定义（对齐 airi constants/emotions.ts）
// ============================================================
export type EmotionType =
  | 'happy'
  | 'sad'
  | 'angry'
  | 'think'
  | 'surprised'
  | 'awkward'
  | 'question'
  | 'curious'
  | 'neutral'
  | 'normal'

// ============================================================
// 情绪 → Motion 组名映射（airi 同款约定）
// 模型只需按此命名 motion 组即可自动生效，无需每个模型单独配置
// ============================================================
export const EMOTION_MOTION_GROUP: Record<EmotionType, string> = {
  happy:     'Happy',
  sad:       'Sad',
  angry:     'Angry',
  think:     'Think',
  surprised: 'Surprise',
  awkward:   'Awkward',
  question:  'Question',
  curious:   'Curious',
  neutral:   'Idle',   // fallback 到 Idle
  normal:    'Idle',   // LLM 有时返回 normal 而不是 neutral
}

// ============================================================
// 情绪 → Expression 名称映射（pixi-live2d-display model.expression(name)）
// 若模型没有对应 expression，pixi 会静默忽略，不需要 try/catch
// ============================================================
export const EMOTION_EXPRESSION: Record<EmotionType, string | undefined> = {
  happy:     'happy',
  sad:       'sad',
  angry:     'angry',
  think:     undefined,
  surprised: 'surprised',
  awkward:   undefined,
  question:  undefined,
  curious:   'surprised',
  neutral:   undefined,
  normal:    undefined,
}

// ============================================================
// LLM 回复内容 → 情绪关键词匹配（简易版，后续可换 LLM 结构化输出）
// ============================================================
export const EMOTION_KEYWORDS: Record<EmotionType, string[]> = {
  happy:     ['哈哈', '开心', '棒', '太好了', '好的', '完成', '成功', '😊', '😄', '🎉', 'great', 'awesome', 'done'],
  sad:       ['抱歉', '对不起', '失败', '没法', '无法', '遗憾', '😢', '😔', 'sorry', 'failed'],
  angry:     ['错误', '警告', '不对', '问题', '⚠️', 'error', 'warning'],
  surprised: ['哇', '真的', '没想到', '居然', '竟然', '😲', 'wow', 'really', 'unexpected'],
  think:     ['让我', '思考', '分析', '正在', '检索', '查询', '💭', 'thinking', 'analyzing', 'searching'],
  awkward:   ['尴尬', '不好意思', '额', '呃', 'awkward', 'oops'],
  question:  ['？', '什么', '怎么', '为什么', 'what', 'how', 'why', '?'],
  curious:   ['有趣', '好奇', '发现', '原来', 'interesting', 'curious', 'found'],
  neutral:   [],
}

// 用 LLM 回复内容推断情绪（按优先级顺序匹配）
const EMOTION_PRIORITY: EmotionType[] = [
  'angry', 'surprised', 'sad', 'think', 'awkward', 'question', 'curious', 'happy', 'neutral',
]

export function inferEmotion(content: string): EmotionType {
  const lower = content.toLowerCase()
  for (const emotion of EMOTION_PRIORITY) {
    if (emotion === 'neutral') continue
    const keywords = EMOTION_KEYWORDS[emotion]
    if (keywords.some(kw => lower.includes(kw))) return emotion
  }
  return 'neutral'
}

// ============================================================
// 情绪 → motion 组名 模糊匹配关键词（airi 同款 fuzzy 机制）
// 每种情绪按优先级列关键词，匹配模型里实际的 motion 组名
// ============================================================
const EMOTION_MOTION_FUZZY: Record<EmotionType, string[]> = {
  happy:     ['happy', 'joy', 'smile', 'laugh', 'cheer', 'excited'],
  sad:       ['sad', 'cry', 'depress', 'down', 'grieve'],
  angry:     ['angry', 'anger', 'mad', 'rage', 'annoy'],
  surprised: ['surprise', 'shock', 'amaze', 'wow', 'startle'],
  think:     ['think', 'ponder', 'consider', 'hmm', 'wonder'],
  awkward:   ['awkward', 'shy', 'embarrass', 'sweat', 'blush'],
  question:  ['question', 'ask', 'query', 'curious2', 'doubt'],
  curious:   ['curious', 'interest', 'peek', 'look'],
  neutral:   ['idle', 'wait', 'stand'],
}

const ALL_EMOTIONS: EmotionType[] = [
  'happy', 'sad', 'angry', 'surprised', 'think', 'awkward', 'question', 'curious', 'neutral',
]

/**
 * 根据模型实际 motion 组名列表，模糊匹配出每种情绪对应的组名。
 * 找不到则 fallback 到 idleGroup。
 */
export function buildEmotionMotionMap(
  groups: string[],
  idleGroup: string,
): Record<EmotionType, string> {
  const lowerGroups = groups.map(g => g.toLowerCase())
  const result = {} as Record<EmotionType, string>

  for (const emotion of ALL_EMOTIONS) {
    const fuzzyKws = EMOTION_MOTION_FUZZY[emotion]
    // 先尝试标准名称（如 'Happy'、'Sad'）
    const stdName = EMOTION_MOTION_GROUP[emotion]
    let found = groups.find(g => g === stdName) ?? ''

    if (!found) {
      // 标准名不存在，fuzzy 匹配
      for (const kw of fuzzyKws) {
        const idx = lowerGroups.findIndex(g => g.includes(kw))
        if (idx !== -1) { found = groups[idx]; break }
      }
    }

    result[emotion] = found || idleGroup
    if (found && found !== stdName) {
      console.log(`[Live2D EmotionMap] ${emotion}: 标准名 "${stdName}" 不存在，fuzzy 命中 → "${found}"`)
    } else if (!found) {
      console.log(`[Live2D EmotionMap] ${emotion}: 未命中任何 motion 组，fallback → "${idleGroup}"`)
    } else {
      console.log(`[Live2D EmotionMap] ${emotion}: 标准名命中 → "${found}"`)
    }
  }

  return result
}
