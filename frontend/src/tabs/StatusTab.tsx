/**
 * StatusTab.tsx — 今日状态 Tab
 * 包含：StatusDot / StatusCard / SummaryCard / ReportCard / CameraCard
 */
import { useState, useEffect, useCallback } from "react";
import { API } from "../utils/api";
import { Camera, User, UserX, HelpCircle, FileText, RefreshCw } from "lucide-react";

export function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span style={{
      display: "inline-block", width: 8, height: 8, borderRadius: "50%",
      background: ok ? "var(--green)" : "var(--red)", marginRight: 6,
      boxShadow: ok ? `0 0 8px ${"var(--green)"}` : "none", flexShrink: 0,
    }} />
  );
}

export function StatusCard({ label, icon, count, todayTotal, lastTime, running, error }: {
  label: string; icon: React.ReactNode; count: number; todayTotal: number;
  lastTime: string | null; running: boolean; error: string | null;
}) {
  return (
    <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px", flex: 1 }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 12, gap: 8 }}>
        <span style={{
          width: 36, height: 36, borderRadius: 10,
          background: running ? "var(--primary-light)" : "var(--bg-card, #f3f4f6)",
          display: "flex", alignItems: "center", justifyContent: "center",
          color: running ? "var(--primary)" : "var(--text-muted)",
        }}>{icon}</span>
        <div>
          <div style={{ display: "flex", alignItems: "center" }}>
            <StatusDot ok={running} />
            <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text)" }}>{label}</span>
          </div>
          <div style={{ fontSize: 11, color: running ? "var(--green)" : "var(--red)", marginTop: 1 }}>
            {running ? "运行中" : "已停止"}
          </div>
        </div>
      </div>
      <div style={{ color: "var(--text-sub)", fontSize: 12, lineHeight: 2 }}>
        <div>
          今日合计
          <span style={{ color: "var(--text)", fontWeight: 700, float: "right" }}>{todayTotal}</span>
        </div>
        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
          本次启动
          <span style={{ float: "right" }}>{count}</span>
        </div>
        <div>最后采集 <span style={{ color: "var(--text)", fontWeight: 600, float: "right" }}>{lastTime ? lastTime.slice(11, 19) : "—"}</span></div>
        {error && <div style={{ color: "var(--red)", fontSize: 11, marginTop: 6, background: "var(--red-bg)", borderRadius: 6, padding: "4px 8px" }}>{error}</div>}
      </div>
    </div>
  );
}

export function SummaryCard({ total, workMin, entertainMin, screenshots }: {
  total: number; workMin: number; entertainMin: number; screenshots: number;
}) {
  const items = [
    { label: "活动段",   value: total,            color: "var(--primary)", bg: "var(--primary-light)", icon: "" },
    { label: "有效工作", value: `${workMin}min`,   color: "var(--green)",   bg: "var(--green-bg)",      icon: "" },
    { label: "娱乐时间", value: `${entertainMin}min`, color: "var(--red)", bg: "var(--red-bg)",         icon: "" },
    { label: "截图数量", value: screenshots,       color: "var(--blue)",    bg: "var(--blue-bg)",       icon: "" },
  ];
  return (
    <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
      <div style={{ fontWeight: 700, marginBottom: 14, fontSize: 13, color: "var(--text)" }}>今日摘要</div>
      <div style={{ display: "flex", gap: 10 }}>
        {items.map((it) => (
          <div key={it.label} style={{ flex: 1, background: it.bg, borderRadius: 12, padding: "12px 10px", textAlign: "center" }}>
            <div style={{ fontSize: 18, marginBottom: 4 }}>{it.icon}</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: it.color }}>{it.value}</div>
            <div style={{ fontSize: 11, color: "var(--text-sub)", marginTop: 2 }}>{it.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Emotion 中文映射 ── */
const EMOTION_CN: Record<string, string> = {
  happy: "开心", neutral: "平静", sad: "悲伤", angry: "生气",
  fear: "恐惧", surprise: "惊讶", disgust: "厌恶",
};

interface CameraStats {
  capture_count: number;
  error_count: number;
  last_capture_time: string | null;
}

interface EmotionSummary {
  dominant_emotion: string | null;
  distribution: Record<string, number>;
  trend: string | null;
}

export function CameraCard() {
  const [cameraEnabled, setCameraEnabled] = useState(false);
  const [cameraRunning, setCameraRunning] = useState(false);
  const [userPresent, setUserPresent] = useState(false);
  const [currentEmotion, setCurrentEmotion] = useState<string | null>(null);
  const [emotionConfidence, setEmotionConfidence] = useState(0);
  const [cameraStats, setCameraStats] = useState<CameraStats>({ capture_count: 0, error_count: 0, last_capture_time: null });
  const [emotionSummary, setEmotionSummary] = useState<EmotionSummary | null>(null);
  const [toggling, setToggling] = useState(false);

  /* 拉取摄像头状态 */
  const fetchCameraStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/camera/status`);
      if (!res.ok) return;
      const d = await res.json();
      const running = d.running ?? false;
      setCameraEnabled(running);      // switch 跟随实际运行状态
      setCameraRunning(running);
      // 兼容：优先从顶层取，fallback 到 stats 内部
      setUserPresent(d.user_present ?? d.stats?.user_present ?? false);
      setCurrentEmotion(d.current_emotion ?? d.stats?.current_emotion ?? null);
      setEmotionConfidence(d.current_confidence ?? d.stats?.emotion_confidence ?? 0);
      setCameraStats({
        capture_count: d.stats?.capture_count ?? 0,
        error_count: d.stats?.error_count ?? 0,
        last_capture_time: d.stats?.last_capture_time ?? null,
      });
    } catch { /* 后端不在线 */ }
  }, []);

  /* 拉取情绪历史摘要 */
  const fetchEmotionSummary = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/camera/emotions?hours=2`);
      if (!res.ok) return;
      const d = await res.json();
      setEmotionSummary(d.summary ?? null);
    } catch { /* ignore */ }
  }, []);

  /* 轮询 */
  useEffect(() => {
    fetchCameraStatus();
    fetchEmotionSummary();
    const t = setInterval(() => { fetchCameraStatus(); fetchEmotionSummary(); }, 5_000);
    return () => clearInterval(t);
  }, [fetchCameraStatus, fetchEmotionSummary]);

  /* 开关摄像头 */
  const toggleCamera = async (enabled: boolean) => {
    setToggling(true);
    try {
      await fetch(`${API}/api/camera/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      setCameraEnabled(enabled);
      await fetchCameraStatus();
    } catch { /* ignore */ }
    setToggling(false);
  };

  return (
    <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px", flex: 1 }}>
      {/* 标题行 + 开关 */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            fontSize: 18, width: 36, height: 36, borderRadius: 10,
            background: cameraRunning ? "var(--primary-light)" : "#f3f4f6",
            display: "flex", alignItems: "center", justifyContent: "center",
          color: cameraRunning ? "var(--primary)" : "var(--text-muted)",
          }}><Camera size={16} /></span>
          <div>
            <div style={{ display: "flex", alignItems: "center" }}>
              <StatusDot ok={cameraRunning} />
              <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text)" }}>摄像头</span>
            </div>
            <div style={{ fontSize: 11, color: cameraRunning ? "var(--green)" : "var(--red)", marginTop: 1 }}>
              {cameraRunning ? "运行中" : "已停止"}
            </div>
          </div>
        </div>

        {/* Toggle — 与 StudyTab 同款 */}
        <div
          onClick={() => !toggling && toggleCamera(!cameraEnabled)}
          style={{
            width: 52, height: 28, borderRadius: 14, cursor: toggling ? "not-allowed" : "pointer",
            background: cameraEnabled ? "var(--green)" : "var(--border)",
            position: "relative", transition: "background .2s", flexShrink: 0,
            opacity: toggling ? 0.6 : 1,
          }}
        >
          <div style={{
            position: "absolute", top: 3,
            left: cameraEnabled ? 26 : 3,
            width: 22, height: 22, borderRadius: "50%",
            background: "var(--bg-app)", transition: "left .2s",
            boxShadow: "0 1px 4px rgba(0,0,0,0.18)",
          }} />
        </div>
      </div>

      {/* 详细状态 */}
      <div style={{ color: "var(--text-sub)", fontSize: 12, lineHeight: 2 }}>
        <div>
          用户状态
          <span style={{ color: "var(--text)", fontWeight: 600, float: "right", display: "inline-flex", alignItems: "center", gap: 4 }}>
            {userPresent
              ? <><User size={12} color="var(--green)" />在电脑前</>
              : <><UserX size={12} color="var(--text-muted)" />已离开</>}
          </span>
        </div>
        <div>
          当前情绪
          <span style={{ color: "var(--text)", fontWeight: 700, float: "right" }}>
            {currentEmotion
              ? `${EMOTION_CN[currentEmotion] ?? currentEmotion} (${emotionConfidence.toFixed(2)})`
              : "—"}
          </span>
        </div>
        <div>
          采集次数
          <span style={{ color: "var(--text)", fontWeight: 700, float: "right" }}>{cameraStats.capture_count}</span>
        </div>
        {cameraStats.error_count > 0 && (
          <div>
            错误次数
            <span style={{ color: "var(--red)", fontWeight: 700, float: "right" }}>{cameraStats.error_count}</span>
          </div>
        )}
        <div>
          最后采集
          <span style={{ color: "var(--text)", fontWeight: 600, float: "right" }}>
            {cameraStats.last_capture_time ? cameraStats.last_capture_time.slice(11, 19) : "—"}
          </span>
        </div>
      </div>

      {/* 情绪历史摘要 */}
      {emotionSummary?.dominant_emotion && (
        <div style={{
          marginTop: 10, fontSize: 11, padding: "6px 12px", borderRadius: 8,
          background: "var(--primary-light)", color: "var(--text-sub)", lineHeight: 1.6,
        }}>
          <span style={{ fontWeight: 600, color: "var(--text)" }}>近 2h 情绪：</span>
          {EMOTION_CN[emotionSummary.dominant_emotion] ?? emotionSummary.dominant_emotion}
          {emotionSummary.distribution && Object.keys(emotionSummary.distribution).length > 0 && (
            <span style={{ marginLeft: 6 }}>
              ({Object.entries(emotionSummary.distribution)
                .sort(([, a], [, b]) => (b as number) - (a as number))
                .slice(0, 3)
                .map(([e, v]) => `${EMOTION_CN[e] ?? e} ${((v as number) * 100).toFixed(0)}%`)
                .join("  ")})
            </span>
          )}
          {emotionSummary.trend && <span style={{ marginLeft: 6, color: "var(--text-muted)" }}>趋势: {emotionSummary.trend}</span>}
        </div>
      )}
    </div>
  );
}

export function ReportCard() {
  // status 状态由后端 SSOT 决定，避免 Tab 切换 unmount 丢状态
  // 'idle'      — 未生成，按钮可点
  // 'generating'— 正在生成（loading 转圈）
  // 'waiting'   — 后端有 pending 追问，按钮变"去聊天回答"
  // 'done'      — 已生成
  type ReportState = "idle" | "generating" | "waiting" | "done";
  const [state, setState] = useState<ReportState>("idle");
  const [msg, setMsg]   = useState("");
  const [msgOk, setMsgOk] = useState(true);
  const [pendingQuestion, setPendingQuestion] = useState<string>("");
  const HINTS = ["分析截图中 ", "理解活动数据 ", "检索历史记忆 ", "撰写日报 "];
  const [, setHintIdx] = useState(0);

  // 挂载时从后端拉真实状态（SSOT），避免 Tab 切回来按钮变回 idle
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const r = await fetch(`${API}/api/report/status`);
        const d = await r.json();
        if (cancelled) return;
        if (d.status === "done") {
          setState("done");
          setMsg("今日日报已生成");
          setMsgOk(true);
        } else if (d.status === "waiting_answer") {
          setState("waiting");
          setPendingQuestion(d.question || "");
          setMsg(`Navi 想问你一个问题，去【聊天】里回答：${(d.question || "").slice(0, 40)}...`);
          setMsgOk(true);
        } else {
          setState("idle");
          setMsg("");
        }
      } catch {
        // 拿不到状态就保持 idle，不打扰用户
      }
    };
    refresh();

    // 监听 WebSocket 推送，实时刷新状态（不用等 Tab 切回来）
    // 用专门的 chat_id 让 broadcaster 把全局事件推给我们（不会污染聊天会话）
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`ws://localhost:8000/ws/chat?chat_id=status_tab_observer`);
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === "navi:report_inquiry") {
            setState("waiting");
            setPendingQuestion(data.question || "");
            setMsg(`Navi 在聊天里问你了：${(data.question || "").slice(0, 40)}...`);
            setMsgOk(true);
          } else if (data.type === "navi:report_done") {
            setState("done");
            setMsg("今日日报已生成");
            setMsgOk(true);
          }
        } catch {
          // ignore
        }
      };
    } catch {
      // events ws 不存在也不影响主流程
    }

    return () => {
      cancelled = true;
      ws?.close();
    };
  }, []);

  const handleGenerate = async () => {
    // waiting 状态点按钮：相当于切去 ChatPage 提示用户（暂时只显示文字，不真切 tab）
    if (state === "waiting") {
      setMsg(`请去【聊天】回答 Navi 的问题：${pendingQuestion.slice(0, 60)}`);
      setMsgOk(true);
      return;
    }

    setState("generating");
    setMsg(HINTS[0]);
    setMsgOk(true);

    let idx = 0;
    const timer = setInterval(() => {
      idx = (idx + 1) % HINTS.length;
      setHintIdx(idx);
      setMsg(HINTS[idx]);
    }, 5000);

    try {
      const res  = await fetch(`${API}/api/report/generate`, { method: "POST" });
      const data = await res.json();
      clearInterval(timer);
      if (res.ok && data.status === "done") {
        setState("done");
        setMsg(`[日报生成完成]，Vision 处理 ${data.screenshot_count} 张截图`);
        setMsgOk(true);
      } else if (res.ok && data.status === "waiting_answer") {
        // 后端检测到 gap 进入追问等待 — 按钮放回，引导用户去聊天
        setState("waiting");
        setPendingQuestion(data.question || "");
        setMsg(`Navi 想问你一个问题，去【聊天】里回答：${(data.question || "").slice(0, 40)}...`);
        setMsgOk(true);
      } else {
        setState("idle");
        setMsg(`${data.message ?? "生成失败，请查看后端控制台"}`);
        setMsgOk(false);
      }
    } catch {
      clearInterval(timer);
      setState("idle");
      setMsg("请求失败，请检查后端是否在线");
      setMsgOk(false);
    }
    // done 状态保留 8 秒后清空提示，但不回退 state
    if (state !== "waiting") {
      setTimeout(() => setMsg(""), 8000);
    }
  };

  const generating = state === "generating";
  const waiting = state === "waiting";
  const btnLabel = generating ? "生成中..." : waiting ? "等你回答中" : state === "done" ? "重新生成" : "生成日报";
  const btnDisabled = generating;

  return (
    <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "14px 20px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)" }}>今日日报</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            {waiting ? "Navi 在聊天里问你了，回答后会自动生成" : "生成后自动写入 Obsidian 目录"}
          </div>
        </div>
        <button onClick={handleGenerate} disabled={btnDisabled} style={{
          background: btnDisabled ? "var(--bg-card, #e5e7eb)" : waiting ? "#d97706" : "var(--accent, #0891b2)",
          color: btnDisabled ? "var(--text-muted)" : "#fff", border: "none",
          borderRadius: 10, padding: "8px 18px", fontWeight: 600, fontSize: 12,
          cursor: btnDisabled ? "not-allowed" : "pointer",
          display: "flex", alignItems: "center", gap: 6,
        }}>
          <RefreshCw size={13} style={{ animation: generating ? "spin 0.8s linear infinite" : "none" }} />
          {btnLabel}
        </button>
      </div>
      {msg && (
        <div style={{
          marginTop: 10, fontSize: 11, padding: "6px 12px", borderRadius: 8,
          background: msgOk ? "var(--green-bg)" : "var(--red-bg)",
          color: msgOk ? "var(--green)" : "var(--red)",
        }}>
          {generating && "加载中 "}{msg}
        </div>
      )}
    </div>
  );
}