/**
 * SettingsTab.tsx — 采集 & 日报 & 主动对话 配置 Tab
 */
import { useState, useEffect } from "react";
import { saveConfig, fetchProactiveConfig, saveProactiveConfig, fetchProactiveStatus, testProactiveTrigger } from "../utils/api";
import { Lightbulb, Timer, Target, MoonStar, MessageSquare, FlaskConical, Shuffle, Camera, CheckCircle, XCircle, Circle, Save } from "lucide-react";

function ConfigField({ label, desc, value, onChange, type = "number", min, max, step }: {
  label: string; desc: string; value: string | number;
  onChange: (v: string) => void; type?: string; min?: number; max?: number; step?: number;
}) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontWeight: 600, marginBottom: 3, fontSize: 13, color: "var(--text)" }}>{label}</div>
      <div style={{ color: "var(--text-muted)", fontSize: 11, marginBottom: 8 }}>{desc}</div>
      <input type={type} value={value} min={min} max={max} step={step} onChange={(e) => onChange(e.target.value)}
        style={{
          background: "var(--bg-app)", border: `1.5px solid ${"var(--border)"}`,
          borderRadius: 10, padding: "8px 14px", color: "var(--text)",
          fontSize: 13, width: type === "text" ? "100%" : 140, outline: "none", boxSizing: "border-box",
        }}
      />
    </div>
  );
}

// 小节标题组件
function SectionTitle({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 12, color: "var(--text)", display: "flex", alignItems: "center", gap: 6 }}>
      <span style={{ display: "flex", alignItems: "center", color: "var(--text-muted)" }}>{icon}</span>
      {label}
    </div>
  );
}

function ProactiveConfigSection() {
  const [cfg, setCfg] = useState<Record<string, any> | null>(null);
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [saveMsg, setSaveMsg] = useState("");
  const [testMsg, setTestMsg] = useState("");

  useEffect(() => {
    fetchProactiveConfig().then(setCfg).catch(() => setCfg(null));
    fetchProactiveStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  if (!cfg) {
    return (
      <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
        <div style={{ fontWeight: 700, marginBottom: 12, fontSize: 13, color: "var(--text)", display: "flex", alignItems: "center", gap: 6 }}>
          <Lightbulb size={14} /> 主动对话
        </div>
        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
          加载中...（如果长时间显示此消息，说明 ProactiveEngine 未启动）
        </div>
      </div>
    );
  }

  const update = (key: string, val: number) => {
    setCfg({ ...cfg, [key]: val });
  };

  const handleSave = async () => {
    try {
      const res = await saveProactiveConfig(cfg);
      if (res?.ok) {
        setSaveMsg(`已保存，${res.updated?.length ?? 0} 项即时生效`);
      } else {
        setSaveMsg("保存失败");
      }
      setTimeout(() => setSaveMsg(""), 4000);
    } catch {
      setSaveMsg("保存失败（网络错误）");
    }
  };

  const handleTest = async () => {
    setTestMsg("发送中...");
    try {
      const res = await testProactiveTrigger();
      setTestMsg(res?.ok ? "测试消息已发送，查看主动对话会话" : `${res?.error || "失败"}`);
    } catch {
      setTestMsg("请求失败");
    }
    setTimeout(() => setTestMsg(""), 5000);
  };

  return (
    <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
      <div style={{ fontWeight: 700, marginBottom: 4, fontSize: 13, color: "var(--text)", display: "flex", alignItems: "center", gap: 6 }}>
        <Lightbulb size={14} /> 主动对话
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: 11, marginBottom: 16 }}>
        控制 Navi 主动找你说话的频率和时机。参数修改即时生效，无需重启。
      </div>

      {/* 引擎状态 */}
      {status && (
        <div style={{
          background: status.running ? "rgba(16,185,129,0.08)" : "rgba(239,68,68,0.08)",
          border: `1px solid ${status.running ? "rgba(16,185,129,0.25)" : "rgba(239,68,68,0.25)"}`,
          borderRadius: 8, padding: "8px 14px", marginBottom: 18, fontSize: 12,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          {status.running
            ? <Circle size={10} fill="#10b981" color="#10b981" />
            : <Circle size={10} fill="#ef4444" color="#ef4444" />}
          <span style={{ color: "var(--text)" }}>
            {status.running
              ? `引擎运行中 · 今日已主动 ${status.gate?.today_count ?? 0}/${status.gate?.max_daily ?? 8} 次 · 待反馈 ${status.pending_feedback ?? 0} 条`
              : `引擎未运行${status.error ? `：${status.error}` : ""}`}
          </span>
        </div>
      )}

      {/* 频率控制 */}
      <div style={{ borderBottom: "1px solid var(--border)", paddingBottom: 14, marginBottom: 14 }}>
        <SectionTitle icon={<Timer size={13} />} label="频率控制" />
        <ConfigField
          label="发言最小间隔（分钟）"
          desc="两次主动发言之间至少间隔多久。降低此值可让 Navi 更频繁地主动说话。默认 30 分钟"
          value={cfg.gate_min_interval_min ?? 30} min={1} max={120}
          onChange={(v) => update("gate_min_interval_min", parseInt(v))}
        />
        <ConfigField
          label="每日发言上限（次）"
          desc="每天最多主动说话多少次。增大此值可让 Navi 一天内说更多话。默认 8 次"
          value={cfg.gate_max_daily_proactive ?? 8} min={1} max={50}
          onChange={(v) => update("gate_max_daily_proactive", parseInt(v))}
        />
        <ConfigField
          label="巡检间隔（秒）"
          desc="后台每隔多少秒检查一次是否需要主动说话。降低此值可更快响应。默认 120 秒"
          value={cfg.patrol_interval_sec ?? 120} min={30} max={600}
          onChange={(v) => update("patrol_interval_sec", parseInt(v))}
        />
      </div>

      {/* 触发阈值 */}
      <div style={{ borderBottom: "1px solid var(--border)", paddingBottom: 14, marginBottom: 14 }}>
        <SectionTitle icon={<Target size={13} />} label="触发条件" />
        <ConfigField
          label="紧急度基础阈值"
          desc="低于此值的触发会被直接拒绝（0.1~0.9）。降低此值 = 更容易触发主动对话。默认 0.2"
          value={cfg.gate_base_threshold ?? 0.2} min={0.1} max={0.9} step={0.05}
          onChange={(v) => update("gate_base_threshold", parseFloat(v))}
        />
        <ConfigField
          label="连续工作提醒（分钟）"
          desc="连续工作超过多少分钟后提醒休息。默认 120 分钟"
          value={cfg.long_work_threshold_min ?? 120} min={15} max={480}
          onChange={(v) => update("long_work_threshold_min", parseInt(v))}
        />
        <ConfigField
          label="空闲触发（分钟）"
          desc="多少分钟无活动后主动关心。默认 30 分钟"
          value={cfg.idle_threshold_min ?? 30} min={5} max={120}
          onChange={(v) => update("idle_threshold_min", parseInt(v))}
        />
        <ConfigField
          label="无互动触发（小时）"
          desc="多少小时没聊天后主动搭话。默认 6 小时"
          value={cfg.periodic_chat_threshold_hours ?? 6} min={1} max={24}
          onChange={(v) => update("periodic_chat_threshold_hours", parseInt(v))}
        />
      </div>

      {/* 安静时段 */}
      <div style={{ borderBottom: "1px solid var(--border)", paddingBottom: 14, marginBottom: 14 }}>
        <SectionTitle icon={<MoonStar size={13} />} label="安静时段" />
        <div style={{ display: "flex", gap: 16, alignItems: "flex-end" }}>
          <ConfigField
            label="开始时间"
            desc="从几点开始不主动说话（24h制）"
            value={cfg.gate_quiet_hours_start ?? 0} min={0} max={23}
            onChange={(v) => update("gate_quiet_hours_start", parseInt(v))}
          />
          <ConfigField
            label="结束时间"
            desc="到几点恢复主动说话（24h制）"
            value={cfg.gate_quiet_hours_end ?? 7} min={0} max={23}
            onChange={(v) => update("gate_quiet_hours_end", parseInt(v))}
          />
        </div>
        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
          当前设置：{cfg.gate_quiet_hours_start ?? 0}:00 ~ {cfg.gate_quiet_hours_end ?? 7}:00 不主动说话。设为 0 ~ 0 可关闭安静时段。
        </div>
      </div>

      {/* 反馈 */}
      <div style={{ marginBottom: 14 }}>
        <SectionTitle icon={<MessageSquare size={13} />} label="反馈与静默" />
        <ConfigField
          label="无视超时（秒）"
          desc="主动消息发出后多久没回复算'被无视'，会微调阈值。默认 300 秒"
          value={cfg.feedback_ignore_timeout_sec ?? 300} min={60} max={1800}
          onChange={(v) => update("feedback_ignore_timeout_sec", parseInt(v))}
        />
        <ConfigField
          label="拒绝后静默时长（小时）"
          desc="用户说'别烦我'后静默多久。默认 2 小时"
          value={cfg.silent_mode_default_hours ?? 2} min={0.5} max={12} step={0.5}
          onChange={(v) => update("silent_mode_default_hours", parseFloat(v))}
        />
      </div>

      {/* 操作按钮 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <button onClick={handleSave} style={{
          background: "var(--accent, #7c3aed)", color: "#fff", border: "none",
          borderRadius: 10, padding: "9px 22px", fontWeight: 600, fontSize: 13, cursor: "pointer",
          display: "flex", alignItems: "center", gap: 6,
        }}>
          <Save size={13} />保存主动对话配置
        </button>
        <button onClick={handleTest} style={{
          background: "var(--bg-app)", color: "var(--text)", border: "1.5px solid var(--border)",
          borderRadius: 10, padding: "9px 18px", fontWeight: 600, fontSize: 12, cursor: "pointer",
          display: "flex", alignItems: "center", gap: 6,
        }}>
          <FlaskConical size={13} />测试触发
        </button>
        {saveMsg && (
          <span style={{ fontSize: 12, color: "var(--text-sub)", background: "var(--bg-app)", borderRadius: 8, padding: "4px 12px", border: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 5 }}>
            <CheckCircle size={12} color="#10b981" />{saveMsg}
          </span>
        )}
        {testMsg && (
          <span style={{ fontSize: 12, color: "var(--text-sub)", background: "var(--bg-app)", borderRadius: 8, padding: "4px 12px", border: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 5 }}>
            {testMsg.includes("失败") || testMsg.includes("请求")
              ? <XCircle size={12} color="#ef4444" />
              : <CheckCircle size={12} color="#10b981" />}
            {testMsg}
          </span>
        )}
      </div>
    </div>
  );
}

export function SettingsTab({ config, setConfig }: {
  config: Record<string, unknown>;
  setConfig: (c: Record<string, unknown>) => void;
}) {
  const [saveMsg, setSaveMsg] = useState("");

  const handleSave = async () => {
    try {
      const res = await saveConfig(config);
      const reloaded: string[] = res?.hot_reloaded ?? [];
      if (reloaded.length > 0) {
        setSaveMsg(`已保存并即时生效（${reloaded.join("、")}已热重载）`);
      } else {
        setSaveMsg("已保存，重启采集器后生效");
      }
      setTimeout(() => setSaveMsg(""), 4000);
    }
    catch { setSaveMsg("保存失败"); }
  };

  const cc = (config?.collector as Record<string, unknown>) ?? {};

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* ── 主动对话配置（最重要，放最前面）── */}
      <ProactiveConfigSection />

      <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
        <div style={{ fontWeight: 700, marginBottom: 16, fontSize: 13, color: "var(--text)" }}>采集配置</div>
        <ConfigField label="窗口采集间隔（秒）" desc="每隔多少秒记录一次活跃窗口，默认60秒" value={cc.window_interval_sec as number ?? 60} min={10} max={300}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, window_interval_sec: parseInt(v) } })} />
        <ConfigField label="截图采集间隔（秒）" desc="每隔多少秒尝试截一次屏，默认30秒" value={cc.screenshot_interval_sec as number ?? 30} min={10} max={600}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, screenshot_interval_sec: parseInt(v) } })} />
        <ConfigField label="截图去重阈值" desc="Hamming距离，越小存越多，越大存越少，默认10" value={cc.screenshot_similarity as number ?? 10} min={0} max={64}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, screenshot_similarity: parseInt(v) } })} />
        <ConfigField label="截图质量（JPG）" desc="1~100，越高越清晰文件越大，默认75" value={cc.screenshot_quality as number ?? 75} min={1} max={100}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, screenshot_quality: parseInt(v) } })} />
        <ConfigField label="空闲判定阈值（秒）" desc="多少秒无键鼠输入视为发呆/空闲，该时间段不计入工作时长，默认300秒（5分钟）" value={cc.idle_threshold_sec as number ?? 300} min={30} max={3600}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, idle_threshold_sec: parseInt(v) } })} />

        {/* ── 活动段切换配置 ── */}
        <div style={{ borderTop: "1px solid var(--border)", margin: "18px 0 14px", paddingTop: 14 }}>
          <SectionTitle icon={<Shuffle size={13} />} label="活动大段切换" />
        </div>
        <ConfigField label="回看窗口（分钟）" desc="检查最近 N 分钟内的活动分类，判断是否切换大段，默认2分钟" value={cc.segment_lookback_min as number ?? 2} min={1} max={10}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, segment_lookback_min: parseFloat(v) } })} />
        <ConfigField label="切换阈值（分钟）" desc="如果不同分类在回看窗口内累计超过此时长，触发大段切换，默认1分钟" value={cc.segment_switch_threshold_min as number ?? 1} min={0.5} max={5}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, segment_switch_threshold_min: parseFloat(v) } })} />

        {/* ── 摄像头配置 ── */}
        <div style={{ borderTop: "1px solid var(--border)", margin: "18px 0 14px", paddingTop: 14 }}>
          <SectionTitle icon={<Camera size={13} />} label="摄像头（人脸检测 + 情绪识别）" />
        </div>
        <ConfigField label="摄像头设备索引" desc="0 = 默认摄像头，如有多个摄像头可切换 1、2..." value={cc.camera_index as number ?? 0} min={0} max={5}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, camera_index: parseInt(v) } })} />
        <ConfigField label="人脸检测间隔（秒）" desc="每隔多少秒检测一次人脸是否在电脑前，默认5秒" value={cc.camera_interval_sec as number ?? 5} min={2} max={30}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, camera_interval_sec: parseInt(v) } })} />
        <ConfigField label="情绪识别频率（N次）" desc="每 N 次人脸检测做 1 次情绪识别，默认6（即每30秒）" value={cc.emotion_every_n as number ?? 6} min={1} max={30}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, emotion_every_n: parseInt(v) } })} />
        <ConfigField label="人脸检测置信度" desc="0.0~1.0，越高越严格（误检少但漏检多），默认0.7" value={cc.camera_confidence_threshold as number ?? 0.7} min={0.3} max={1.0}
          onChange={(v) => setConfig({ ...config, collector: { ...cc, camera_confidence_threshold: parseFloat(v) } })} />
      </div>
      <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
        <div style={{ fontWeight: 700, marginBottom: 16, fontSize: 13, color: "var(--text)" }}>日报配置</div>
        <ConfigField label="Obsidian 输出目录" desc="日报 .md 文件保存位置，例如 D:\obsidian\diary"
          value={(config?.reports as Record<string,unknown>)?.output_dir as string ?? ""} type="text"
          onChange={(v) => setConfig({ ...config, reports: { ...(config.reports as object), output_dir: v } })} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button onClick={handleSave} style={{
          background: "var(--accent, #0891b2)", color: "#fff", border: "none",
          borderRadius: 10, padding: "9px 22px", fontWeight: 600, fontSize: 13, cursor: "pointer",
          display: "flex", alignItems: "center", gap: 6,
        }}>
          <Save size={13} />保存采集配置
        </button>
        {saveMsg && (
          <span style={{ fontSize: 12, color: "var(--text-sub)", background: "var(--bg-app)", borderRadius: 8, padding: "4px 12px", border: `1px solid ${"var(--border)"}`, display: "flex", alignItems: "center", gap: 5 }}>
            <CheckCircle size={12} color="#10b981" />{saveMsg}
          </span>
        )}
      </div>
    </div>
  );
}