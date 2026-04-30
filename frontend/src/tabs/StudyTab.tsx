/**
 * StudyTab.tsx — 专注模式 / 学习模式 Tab
 */
import { useState, useCallback, useEffect, useRef } from "react";
import {
  LockOutlined, StopOutlined, PlusOutlined,
  DesktopOutlined, ReloadOutlined, SyncOutlined, CheckOutlined,
} from "@ant-design/icons";
import { API } from "../utils/api";

type StudyStatus = {
  study_mode: boolean;
  blacklist: string[];
  recent_kills: { process: string; success: boolean; time: string }[];
};

export function StudyTab() {
  const [studyStatus, setStudyStatus] = useState<StudyStatus | null>(null);
  const [newProc, setNewProc] = useState("");
  const [msg, setMsg] = useState("");
  const [allProcs, setAllProcs] = useState<string[]>([]);
  const [procsLoading, setProcsLoading] = useState(false);
  const [showProcPanel, setShowProcPanel] = useState(false);
  const [procSearch, setProcSearch] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => {
    fetch(`${API}/api/study/status`).then(r => r.json()).then(setStudyStatus).catch(() => {});
  }, []);

  const fetchProcs = useCallback(async () => {
    setProcsLoading(true);
    try {
      const r = await fetch(`${API}/api/processes`);
      const d = await r.json();
      setAllProcs(d.processes ?? []);
    } catch {}
    setProcsLoading(false);
  }, []);

  useEffect(() => { reload(); const t = setInterval(reload, 5000); return () => clearInterval(t); }, [reload]);

  const toggleStudy = async (v: boolean) => {
    await fetch(`${API}/api/study/toggle`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: v }) });
    reload();
  };

  const addBlacklist = async (procName?: string) => {
    const proc = (procName ?? newProc).trim();
    if (!proc) return;
    await fetch(`${API}/api/blacklist`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ process_name: proc }) });
    setNewProc("");
    setSuggestions([]);
    setShowSuggestions(false);
    setMsg(`[已添加] ${proc}`);
    setTimeout(() => setMsg(""), 2500);
    reload();
  };

  const handleInputChange = (v: string) => {
    setNewProc(v);
    if (v.trim().length >= 1 && allProcs.length > 0) {
      const kw = v.toLowerCase();
      setSuggestions(allProcs.filter(p => p.toLowerCase().includes(kw)).slice(0, 8));
      setShowSuggestions(true);
    } else {
      setSuggestions([]);
      setShowSuggestions(false);
    }
  };

  const removeBlacklist = async (proc: string) => {
    await fetch(`${API}/api/blacklist/${encodeURIComponent(proc)}`, { method: "DELETE" });
    setMsg(`已移除 ${proc}`);
    setTimeout(() => setMsg(""), 2500);
    reload();
  };

  const isOn = studyStatus?.study_mode ?? false;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* 大开关 */}
      <div className="navi-card" style={{ padding: "22px 28px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, color: "var(--text)", display: "flex", alignItems: "center", gap: 8 }}>
              <LockOutlined /> 学习模式
            </div>
            <div style={{ fontSize: 12, color: "var(--text-sub)", marginTop: 4 }}>
              开启后，黑名单中的进程一旦运行将被立即终止
            </div>
          </div>
          <div
            onClick={() => toggleStudy(!isOn)}
            style={{
              width: 52, height: 28, borderRadius: 14, cursor: "pointer",
              background: isOn ? "var(--green)" : "var(--border)",
              position: "relative", transition: "background .2s", flexShrink: 0,
            }}
          >
            <div style={{
              position: "absolute", top: 3,
              left: isOn ? 26 : 3,
              width: 22, height: 22, borderRadius: "50%",
              background: "var(--bg-app)", transition: "left .2s",
              boxShadow: "0 1px 4px rgba(0,0,0,0.18)",
            }} />
          </div>
        </div>
        <div style={{
          marginTop: 14, fontSize: 12, padding: "8px 14px", borderRadius: "var(--r-md)",
          background: isOn ? "var(--green-bg)" : "var(--yellow-bg)",
          color: isOn ? "var(--green)" : "var(--yellow)",
          fontWeight: 500,
        }}>
          {isOn ? "学习模式已开启 — 黑名单进程将被自动终止" : "学习模式已关闭 — 黑名单仅记录，不做干预"}
        </div>
      </div>

      {/* 黑名单管理 */}
      <div className="navi-card" style={{ padding: "20px 24px" }}>
        <div style={{ fontWeight: 600, marginBottom: 16, fontSize: 13, color: "var(--text)", display: "flex", alignItems: "center", gap: 7 }}>
          <StopOutlined style={{ color: "var(--red)" }} /> 黑名单进程
        </div>

        <div style={{ display: "flex", gap: 8, marginBottom: 6, position: "relative" }}>
          <div style={{ flex: 1, position: "relative" }}>
            <input
              ref={inputRef}
              value={newProc}
              onChange={e => handleInputChange(e.target.value)}
              onFocus={() => { if (allProcs.length === 0) fetchProcs(); if (suggestions.length > 0) setShowSuggestions(true); }}
              onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
              onKeyDown={e => e.key === "Enter" && addBlacklist()}
              placeholder="输入进程名，如 bilibili.exe"
              className="navi-input"
              style={{ fontSize: 13 }}
            />
            {showSuggestions && suggestions.length > 0 && (
              <div style={{
                position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 99,
                background: "var(--bg-card)", border: "1px solid var(--border)",
                borderRadius: "var(--r-md)", boxShadow: "var(--shadow-md)", overflow: "hidden",
              }}>
                {suggestions.map(p => (
                  <div key={p} onMouseDown={() => addBlacklist(p)} style={{
                    padding: "8px 14px", fontSize: 12, cursor: "pointer",
                    color: "var(--text)", fontFamily: "monospace",
                    borderBottom: "1px solid var(--border)", transition: "background 0.1s",
                  }}
                    onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                  >{p}</div>
                ))}
              </div>
            )}
          </div>

          <button
            onClick={() => { if (!showProcPanel) { fetchProcs(); setProcSearch(""); } setShowProcPanel(v => !v); }}
            title="从当前运行进程中选择"
            className="navi-btn"
            style={{ flexShrink: 0, background: showProcPanel ? "var(--primary-btn)" : undefined, color: showProcPanel ? "var(--text-inverse)" : undefined }}
          >
            <DesktopOutlined />
          </button>

          <button onClick={() => addBlacklist()} className="navi-btn navi-btn-primary" style={{ flexShrink: 0 }}>
            <PlusOutlined /> 添加
          </button>
        </div>

        {/* 进程选择面板 */}
        {showProcPanel && (
          <div style={{
            marginBottom: 14, border: "1px solid var(--border)", borderRadius: "var(--r-lg)",
            background: "var(--bg-card)", overflow: "hidden",
          }}>
            <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", gap: 8, alignItems: "center" }}>
              <ReloadOutlined style={{ fontSize: 12, color: "var(--text-muted)", flexShrink: 0 }} />
              <input autoFocus value={procSearch} onChange={e => setProcSearch(e.target.value)}
                placeholder="搜索进程名..." style={{ flex: 1, border: "none", outline: "none", background: "transparent", fontSize: 12, color: "var(--text)", fontFamily: "inherit" }}
              />
              <button onClick={() => fetchProcs()} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)", display: "flex", alignItems: "center" }}>
                <SyncOutlined spin={procsLoading} style={{ fontSize: 13 }} />
              </button>
            </div>
            <div style={{ maxHeight: 220, overflowY: "auto" }}>
              {procsLoading ? (
                <div style={{ textAlign: "center", padding: 20, fontSize: 12, color: "var(--text-muted)" }}>加载中...</div>
              ) : allProcs.filter(p => p.toLowerCase().includes(procSearch.toLowerCase())).length === 0 ? (
                <div style={{ textAlign: "center", padding: 20, fontSize: 12, color: "var(--text-muted)" }}>没有匹配的进程</div>
              ) : (
                allProcs.filter(p => p.toLowerCase().includes(procSearch.toLowerCase())).map(p => {
                  const inBl = studyStatus?.blacklist?.includes(p);
                  return (
                    <div key={p} onClick={() => !inBl && addBlacklist(p)} style={{
                      display: "flex", alignItems: "center", justifyContent: "space-between",
                      padding: "7px 14px", fontSize: 12, cursor: inBl ? "default" : "pointer",
                      borderBottom: "1px solid var(--border)",
                      background: inBl ? "var(--red-bg)" : "transparent",
                      opacity: inBl ? 0.7 : 1, transition: "background 0.1s",
                    }}
                      onMouseEnter={e => { if (!inBl) e.currentTarget.style.background = "var(--bg-hover)"; }}
                      onMouseLeave={e => { if (!inBl) e.currentTarget.style.background = inBl ? "var(--red-bg)" : "transparent"; }}
                    >
                      <span style={{ fontFamily: "monospace", color: inBl ? "var(--red)" : "var(--text)" }}>{p}</span>
                      {inBl
                        ? <span style={{ fontSize: 10, color: "var(--red)", fontWeight: 600 }}>已加入</span>
                        : <span style={{ fontSize: 10, color: "var(--text-muted)" }}>+ 添加</span>
                      }
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
        {msg && <div style={{ fontSize: 12, color: "var(--text-sub)", marginBottom: 10, display: "flex", alignItems: "center", gap: 5 }}>
          <CheckOutlined style={{ fontSize: 11, color: "var(--green)" }} /> {msg}
        </div>}
        {/* 列表 */}
        {(studyStatus?.blacklist?.length ?? 0) === 0 ? (
          <div style={{ textAlign: "center", padding: "24px 0", color: "var(--text-muted)", fontSize: 12 }}>
            黑名单为空，添加进程后开启学习模式
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {studyStatus!.blacklist.map(proc => (
              <div key={proc} style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                background: "var(--red-bg)", borderRadius: 8, padding: "8px 14px",
              }}>
                <span style={{ fontSize: 12, color: "var(--red)", fontWeight: 600, fontFamily: "monospace" }}>{proc}</span>
                <button onClick={() => removeBlacklist(proc)} style={{
                  background: "none", border: "none", color: "var(--text-muted)",
                  cursor: "pointer", fontSize: 16, lineHeight: 1,
                }}>×</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 最近击杀记录 */}
      {(studyStatus?.recent_kills?.length ?? 0) > 0 && (
        <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "14px 18px" }}>
          <div style={{ fontWeight: 700, marginBottom: 10, fontSize: 12, color: "var(--text)" }}>最近拦截记录</div>
          {studyStatus!.recent_kills.slice().reverse().map((k, i) => (
            <div key={i} style={{ fontSize: 11, color: k.success ? "var(--green)" : "var(--red)", lineHeight: 2 }}>
              {k.time}　
              <span style={{
                display: "inline-block", fontSize: 10, fontWeight: 600,
                padding: "1px 7px", borderRadius: 4, marginRight: 4,
                background: k.success ? "var(--green-bg)" : "var(--red-bg)",
                color: k.success ? "var(--green)" : "var(--red)",
                border: `1px solid ${k.success ? "var(--green)" : "var(--red)"}`,
              }}>
                {k.success ? "已终止" : "失败"}
              </span>
              <b>{k.process}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
