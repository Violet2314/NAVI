/**
 * ActivityTab.tsx — 活动记录 Tab（v4：智能聚合 + 三层展示）
 *
 * 显示层级：
 *   聚合组（Group） → 后端段（Segment） → 子活动（SubActivity）
 *
 * 聚合规则（纯前端，不改后端）：
 *   1. 相邻段主导分类相同 → 合入同一组
 *   2. 组的时间跨度不超过 30 分钟 → 超了拆新组
 *   3. 两段间隔 > 5 分钟 → 拆新组
 *   4. 只有 1 个段的组 → 扁平显示，不额外嵌套
 */
import { useState, useMemo } from "react";
import { InboxOutlined, UnorderedListOutlined, RightOutlined, DownOutlined } from "@ant-design/icons";

/* ── 常量 ──────────────────────────────────────────────────────────────── */

const CAT: Record<string, { color: string; bg: string; label: string }> = {
  learning:      { color: "var(--green)",      bg: "var(--green-bg)",    label: "学习" },
  work:          { color: "var(--blue)",       bg: "var(--blue-bg)",     label: "工作" },
  entertainment: { color: "var(--red)",        bg: "var(--red-bg)",      label: "娱乐" },
  utility:       { color: "var(--yellow)",     bg: "var(--yellow-bg)",   label: "工具" },
  other:         { color: "var(--text-muted)", bg: "#f3f4f6",            label: "其他" },
};

const CAT_BAR_COLORS: Record<string, string> = {
  learning: "#22c55e", work: "#3b82f6", entertainment: "#ef4444",
  utility: "#eab308", other: "#9ca3af",
};

/** 聚合参数 */
const GROUP_MAX_SPAN_SEC = 30 * 60; // 组最大时间跨度：30 分钟
const GROUP_GAP_SEC = 5 * 60;       // 段间隔超过 5 分钟则拆组

/* ── 类型 ──────────────────────────────────────────────────────────────── */

type ChildActivity = {
  process_name: string; window_title: string; started_at: string;
  ended_at?: string; duration_sec: number; app_category: string;
};

type MergedChild = {
  process_name: string; window_title: string; started_at: string;
  duration_sec: number; app_category: string; count: number;
};

type CategoryBreakdown = Record<string, number>;

type ActivityItem = {
  process_name: string; window_title: string; started_at: string;
  duration_sec: number; app_category: string; sentiment: string;
  category_breakdown?: CategoryBreakdown;
  is_current?: boolean;
  children?: ChildActivity[];
};

/** 聚合组 */
type ActivityGroup = {
  segments: ActivityItem[];           // 组内的后端段
  startedAt: string;                  // 最早段的 started_at
  endedAt: string;                    // 最晚段的结束时间 or started_at
  totalDuration: number;              // 总时长（秒）
  breakdown: CategoryBreakdown;       // 聚合后的分类分布
  dominantCategory: string;           // 主导分类
  dominantProcess: string;            // 占时最长的进程名
  isCurrent: boolean;                 // 组内是否包含进行中的段
  segmentCount: number;               // 后端段数量
};

/* ── 工具函数 ─────────────────────────────────────────────────────────── */

const fmtTime = (s: string) => s ? s.slice(11, 16) : "";
const fmtDur = (d: number) =>
  d >= 3600 ? `${Math.floor(d / 3600)}h${Math.floor((d % 3600) / 60)}m`
    : d >= 60 ? `${Math.floor(d / 60)}m`
      : `${d}s`;

const parseTs = (s: string) => new Date(s).getTime();

/** 从 breakdown 中获取主导分类
 *  "other" 和空字符串不参与竞争：只有在没有任何有意义分类时才返回 "other"，
 *  避免大量 pending/未知 活动把有效分类的大段误标为"其他"。
 */
function getDominant(bd: CategoryBreakdown): string {
  const SKIP = new Set(["other", ""]);
  let max = 0, dom = "";
  for (const [k, v] of Object.entries(bd)) {
    if (!SKIP.has(k) && v > max) { max = v; dom = k; }
  }
  return dom || "other";
}

/** 合并多个 breakdown */
function mergeBreakdowns(items: { breakdown: CategoryBreakdown }[]): CategoryBreakdown {
  const merged: CategoryBreakdown = {};
  for (const { breakdown } of items) {
    for (const [k, v] of Object.entries(breakdown)) {
      merged[k] = (merged[k] || 0) + v;
    }
  }
  return merged;
}

/** 按进程名合并子活动 */
function mergeChildren(children: ChildActivity[]): MergedChild[] {
  const map = new Map<string, MergedChild>();
  for (const c of children) {
    const key = c.process_name.toLowerCase();
    const existing = map.get(key);
    if (existing) {
      existing.duration_sec += c.duration_sec;
      existing.count++;
      existing.window_title = c.window_title;
    } else {
      map.set(key, { ...c, count: 1 });
    }
  }
  return [...map.values()].sort((a, b) => b.duration_sec - a.duration_sec);
}

/* ── 智能聚合算法 ─────────────────────────────────────────────────────── */

/**
 * 将后端返回的段列表（按时间倒序）聚合为组。
 * 注意：activities 是倒序（最新在前），输出的 groups 也保持倒序。
 */
function groupActivities(activities: ActivityItem[]): ActivityGroup[] {
  if (activities.length === 0) return [];

  // 给每个 segment 预计算 breakdown 和 dominant
  const enriched = activities.map(a => ({
    item: a,
    breakdown: a.category_breakdown ?? { [a.app_category || "other"]: a.duration_sec },
    dominant: getDominant(a.category_breakdown ?? { [a.app_category || "other"]: a.duration_sec }),
    ts: parseTs(a.started_at),
    endTs: a.duration_sec ? parseTs(a.started_at) + a.duration_sec * 1000 : parseTs(a.started_at),
  }));

  const groups: ActivityGroup[] = [];
  let curGroup = [enriched[0]];

  for (let i = 1; i < enriched.length; i++) {
    const prev = enriched[i - 1]; // 时间更晚的
    const curr = enriched[i];     // 时间更早的

    // 注意：activities 是倒序，所以 curr.ts < prev.ts
    const gap = prev.ts - curr.endTs;                       // 两段间隔
    const span = curGroup[0].endTs - curr.ts;               // 合入后组的跨度
    const sameDominant = curr.dominant === curGroup[0].dominant 
                      || curr.dominant === getDominant(
                           mergeBreakdowns(curGroup.map(g => ({ breakdown: g.breakdown })))
                         );

    if (sameDominant && gap <= GROUP_GAP_SEC * 1000 && span <= GROUP_MAX_SPAN_SEC * 1000) {
      curGroup.push(curr);
    } else {
      groups.push(buildGroup(curGroup));
      curGroup = [curr];
    }
  }
  groups.push(buildGroup(curGroup));

  return groups;
}

function buildGroup(items: { item: ActivityItem; breakdown: CategoryBreakdown; ts: number; endTs: number }[]): ActivityGroup {
  const segments = items.map(x => x.item);
  const breakdown = mergeBreakdowns(items.map(x => ({ breakdown: x.breakdown })));
  const dominant = getDominant(breakdown);
  const totalDuration = segments.reduce((s, a) => s + a.duration_sec, 0);
  const isCurrent = segments.some(a => a.is_current);

  // 占时最长的进程
  const procDur: Record<string, number> = {};
  for (const seg of segments) {
    const kids = seg.children ?? [];
    if (kids.length > 0) {
      for (const k of kids) {
        const p = k.process_name.replace(".exe", "").toLowerCase();
        procDur[p] = (procDur[p] || 0) + k.duration_sec;
      }
    } else {
      const p = seg.process_name.replace(".exe", "").toLowerCase();
      procDur[p] = (procDur[p] || 0) + seg.duration_sec;
    }
  }
  const dominantProcess = Object.entries(procDur).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";

  // 时间范围（items 是倒序，最后一个最早）
  const startedAt = items[items.length - 1].item.started_at;
  const endedAt = items[0].item.started_at; // 最新段的 started_at

  return {
    segments, startedAt, endedAt, totalDuration, breakdown,
    dominantCategory: dominant, dominantProcess, isCurrent,
    segmentCount: segments.length,
  };
}

/* ── 组件 ─────────────────────────────────────────────────────────────── */

/** 分类比例条 */
function CategoryBar({ breakdown, totalDur, barWidth }: {
  breakdown: CategoryBreakdown; totalDur: number; barWidth?: string;
}) {
  const entries = Object.entries(breakdown)
    .filter(([, sec]) => sec > 0)
    .sort((a, b) => b[1] - a[1]);

  if (entries.length <= 1) {
    const [catKey] = entries[0] ?? ["other"];
    const cat = CAT[catKey] ?? CAT.other;
    return (
      <span style={{
        background: cat.bg, color: cat.color, borderRadius: 6,
        padding: "2px 7px", fontSize: 10, fontWeight: 600, flexShrink: 0,
      }}>{cat.label}</span>
    );
  }

  const total = Math.max(totalDur, 1);
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 4, flexShrink: 0,
      minWidth: 80, maxWidth: barWidth ?? "120px",
    }}>
      <div style={{
        display: "flex", height: 6, borderRadius: 3, overflow: "hidden",
        flex: 1, background: "var(--border)",
      }}>
        {entries.map(([catKey, sec]) => {
          const pct = (sec / total) * 100;
          if (pct < 2) return null;
          return (
            <div
              key={catKey}
              title={`${(CAT[catKey] ?? CAT.other).label} ${fmtDur(sec)} (${Math.round(pct)}%)`}
              style={{
                width: `${pct}%`,
                background: CAT_BAR_COLORS[catKey] ?? CAT_BAR_COLORS.other,
                transition: "width 0.3s ease",
              }}
            />
          );
        })}
      </div>
      {(() => {
        const [topCat] = entries[0];
        const cat = CAT[topCat] ?? CAT.other;
        return (
          <span style={{
            color: cat.color, fontSize: 9, fontWeight: 600,
            flexShrink: 0, whiteSpace: "nowrap",
          }}>{cat.label}</span>
        );
      })()}
    </div>
  );
}

/** 子活动行 */
function ChildRow({ item }: { item: MergedChild }) {
  const cat = CAT[item.app_category] ?? CAT.other;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, padding: "5px 16px 5px 72px",
      background: "rgba(6,182,212,0.02)",
      borderBottom: "1px solid var(--border)",
      fontSize: 11,
    }}>
      <span style={{
        width: 1, height: 14, background: "var(--border)", flexShrink: 0,
        marginRight: 4, borderRadius: 1,
      }} />
      <span style={{
        background: cat.bg, color: cat.color, borderRadius: 4,
        padding: "1px 5px", fontSize: 9, fontWeight: 600, flexShrink: 0,
      }}>{cat.label}</span>
      <span style={{
        color: "var(--text-sub)", flexShrink: 0, width: 56,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        fontSize: 10,
      }}>{item.process_name.replace(".exe", "")}</span>
      <span style={{
        flex: 1, overflow: "hidden", textOverflow: "ellipsis",
        whiteSpace: "nowrap", color: "var(--text-sub)",
      }}>{item.window_title}</span>
      <span style={{ color: "var(--text-muted)", flexShrink: 0, fontSize: 10 }}>
        {fmtDur(item.duration_sec)}
      </span>
    </div>
  );
}

/** 段行（组内的后端段，展开后显示） */
function SegmentRow({ item }: { item: ActivityItem }) {
  const [expanded, setExpanded] = useState(false);
  const children = item.children ?? [];
  const merged = useMemo(() => mergeChildren(children), [children]);
  const hasChildren = merged.length > 0;
  const breakdown: CategoryBreakdown = item.category_breakdown
    ?? { [item.app_category || "other"]: item.duration_sec };

  return (
    <div>
      <div
        onClick={() => hasChildren && setExpanded(!expanded)}
        style={{
          display: "flex", alignItems: "center", gap: 8, padding: "6px 16px 6px 40px",
          background: expanded ? "rgba(6,182,212,0.03)" : "rgba(6,182,212,0.015)",
          borderBottom: "1px solid var(--border)",
          fontSize: 11, cursor: hasChildren ? "pointer" : "default",
          userSelect: "none",
        }}
      >
        <span style={{ width: 12, flexShrink: 0, color: "var(--text-muted)", fontSize: 9 }}>
          {hasChildren ? (expanded ? <DownOutlined /> : <RightOutlined />) : null}
        </span>
        <span style={{ color: "var(--text-muted)", width: 34, flexShrink: 0, fontSize: 10 }}>
          {fmtTime(item.started_at)}
        </span>
        <CategoryBar breakdown={breakdown} totalDur={item.duration_sec} barWidth="90px" />
        <span style={{
          color: "var(--text-sub)", flexShrink: 0, width: 60,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 10,
        }}>{item.process_name.replace(".exe", "")}</span>
        <span style={{
          flex: 1, overflow: "hidden", textOverflow: "ellipsis",
          whiteSpace: "nowrap", color: "var(--text-sub)", fontSize: 11,
        }}>{item.window_title}</span>
        {merged.length > 1 && (
          <span style={{
            background: "var(--primary-light)", color: "var(--primary)",
            borderRadius: 8, padding: "0 5px", fontSize: 8, fontWeight: 600,
            flexShrink: 0, lineHeight: "14px",
          }}>{merged.length}</span>
        )}
        <span style={{ color: "var(--text-muted)", flexShrink: 0, fontSize: 10 }}>
          {fmtDur(item.duration_sec)}
        </span>
      </div>
      {expanded && merged.map((child, ci) => <ChildRow key={ci} item={child} />)}
    </div>
  );
}

/** 单段组（只有 1 个 segment 的组，扁平显示，不多嵌一层） */
function SingleSegmentGroupRow({ group, idx }: { group: ActivityGroup; idx: number }) {
  const [expanded, setExpanded] = useState(false);
  const item = group.segments[0];
  const children = item.children ?? [];
  const merged = useMemo(() => mergeChildren(children), [children]);
  const hasChildren = merged.length > 0;

  return (
    <div>
      <div
        onClick={() => hasChildren && setExpanded(!expanded)}
        style={{
          display: "flex", alignItems: "center", gap: 10, padding: "10px 16px",
          background: group.isCurrent
            ? "linear-gradient(90deg,rgba(6,182,212,0.06),transparent)"
            : idx % 2 === 0 ? "transparent" : "rgba(6,182,212,0.02)",
          borderBottom: expanded ? "none" : "1px solid var(--border)",
          borderLeft: group.isCurrent ? "3px solid var(--primary)" : "3px solid transparent",
          fontSize: 12, cursor: hasChildren ? "pointer" : "default",
          userSelect: "none",
        }}
      >
        <span style={{ width: 14, flexShrink: 0, color: "var(--text-muted)", fontSize: 10 }}>
          {hasChildren ? (expanded ? <DownOutlined /> : <RightOutlined />) : <span style={{ width: 14, display: "inline-block" }} />}
        </span>
        <span style={{ color: "var(--text-muted)", width: 36, flexShrink: 0 }}>
          {fmtTime(item.started_at)}
        </span>
        <CategoryBar breakdown={group.breakdown} totalDur={group.totalDuration} />
        <span style={{
          color: "var(--text-sub)", flexShrink: 0, width: 70,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>{item.process_name.replace(".exe", "")}</span>
        <span style={{
          flex: 1, overflow: "hidden", textOverflow: "ellipsis",
          whiteSpace: "nowrap", color: "var(--text)",
        }}>{item.window_title}</span>
        {merged.length > 1 && (
          <span style={{
            background: "var(--primary-light)", color: "var(--primary)",
            borderRadius: 10, padding: "0 6px", fontSize: 9, fontWeight: 600,
            flexShrink: 0, lineHeight: "16px",
          }}>{merged.length}</span>
        )}
        <span style={{
          color: group.isCurrent ? "var(--primary)" : "var(--text-sub)",
          flexShrink: 0, fontSize: 11,
          display: "flex", alignItems: "center", gap: 4,
        }}>
          {group.isCurrent && (
            <span style={{
              width: 6, height: 6, borderRadius: "50%",
              background: "var(--primary)", display: "inline-block",
              animation: "pulse 1.5s infinite",
            }} />
          )}
          {group.isCurrent ? `${fmtDur(group.totalDuration)} 进行中` : fmtDur(group.totalDuration)}
        </span>
      </div>
      {expanded && merged.map((child, ci) => <ChildRow key={ci} item={child} />)}
      {expanded && <div style={{ borderBottom: "1px solid var(--border)" }} />}
    </div>
  );
}

/** 多段组行（≥2 个 segment，展开显示段列表） */
function MultiSegmentGroupRow({ group, idx }: { group: ActivityGroup; idx: number }) {
  const [expanded, setExpanded] = useState(false);
  const timeRange = fmtTime(group.startedAt) === fmtTime(group.endedAt)
    ? fmtTime(group.startedAt)
    : `${fmtTime(group.startedAt)}-${fmtTime(group.endedAt)}`;

  // 组的描述文本：主要进程 + 次要进程
  const procDur: Record<string, number> = {};
  for (const seg of group.segments) {
    const p = seg.process_name.replace(".exe", "").toLowerCase();
    procDur[p] = (procDur[p] || 0) + seg.duration_sec;
  }
  const sortedProcs = Object.entries(procDur).sort((a, b) => b[1] - a[1]);
  const mainProc = sortedProcs[0]?.[0] ?? "";
  const otherProcs = sortedProcs.slice(1, 3).map(([p]) => p);
  const descText = otherProcs.length > 0
    ? `${mainProc} 为主` + (otherProcs.length > 0 ? ` + ${otherProcs.join(", ")}` : "")
    : mainProc;

  return (
    <div>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: "flex", alignItems: "center", gap: 10, padding: "10px 16px",
          background: group.isCurrent
            ? "linear-gradient(90deg,rgba(6,182,212,0.06),transparent)"
            : idx % 2 === 0 ? "transparent" : "rgba(6,182,212,0.02)",
          borderBottom: expanded ? "none" : "1px solid var(--border)",
          borderLeft: group.isCurrent ? "3px solid var(--primary)" : "3px solid transparent",
          fontSize: 12, cursor: "pointer", userSelect: "none",
        }}
      >
        <span style={{ width: 14, flexShrink: 0, color: "var(--text-muted)", fontSize: 10 }}>
          {expanded ? <DownOutlined /> : <RightOutlined />}
        </span>
        <span style={{ color: "var(--text-muted)", flexShrink: 0, whiteSpace: "nowrap" }}>
          {timeRange}
        </span>
        <CategoryBar breakdown={group.breakdown} totalDur={group.totalDuration} />
        <span style={{
          flex: 1, overflow: "hidden", textOverflow: "ellipsis",
          whiteSpace: "nowrap", color: "var(--text)",
        }}>{descText}</span>
        {/* 段数 badge */}
        <span style={{
          background: "var(--primary-light)", color: "var(--primary)",
          borderRadius: 10, padding: "0 6px", fontSize: 9, fontWeight: 600,
          flexShrink: 0, lineHeight: "16px",
        }}>{group.segmentCount}段</span>
        <span style={{
          color: group.isCurrent ? "var(--primary)" : "var(--text-sub)",
          flexShrink: 0, fontSize: 11,
          display: "flex", alignItems: "center", gap: 4,
        }}>
          {group.isCurrent && (
            <span style={{
              width: 6, height: 6, borderRadius: "50%",
              background: "var(--primary)", display: "inline-block",
              animation: "pulse 1.5s infinite",
            }} />
          )}
          {group.isCurrent ? `${fmtDur(group.totalDuration)} 进行中` : fmtDur(group.totalDuration)}
        </span>
      </div>
      {/* 展开：显示组内各段 */}
      {expanded && group.segments.map((seg, si) => (
        <SegmentRow key={si} item={seg} />
      ))}
      {expanded && <div style={{ borderBottom: "1px solid var(--border)" }} />}
    </div>
  );
}

/** 组行入口（自动选择单段/多段渲染） */
function GroupRow({ group, idx }: { group: ActivityGroup; idx: number }) {
  if (group.segmentCount === 1) {
    return <SingleSegmentGroupRow group={group} idx={idx} />;
  }
  return <MultiSegmentGroupRow group={group} idx={idx} />;
}

/* ── 主组件 ───────────────────────────────────────────────────────────── */

export function ActivityTab({ activities }: { activities: ActivityItem[] }) {
  const groups = useMemo(() => groupActivities(activities), [activities]);

  return (
    <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", overflow: "hidden" }}>
      <div style={{
        padding: "12px 16px", borderBottom: "1px solid var(--border)",
        fontSize: 12, color: "var(--text-sub)", display: "flex", alignItems: "center", gap: 6,
      }}>
        <UnorderedListOutlined style={{ color: "var(--primary)", fontSize: 14 }} />
        今日活动记录
        <span style={{
          background: "var(--primary-light)", color: "var(--primary)",
          borderRadius: 20, padding: "1px 8px", fontSize: 11, fontWeight: 600,
        }}>{activities.length}</span>
      </div>
      {groups.length === 0 ? (
        <div style={{ padding: 48, textAlign: "center" }}>
          <InboxOutlined style={{ fontSize: 36, color: "var(--text-muted)", marginBottom: 8, display: "block" }} />
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>暂无活动数据</div>
        </div>
      ) : (
        groups.map((g, i) => <GroupRow key={i} group={g} idx={i} />)
      )}
    </div>
  );
}