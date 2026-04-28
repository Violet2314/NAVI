/**
 * AppRulesTab.tsx — 应用分类规则管理
 */
import { useState, useEffect, useRef, useCallback } from "react";
import {
  DesktopOutlined, SyncOutlined, ReloadOutlined,
} from "@ant-design/icons";
import { fetchAppRules, upsertAppRule, deleteAppRule } from "../utils/api";
import { API } from "../utils/api";

const CAT_META: Record<string, { label: string; color: string; bg: string }> = {
  work:          { label: "工作",   color: "#4ade80", bg: "rgba(74,222,128,0.12)" },
  learning:      { label: "学习",   color: "#60a5fa", bg: "rgba(96,165,250,0.12)" },
  entertainment: { label: "娱乐",   color: "#f87171", bg: "rgba(248,113,113,0.12)" },
  communication: { label: "沟通",   color: "#a78bfa", bg: "rgba(167,139,250,0.12)" },
  utility:       { label: "工具",   color: "#94a3b8", bg: "rgba(148,163,184,0.12)" },
  other:         { label: "其他",   color: "#64748b", bg: "rgba(100,116,139,0.12)" },
};

const SOURCE_META: Record<string, { label: string; color: string }> = {
  builtin: { label: "内置规则", color: "#94a3b8" },
  llm:     { label: "AI 学习",  color: "#a78bfa" },
  user:    { label: "手动设置", color: "#4ade80" },
};

const ALL_CATEGORIES = ["work", "learning", "entertainment", "communication", "utility", "other"];

type AppRule = {
  process_name: string;
  category: string;
  is_user_defined: boolean;
  source: "builtin" | "llm" | "user";
  builtin_category: string | null;
  auto_tagged_at: string | null;
};

function CategoryBadge({ cat }: { cat: string }) {
  const m = CAT_META[cat] || CAT_META.other;
  return (
    <span style={{
      display: "inline-block", padding: "2px 10px", borderRadius: 20,
      fontSize: 11, fontWeight: 600, color: m.color, background: m.bg,
      border: `1px solid ${m.color}44`,
    }}>
      {m.label}
    </span>
  );
}

function RuleRow({ rule, onSave, onDelete }: {
  rule: AppRule;
  onSave: (proc: string, cat: string) => Promise<void>;
  onDelete: (proc: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [selCat, setSelCat] = useState(rule.category);
  const [loading, setLoading] = useState(false);

  const handleSave = async () => {
    setLoading(true);
    await onSave(rule.process_name, selCat);
    setLoading(false);
    setEditing(false);
  };

  const handleDelete = async () => {
    setLoading(true);
    await onDelete(rule.process_name);
    setLoading(false);
  };

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 100px 90px 150px",
        alignItems: "center",
        gap: 12,
        padding: "9px 16px",
        borderBottom: "1px solid var(--border)",
        transition: "background 0.12s",
      }}
      onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-hover, rgba(255,255,255,0.04))")}
      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
    >
      <div style={{ fontFamily: "monospace", fontSize: 12, color: "var(--text)" }}>
        {rule.process_name}
        {rule.source === "user" && rule.builtin_category && rule.builtin_category !== rule.category && (
          <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 8 }}>
            (内置: {CAT_META[rule.builtin_category]?.label ?? rule.builtin_category})
          </span>
        )}
      </div>

      <div style={{ fontSize: 11, color: SOURCE_META[rule.source]?.color ?? "#94a3b8", fontWeight: 500 }}>
        {SOURCE_META[rule.source]?.label ?? rule.source}
      </div>

      <div>
        {editing ? (
          <select
            value={selCat}
            onChange={e => setSelCat(e.target.value)}
            style={{
              background: "var(--bg-app)", border: "1px solid var(--border)",
              borderRadius: 6, color: "var(--text)", fontSize: 12, padding: "3px 6px",
            }}
          >
            {ALL_CATEGORIES.map(c => (
              <option key={c} value={c}>{CAT_META[c]?.label ?? c}</option>
            ))}
          </select>
        ) : (
          <CategoryBadge cat={rule.category} />
        )}
      </div>

      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        {editing ? (
          <>
            <button
              onClick={handleSave}
              disabled={loading}
              className="navi-btn navi-btn-primary"
              style={{ padding: "3px 12px", fontSize: 12, opacity: loading ? 0.6 : 1 }}
            >
              {loading ? "保存中..." : "保存"}
            </button>
            <button
              onClick={() => { setEditing(false); setSelCat(rule.category); }}
              className="navi-btn"
              style={{ padding: "3px 10px", fontSize: 12 }}
            >
              取消
            </button>
          </>
        ) : (
          <>
            <button
              onClick={() => setEditing(true)}
              className="navi-btn"
              style={{ padding: "3px 12px", fontSize: 12 }}
            >
              修改
            </button>
            {rule.source === "user" && (
              <button
                onClick={handleDelete}
                disabled={loading}
                className="navi-btn"
                style={{ padding: "3px 10px", fontSize: 12, color: "var(--red)", borderColor: "var(--red)", opacity: loading ? 0.6 : 1 }}
              >
                还原
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function AppRulesTab() {
  const [rules, setRules] = useState<AppRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filterSource, setFilterSource] = useState<"all" | "builtin" | "llm" | "user">("all");
  const [toast, setToast] = useState("");

  // 新增规则区
  const [newProc, setNewProc] = useState("");
  const [newCat, setNewCat] = useState("work");
  const [addLoading, setAddLoading] = useState(false);
  const [addErr, setAddErr] = useState("");

  // 进程选择面板（同专注模式）
  const [allProcs, setAllProcs] = useState<string[]>([]);
  const [procsLoading, setProcsLoading] = useState(false);
  const [showProcPanel, setShowProcPanel] = useState(false);
  const [procSearch, setProcSearch] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(""), 3000);
  };

  const load = async () => {
    try {
      const data = await fetchAppRules();
      setRules(Array.isArray(data) ? data : []);
    } catch {
      setRules([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const fetchProcs = useCallback(async () => {
    setProcsLoading(true);
    try {
      const r = await fetch(`${API}/api/processes`);
      const d = await r.json();
      setAllProcs(d.processes ?? []);
    } catch {}
    setProcsLoading(false);
  }, []);

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

  const handleSave = async (proc: string, cat: string) => {
    await upsertAppRule(proc, cat);
    showToast(`已将 ${proc} 设置为「${CAT_META[cat]?.label ?? cat}」`);
    await load();
  };

  const handleDelete = async (proc: string) => {
    const res = await deleteAppRule(proc);
    if (res?.ok) {
      showToast(`${proc} 已还原为自动分类`);
      await load();
    } else {
      showToast(`删除失败：${res?.error ?? "未知错误"}`);
    }
  };

  // 从进程面板或输入框添加规则
  const handleAdd = async (procOverride?: string) => {
    const p = (procOverride ?? newProc).trim().toLowerCase();
    setAddErr("");
    if (!p) { setAddErr("进程名不能为空"); return; }
    if (!p.endsWith(".exe")) { setAddErr("进程名需以 .exe 结尾"); return; }
    setAddLoading(true);
    try {
      await upsertAppRule(p, newCat);
      showToast(`已将 ${p} 设置为「${CAT_META[newCat]?.label ?? newCat}」`);
      setNewProc("");
      setSuggestions([]);
      setShowSuggestions(false);
      await load();
    } catch {
      setAddErr("添加失败");
    }
    setAddLoading(false);
  };

  const filtered = rules.filter(r => {
    const matchSearch = r.process_name.includes(search.toLowerCase());
    const matchSource = filterSource === "all" || r.source === filterSource;
    return matchSearch && matchSource;
  });

  const counts = {
    builtin: rules.filter(r => r.source === "builtin").length,
    llm:     rules.filter(r => r.source === "llm").length,
    user:    rules.filter(r => r.source === "user").length,
  };

  // 已有规则的进程名集合（用于面板高亮）
  const existingUserProcs = new Set(rules.filter(r => r.source === "user").map(r => r.process_name));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

      {/* 说明卡片 */}
      <div className="navi-card" style={{ padding: "18px 22px" }}>
        <div style={{ fontWeight: 700, fontSize: 14, color: "var(--text)", marginBottom: 5 }}>
          应用分类规则
        </div>
        <div style={{ fontSize: 12, color: "var(--text-sub)", lineHeight: 1.7 }}>
          管理每个应用的活动分类。
          <span style={{ color: "#4ade80", fontWeight: 600 }}>手动设置</span>优先级最高，不会被 AI 覆盖。
          AI 每 30 分钟自动分类未知进程并缓存结果。
          <span style={{ marginLeft: 16, color: "var(--text-muted)" }}>
            内置 {counts.builtin} · AI 学习 {counts.llm} · 手动 {counts.user}
          </span>
        </div>
      </div>

      {/* 新增规则 */}
      <div className="navi-card" style={{ padding: "18px 22px" }}>
        <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text)", marginBottom: 14 }}>
          添加手动规则
        </div>

        <div style={{ display: "flex", gap: 8, marginBottom: 6, position: "relative" }}>
          {/* 进程名输入 + 联想下拉 */}
          <div style={{ flex: 1, position: "relative" }}>
            <input
              ref={inputRef}
              value={newProc}
              onChange={e => handleInputChange(e.target.value)}
              onFocus={() => {
                if (allProcs.length === 0) fetchProcs();
                if (suggestions.length > 0) setShowSuggestions(true);
              }}
              onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
              onKeyDown={e => e.key === "Enter" && handleAdd()}
              placeholder="输入进程名，如 wechat.exe"
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
                  <div
                    key={p}
                    onMouseDown={() => { setNewProc(p); setSuggestions([]); setShowSuggestions(false); }}
                    style={{
                      padding: "8px 14px", fontSize: 12, cursor: "pointer",
                      color: "var(--text)", fontFamily: "monospace",
                      borderBottom: "1px solid var(--border)",
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                  >
                    {p}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 分类选择 */}
          <select
            value={newCat}
            onChange={e => setNewCat(e.target.value)}
            style={{
              background: "var(--bg-app)", border: "1.5px solid var(--border)",
              borderRadius: 8, color: "var(--text)", fontSize: 13, padding: "0 10px",
              flexShrink: 0,
            }}
          >
            {ALL_CATEGORIES.map(c => (
              <option key={c} value={c}>{CAT_META[c]?.label ?? c}</option>
            ))}
          </select>

          {/* 打开进程面板按钮 */}
          <button
            onClick={() => {
              if (!showProcPanel) { fetchProcs(); setProcSearch(""); }
              setShowProcPanel(v => !v);
            }}
            title="从当前运行进程中选择"
            className="navi-btn"
            style={{
              flexShrink: 0,
              background: showProcPanel ? "var(--primary-btn)" : undefined,
              color: showProcPanel ? "var(--text-inverse)" : undefined,
            }}
          >
            <DesktopOutlined />
          </button>

          <button
            onClick={() => handleAdd()}
            disabled={addLoading}
            className="navi-btn navi-btn-primary"
            style={{ flexShrink: 0, opacity: addLoading ? 0.6 : 1 }}
          >
            {addLoading ? "添加中..." : "添加规则"}
          </button>
        </div>

        {addErr && (
          <div style={{ fontSize: 12, color: "var(--red)", marginBottom: 8 }}>{addErr}</div>
        )}

        {/* 进程选择面板 */}
        {showProcPanel && (
          <div style={{
            marginTop: 10, border: "1px solid var(--border)",
            borderRadius: "var(--r-lg)", background: "var(--bg-card)", overflow: "hidden",
          }}>
            <div style={{
              padding: "9px 14px", borderBottom: "1px solid var(--border)",
              display: "flex", gap: 8, alignItems: "center",
            }}>
              <ReloadOutlined style={{ fontSize: 12, color: "var(--text-muted)", flexShrink: 0 }} />
              <input
                autoFocus
                value={procSearch}
                onChange={e => setProcSearch(e.target.value)}
                placeholder="搜索进程名..."
                style={{
                  flex: 1, border: "none", outline: "none",
                  background: "transparent", fontSize: 12,
                  color: "var(--text)", fontFamily: "inherit",
                }}
              />
              <button
                onClick={fetchProcs}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)", display: "flex", alignItems: "center" }}
              >
                <SyncOutlined spin={procsLoading} style={{ fontSize: 13 }} />
              </button>
            </div>
            <div style={{ maxHeight: 220, overflowY: "auto" }}>
              {procsLoading ? (
                <div style={{ textAlign: "center", padding: 20, fontSize: 12, color: "var(--text-muted)" }}>加载中...</div>
              ) : allProcs.filter(p => p.toLowerCase().includes(procSearch.toLowerCase())).length === 0 ? (
                <div style={{ textAlign: "center", padding: 20, fontSize: 12, color: "var(--text-muted)" }}>没有匹配的进程</div>
              ) : (
                allProcs
                  .filter(p => p.toLowerCase().includes(procSearch.toLowerCase()))
                  .map(p => {
                    const hasRule = existingUserProcs.has(p);
                    return (
                      <div
                        key={p}
                        onClick={() => {
                          setNewProc(p);
                          setShowProcPanel(false);
                          inputRef.current?.focus();
                        }}
                        style={{
                          display: "flex", alignItems: "center", justifyContent: "space-between",
                          padding: "7px 14px", fontSize: 12, cursor: "pointer",
                          borderBottom: "1px solid var(--border)",
                          background: hasRule ? "rgba(74,222,128,0.06)" : "transparent",
                          transition: "background 0.1s",
                        }}
                        onMouseEnter={e => { if (!hasRule) e.currentTarget.style.background = "var(--bg-hover)"; }}
                        onMouseLeave={e => { e.currentTarget.style.background = hasRule ? "rgba(74,222,128,0.06)" : "transparent"; }}
                      >
                        <span style={{ fontFamily: "monospace", color: hasRule ? "#4ade80" : "var(--text)" }}>{p}</span>
                        {hasRule
                          ? <span style={{ fontSize: 10, color: "#4ade80", fontWeight: 600 }}>已有规则</span>
                          : <span style={{ fontSize: 10, color: "var(--text-muted)" }}>点击选择</span>
                        }
                      </div>
                    );
                  })
              )}
            </div>
          </div>
        )}
      </div>

      {/* 规则列表 */}
      <div className="navi-card" style={{ padding: 0, overflow: "hidden" }}>
        {/* 筛选栏 */}
        <div style={{
          display: "flex", gap: 8, padding: "14px 16px",
          borderBottom: "1px solid var(--border)", flexWrap: "wrap", alignItems: "center",
        }}>
          <input
            placeholder="搜索进程名..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              flex: "1 1 150px", background: "var(--bg-app)",
              border: "1.5px solid var(--border)", borderRadius: 8,
              color: "var(--text)", fontSize: 12, padding: "6px 12px", outline: "none",
            }}
          />
          {(["all", "user", "llm", "builtin"] as const).map(s => (
            <button
              key={s}
              onClick={() => setFilterSource(s)}
              className="navi-btn"
              style={{
                padding: "5px 12px", fontSize: 12,
                border: filterSource === s ? "1.5px solid var(--accent, #4ade80)" : "1.5px solid var(--border)",
                background: filterSource === s ? "rgba(74,222,128,0.12)" : "transparent",
                color: filterSource === s ? "#4ade80" : "var(--text-muted)",
                fontWeight: filterSource === s ? 600 : 400,
              }}
            >
              {s === "all" ? "全部" : SOURCE_META[s]?.label}
            </button>
          ))}
        </div>

        {/* 表头 */}
        <div style={{
          display: "grid", gridTemplateColumns: "1fr 100px 90px 150px",
          gap: 12, padding: "7px 16px",
          borderBottom: "1px solid var(--border)",
          background: "rgba(255,255,255,0.02)",
        }}>
          {["进程名", "来源", "分类", "操作"].map(h => (
            <div
              key={h}
              style={{
                fontSize: 11, color: "var(--text-muted)", fontWeight: 600,
                textAlign: h === "操作" ? "right" : "left",
              }}
            >
              {h}
            </div>
          ))}
        </div>

        {/* 数据行 */}
        {loading ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--text-muted)", fontSize: 13 }}>加载中...</div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--text-muted)", fontSize: 13 }}>
            {search ? "没有匹配的规则" : "暂无规则"}
          </div>
        ) : (
          filtered.map(rule => (
            <RuleRow key={rule.process_name} rule={rule} onSave={handleSave} onDelete={handleDelete} />
          ))
        )}
      </div>

      {/* Toast */}
      {toast && (
        <div style={{
          position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
          background: "var(--bg-card)", border: "1px solid var(--border)",
          borderRadius: 10, padding: "10px 20px", fontSize: 13,
          color: "var(--text)", boxShadow: "0 4px 20px rgba(0,0,0,0.3)", zIndex: 9999,
        }}>
          {toast}
        </div>
      )}
    </div>
  );
}