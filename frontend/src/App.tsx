/**
 * App.tsx — Navi 主入口（瘦壳）
 *
 * 所有 Tab 组件已拆分至 src/tabs/ 目录：
 *   StatusTab / ActivityTab / StudyTab / LlmTab / SoulTab / SettingsTab / TTSTab
 */
import { useState, useEffect, useCallback, useRef } from "react";
import "./App.css";
import { AnimatePresence, motion } from "framer-motion";
import { gsap } from "gsap";
import {
  MessageOutlined, BarChartOutlined, UnorderedListOutlined,
  RobotOutlined, LockOutlined, ExperimentOutlined, SettingOutlined,
  SoundOutlined, SunOutlined, MoonOutlined, MenuFoldOutlined,
  MenuUnfoldOutlined, PlusOutlined, EditOutlined, DeleteOutlined,
  BulbOutlined, WechatOutlined, ThunderboltOutlined,
} from "@ant-design/icons";

// ── Utils ──
import { API, fetchStatus, fetchActivities, fetchConfig } from "./utils/api";
import { getInitialTheme, applyTheme } from "./utils/theme";

// ── Tab 组件 ──
import { StatusCard, SummaryCard, ReportCard, CameraCard } from "./tabs/StatusTab";
import { Monitor, Image } from "lucide-react";
import { ActivityTab } from "./tabs/ActivityTab";
import { StudyTab } from "./tabs/StudyTab";
import { LlmTab } from "./tabs/LlmTab";
import { SoulTab } from "./tabs/SoulTab";
import { SettingsTab } from "./tabs/SettingsTab";
import { TTSTab } from "./pages/TTSTab";
import { Live2DPage } from "./pages/Live2DPage";
import { ChatPage } from "./pages/ChatPage";
import AppRulesTab from "./tabs/AppRulesTab";
import CapabilitiesTab from "./tabs/CapabilitiesTab";


export default function App() {
  const [tab, setTab] = useState<"status" | "activities" | "settings" | "study" | "llm" | "chat" | "live2d" | "soul" | "tts" | "app-rules" | "capabilities">("chat");
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [activities, setActivities] = useState<unknown[]>([]);
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [backendOk, setBackendOk] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(getInitialTheme);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const sidebarRef = useRef<HTMLElement>(null);

  // ── 初始化主题 + GSAP 入场 ──
  useEffect(() => {
    applyTheme(theme);
    if (sidebarRef.current) {
      gsap.fromTo(sidebarRef.current,
        { x: -20, opacity: 0 },
        { x: 0, opacity: 1, duration: 0.45, ease: "power3.out" }
      );
    }
  }, []);

  const toggleTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    applyTheme(next);
  };

  // ── 后端状态轮询 ──
  const refreshStatus = useCallback(async () => {
    try { const d = await fetchStatus(); setStatus(d); setBackendOk(true); }
    catch { setBackendOk(false); }
  }, []);

  useEffect(() => {
    refreshStatus();
    const t = setInterval(refreshStatus, 10000);
    return () => clearInterval(t);
  }, [refreshStatus]);

  // ── 活动列表 ──
  const refreshActivities = useCallback(() => {
    fetchActivities().then(setActivities).catch(() => setActivities([]));
  }, []);

  useEffect(() => {
    if (tab === "activities") {
      refreshActivities();
      const t = setInterval(refreshActivities, 30000);
      return () => clearInterval(t);
    }
    if (tab === "settings") fetchConfig().then(setConfig).catch(() => {});
  }, [tab, refreshActivities]);

  // ── 会话列表状态 ──
  const [sessions, setSessions] = useState<{id:string;title:string;last_preview:string|null;is_proactive?:boolean;is_wechat?:boolean}[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editSessionTitle, setEditSessionTitle] = useState("");

  const loadSessions = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/chat/sessions`);
      const data = await res.json();
      setSessions(data);
      return data as {id:string;title:string;last_preview:string|null;is_proactive?:boolean}[];
    } catch { return []; }
  }, []);

  useEffect(() => {
    if (tab === "chat") loadSessions().then(data => {
      if (!activeChatId && data.length > 0) setActiveChatId(data[0].id);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const createChatSession = async () => {
    try {
      const res = await fetch(`${API}/api/chat/sessions`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "新对话" }),
      });
      const s = await res.json();
      await loadSessions();
      setActiveChatId(s.id);
    } catch { /* ignore */ }
  };

  const deleteChatSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await fetch(`${API}/api/chat/sessions/${id}`, { method: "DELETE" });
      const data = await loadSessions();
      if (id === activeChatId) setActiveChatId(data.length > 0 ? data[0].id : null);
    } catch { /* ignore */ }
  };

  const renameChatSession = async (id: string, title: string) => {
    try {
      await fetch(`${API}/api/chat/sessions/${id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      await loadSessions();
    } catch { /* ignore */ }
  };

  // ── 导航配置 ──
  const TABS = [
    { key: "chat",         label: "对话",    icon: <MessageOutlined /> },
    { key: "status",       label: "今日状态", icon: <BarChartOutlined /> },
    { key: "activities",   label: "活动记录", icon: <UnorderedListOutlined /> },
    { key: "app-rules",    label: "应用规则", icon: <UnorderedListOutlined /> },
    { key: "capabilities", label: "功能配置", icon: <ThunderboltOutlined /> },
    { key: "live2d",       label: "桌宠伴侣", icon: <RobotOutlined /> },
    { key: "study",        label: "专注模式", icon: <LockOutlined /> },
    { key: "soul",         label: "灵魂",    icon: <ExperimentOutlined /> },
    { key: "tts",          label: "语音合成", icon: <SoundOutlined /> },
    { key: "llm",          label: "AI 模型",  icon: <RobotOutlined /> },
    { key: "settings",     label: "设置",    icon: <SettingOutlined /> },
  ] as const;

  const td = (status?.today as Record<string, unknown>) ?? {};

  return (
    <div className="navi-layout">

      {/* ===== Sidebar ===== */}
      <aside ref={sidebarRef} className={`navi-sidebar${sidebarCollapsed ? " collapsed" : ""}`}>
        <div className="navi-logo">
          <img src="/navi-logo.png" alt="Navi Logo" style={{
            width: 30, height: 30, borderRadius: 8, flexShrink: 0,
            objectFit: "cover",
          }} />
          <div className="navi-logo-text">
            <div className="navi-logo-name">Navi</div>
            <div className="navi-logo-sub">AI 桌宠助手</div>
          </div>
          <button className="sidebar-toggle" onClick={() => setSidebarCollapsed(true)} title="收起侧边栏">
            <MenuFoldOutlined style={{ fontSize: 13 }} />
          </button>
        </div>

        <div className="sidebar-expand-btn">
          <button className="sidebar-toggle" style={{ margin: 0 }} onClick={() => setSidebarCollapsed(false)} title="展开侧边栏">
            <MenuUnfoldOutlined style={{ fontSize: 13 }} />
          </button>
        </div>

        <nav className="navi-nav">
          {TABS.map((t) => (
            <div key={t.key}>
              <button
                className={`navi-nav-item${tab === t.key ? " active" : ""}`}
                onClick={() => setTab(t.key)}
                title={sidebarCollapsed ? t.label : undefined}
              >
                <span className="nav-icon">{t.icon}</span>
                <span className="nav-label">{t.label}</span>
              </button>

              {/* 对话 Tab 展开时：会话列表 */}
              {t.key === "chat" && tab === "chat" && !sidebarCollapsed && (
                <div className="nav-sessions">
                  <button className="nav-sessions-new" onClick={createChatSession}>
                    <PlusOutlined style={{ fontSize: 10 }} /> 新对话
                  </button>
                  {sessions.map(s => (
                    <div
                      key={s.id}
                      className={`nav-session-item${s.id === activeChatId ? " active" : ""}${s.is_proactive ? " proactive" : ""}${s.is_wechat ? " wechat" : ""}`}
                      onClick={() => setActiveChatId(s.id)}
                    >
                      {s.is_proactive ? (
                        /* 主动对话：固定样式，不可编辑/删除 */
                        <>
                          <BulbOutlined style={{ fontSize: 12, color: "var(--warning)", marginRight: 6, flexShrink: 0 }} />
                          <span className="nav-session-title">{s.title}</span>
                        </>
                      ) : s.is_wechat ? (
                        /* 微信对话：固定样式，不可编辑/删除 */
                        <>
                          <WechatOutlined style={{ fontSize: 12, color: "#07c160", marginRight: 6, flexShrink: 0 }} />
                          <span className="nav-session-title">{s.title}</span>
                        </>
                      ) : editingSessionId === s.id ? (
                        <input
                          autoFocus
                          className="nav-session-edit-input"
                          value={editSessionTitle}
                          onChange={e => setEditSessionTitle(e.target.value)}
                          onClick={e => e.stopPropagation()}
                          onKeyDown={e => {
                            if (e.key === "Enter") { renameChatSession(s.id, editSessionTitle); setEditingSessionId(null); }
                            if (e.key === "Escape") setEditingSessionId(null);
                          }}
                          onBlur={() => { renameChatSession(s.id, editSessionTitle); setEditingSessionId(null); }}
                        />
                      ) : (
                        <>
                          <span className="nav-session-title">{s.title}</span>
                          <span className="nav-session-actions">
                            <EditOutlined onClick={e => { e.stopPropagation(); setEditingSessionId(s.id); setEditSessionTitle(s.title); }} />
                            <DeleteOutlined onClick={e => deleteChatSession(s.id, e)} />
                          </span>
                        </>
                      )}
                    </div>
                  ))}
                  {sessions.length === 0 && (
                    <div className="nav-session-empty">暂无对话</div>
                  )}
                </div>
              )}
            </div>
          ))}
        </nav>

        <div className="navi-sidebar-footer">
          <div className="navi-nav-item" style={{ cursor: "default" }} title={sidebarCollapsed ? (backendOk ? "后端在线" : "后端未启动") : undefined}>
            <span className="nav-icon">
              <span className={`status-dot ${backendOk ? "online" : "offline"}`} />
            </span>
            <span className="nav-label" style={{ fontSize: 12, color: backendOk ? "var(--green)" : "var(--text-muted)" }}>
              {backendOk ? "后端在线" : "后端未启动"}
            </span>
          </div>
          <button className="navi-nav-item" onClick={toggleTheme} title={sidebarCollapsed ? (theme === "light" ? "切换暗色" : "切换亮色") : undefined}>
            <span className="nav-icon">
              {theme === "light" ? <MoonOutlined /> : <SunOutlined />}
            </span>
            <span className="nav-label">{theme === "light" ? "切换暗色" : "切换亮色"}</span>
          </button>
        </div>
      </aside>

      {/* ===== 主内容区 ===== */}
      <main className="navi-main">
        <AnimatePresence mode="wait">
        <motion.div
          key={tab}
          className={`navi-page${tab === "chat" ? "" : " with-padding"}`}
          style={tab === "chat" ? { height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" } : undefined}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
        >

        {tab === "status" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {backendOk && <ReportCard />}
            {!backendOk ? (
              <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: 48, textAlign: "center" }}>
                <div style={{ fontSize: 48, marginBottom: 12 }}></div>
                <div style={{ fontWeight: 700, fontSize: 15, color: "var(--text)", marginBottom: 8 }}>后端未启动</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>请运行：</div>
                <div style={{ display: "inline-block", marginTop: 8, background: "var(--primary-light)", color: "var(--primary)", borderRadius: 8, padding: "6px 14px", fontSize: 11, fontFamily: "monospace" }}>
                  uv run python main.py
                </div>
              </div>
            ) : (
              <>
                <div style={{ display: "flex", gap: 12 }}>
                  <StatusCard label="窗口采集" icon={<Monitor size={16} />}
                    running={(status?.window_capture as Record<string,unknown>)?.running as boolean ?? false}
                    count={(status?.window_capture as Record<string,unknown>)?.capture_count as number ?? 0}
                    todayTotal={(status?.window_capture as Record<string,unknown>)?.today_total as number ?? 0}
                    lastTime={(status?.window_capture as Record<string,unknown>)?.last_capture_time as string ?? null}
                    error={(status?.window_capture as Record<string,unknown>)?.last_error as string ?? null}
                  />
                  <StatusCard label="截图采集" icon={<Image size={16} />}
                    running={(status?.screenshot_capture as Record<string,unknown>)?.running as boolean ?? false}
                    count={(status?.screenshot_capture as Record<string,unknown>)?.capture_count as number ?? 0}
                    todayTotal={(status?.screenshot_capture as Record<string,unknown>)?.today_total as number ?? 0}
                    lastTime={(status?.screenshot_capture as Record<string,unknown>)?.last_capture_time as string ?? null}
                    error={(status?.screenshot_capture as Record<string,unknown>)?.last_error as string ?? null}
                  />
                </div>
                <CameraCard />
                <SummaryCard
                  total={td.total_segments as number ?? 0}
                  workMin={td.work_min as number ?? 0}
                  entertainMin={td.entertainment_min as number ?? 0}
                  screenshots={td.screenshot_count as number ?? 0}
                />
              </>
            )}
          </div>
        )}

        {tab === "chat" && <ChatPage sessionId={activeChatId} />}
        {tab === "study" && <StudyTab />}
        {tab === "live2d" && <Live2DPage />}
        {tab === "soul" && <SoulTab />}
        {tab === "tts" && <TTSTab />}
        {tab === "llm" && <LlmTab />}
        {tab === "activities" && <ActivityTab activities={activities as any[]} />}
        {tab === "app-rules" && <AppRulesTab />}
        {tab === "capabilities" && <CapabilitiesTab />}
        {tab === "settings" && config && <SettingsTab config={config} setConfig={setConfig} />}

        </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}
