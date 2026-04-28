/**
 * LlmTab.tsx — AI 模型配置 Tab
 */
import { useState, useEffect, useCallback } from "react";
import { API } from "../utils/api";

type LlmCfg = Record<string, string>;
type ProviderInfo = { id: string; name: string; base_url: string; vision: boolean };

export function LlmTab() {
  const [cfg, setCfg] = useState<LlmCfg>({});
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [msg, setMsg] = useState("");
  const [msgOk, setMsgOk] = useState(true);
  const [activeInfo, setActiveInfo] = useState<string>("");

  useEffect(() => {
    fetch(`${API}/api/llm/config`).then(r => r.json()).then(setCfg).catch(() => {});
    fetch(`${API}/api/llm/providers`).then(r => r.json()).then(setProviders).catch(() => {});
  }, []);

  const checkActive = async () => {
    try {
      const res = await fetch(`${API}/api/llm/active`);
      const data = await res.json();
      setActiveInfo(JSON.stringify(data, null, 2));
    } catch { setActiveInfo("后端未响应"); }
  };

  const save = async () => {
    try {
      await fetch(`${API}/api/llm/config`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) });
      setMsg("[已保存]"); setMsgOk(true);
    } catch { setMsg("保存失败"); setMsgOk(false); }
    setTimeout(() => setMsg(""), 3000);
  };

  const onProviderChange = (field: "vision" | "text" | "emb", providerId: string) => {
    const spec = providers.find(p => p.id === providerId);
    setCfg(c => ({
      ...c,
      [`${field}_provider`]: providerId,
      [`${field}_base_url`]: spec?.base_url ?? "",
    }));
  };

  const [modelLists, setModelLists] = useState<Record<string, string[]>>({});
  const [modelLoading, setModelLoading] = useState<Record<string, boolean>>({});

  const fetchModels = useCallback(async (field: "vision" | "text" | "emb", providerId: string, apiKey: string, baseUrl: string) => {
    if (!providerId) return;
    setModelLoading(s => ({ ...s, [field]: true }));
    try {
      const params = new URLSearchParams({ provider: providerId });
      if (apiKey && !apiKey.endsWith("****")) params.set("api_key", apiKey);
      if (baseUrl) params.set("base_url", baseUrl);
      const res = await fetch(`${API}/api/llm/models?${params}`);
      const data = await res.json();
      setModelLists(s => ({ ...s, [field]: data.models ?? [] }));
    } catch {
      setModelLists(s => ({ ...s, [field]: [] }));
    }
    setModelLoading(s => ({ ...s, [field]: false }));
  }, []);

  const LlmSection = ({ title, icon, field, desc }: { title: string; icon: string; field: "vision" | "text" | "emb"; desc: string }) => {
    const availableProviders = field === "vision" ? providers.filter(p => p.vision) : providers;
    const models = modelLists[field] ?? [];
    const loading = modelLoading[field] ?? false;
    const currentModel = cfg[`${field}_model`] ?? "";

    return (
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)", marginBottom: 4 }}>{icon} {title}</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 12 }}>{desc}</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          {/* 厂商 */}
          <div>
            <div style={{ fontSize: 11, color: "var(--text-sub)", marginBottom: 4 }}>厂商</div>
            <select
              value={cfg[`${field}_provider`] ?? ""}
              onChange={e => {
                onProviderChange(field, e.target.value);
                const spec = providers.find(p => p.id === e.target.value);
                fetchModels(field, e.target.value, cfg[`${field}_api_key`] ?? "", spec?.base_url ?? "");
              }}
              style={{
                width: "100%", background: "var(--bg-app)",
                border: `1.5px solid ${"var(--border)"}`, borderRadius: 10,
                padding: "8px 12px", color: "var(--text)", fontSize: 12, outline: "none",
              }}
            >
              <option value="">— 选择厂商 —</option>
              {availableProviders.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          {/* 模型选择 */}
          <div>
            <div style={{ fontSize: 11, color: "var(--text-sub)", marginBottom: 4, display: "flex", alignItems: "center", gap: 6 }}>
              模型名称
              {loading && <span style={{ fontSize: 10, color: "var(--primary)" }}>加载中…</span>}
              {!loading && models.length > 0 && (
                <span style={{ fontSize: 10, color: "var(--text-muted)" }}>共 {models.length} 个</span>
              )}
            </div>
            {models.length > 0 ? (
              <select
                value={currentModel}
                onChange={e => setCfg(c => ({ ...c, [`${field}_model`]: e.target.value }))}
                style={{
                  width: "100%", background: "var(--bg-app)",
                  border: `1.5px solid ${"var(--border)"}`, borderRadius: 10,
                  padding: "8px 12px", color: "var(--text)", fontSize: 12, outline: "none",
                }}
              >
                <option value="">— 选择模型 —</option>
                {models.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            ) : (
              <input
                value={currentModel}
                onChange={e => setCfg(c => ({ ...c, [`${field}_model`]: e.target.value }))}
                placeholder="手动输入模型名称"
                style={{
                  width: "100%", background: "var(--bg-app)",
                  border: `1.5px solid ${"var(--border)"}`, borderRadius: 10,
                  padding: "8px 12px", color: "var(--text)", fontSize: 12, outline: "none",
                  boxSizing: "border-box",
                }}
              />
            )}
          </div>
          {/* API Key */}
          <div style={{ gridColumn: "1 / -1" }}>
            <div style={{ fontSize: 11, color: "var(--text-sub)", marginBottom: 4 }}>API Key</div>
            <input
              type="text"
              value={cfg[`${field}_api_key`] ?? ""}
              onChange={e => setCfg(c => ({ ...c, [`${field}_api_key`]: e.target.value }))}
              placeholder="粘贴你的 API Key"
              style={{
                width: "100%", background: "var(--bg-app)",
                border: `1.5px solid ${"var(--border)"}`, borderRadius: 10,
                padding: "8px 12px", color: "var(--text)", fontSize: 12, outline: "none",
                boxSizing: "border-box", fontFamily: "monospace",
              }}
            />
          </div>
          {/* Base URL */}
          <div>
            <div style={{ fontSize: 11, color: "var(--text-sub)", marginBottom: 4, display: "flex", alignItems: "center", gap: 6 }}>
              API Base URL
              {cfg[`${field}_provider`] && cfg[`${field}_provider`] !== "custom" && (
                <span style={{ fontSize: 10, color: "var(--text-muted)" }}>选厂商自动填写</span>
              )}
            </div>
            <input
              value={cfg[`${field}_base_url`] ?? ""}
              onChange={e => setCfg(c => ({ ...c, [`${field}_base_url`]: e.target.value }))}
              placeholder="https://your-endpoint/v1"
              readOnly={!!cfg[`${field}_provider`] && cfg[`${field}_provider`] !== "custom"}
              style={{
                width: "100%",
                background: (cfg[`${field}_provider`] && cfg[`${field}_provider`] !== "custom")
                  ? "rgba(245,245,245,0.9)" : "var(--bg-app)",
                border: `1.5px solid ${"var(--border)"}`, borderRadius: 10,
                padding: "8px 12px", color: "var(--text-sub)", fontSize: 11, outline: "none",
                boxSizing: "border-box", fontFamily: "monospace",
              }}
            />
          </div>
        </div>
        {cfg[`${field}_provider`] && (
          <button
            onClick={() => fetchModels(field, cfg[`${field}_provider`] ?? "", cfg[`${field}_api_key`] ?? "", cfg[`${field}_base_url`] ?? "")}
            style={{
              marginTop: 8, background: "transparent", border: `1px solid ${"var(--border)"}`,
              borderRadius: 8, padding: "4px 12px", fontSize: 11, color: "var(--text-sub)", cursor: "pointer",
            }}
          >
            {loading ? "拉取中…" : "刷新模型列表"}
          </button>
        )}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "20px 24px" }}>
        <div style={{ fontWeight: 700, marginBottom: 4, fontSize: 13, color: "var(--text)" }}>AI 模型配置</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 20 }}>
          Vision 用于截图分析，Text 用于日报撰写，Embedding 用于记忆检索
        </div>
        <LlmSection title="Vision 模型（截图分析）" icon="" field="vision" desc="用于理解截图内容，需要支持图片输入的多模态模型" />
        <div style={{ borderTop: `1px solid ${"var(--border)"}`, marginBottom: 20 }} />
        <LlmSection title="Text 模型（日报撰写）" icon="" field="text" desc="用于撰写日报和分析活动数据，普通文本模型即可" />
        <div style={{ borderTop: `1px solid ${"var(--border)"}`, marginBottom: 20 }} />
        <LlmSection title="Embedding 模型（记忆检索）" icon="" field="emb" desc="用于将记忆向量化以支持语义搜索，冷启动时需配置" />
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
          <button onClick={save} style={{
            background: "linear-gradient(135deg,#0891b2,#06b6d4)", color: "var(--text-inverse)",
            border: "none", borderRadius: 12, padding: "10px 28px",
            fontWeight: 700, fontSize: 13, cursor: "pointer",
            boxShadow: "0 4px 14px rgba(6,182,212,0.35)",
          }}>保存 AI 配置</button>
          <button onClick={checkActive} style={{
            background: "transparent", color: "var(--text-sub)",
            border: `1px solid ${"var(--border)"}`, borderRadius: 10, padding: "8px 16px",
            fontSize: 12, cursor: "pointer",
          }}>查看实际生效配置</button>
          {msg && <span style={{
            fontSize: 12, color: msgOk ? "var(--green)" : "var(--red)",
            background: msgOk ? "var(--green-bg)" : "var(--red-bg)",
            borderRadius: 8, padding: "4px 12px",
          }}>{msg}</span>}
        </div>
        {activeInfo && (
          <pre style={{
            marginTop: 12, fontSize: 11, padding: "10px 14px", borderRadius: 10,
            background: "var(--border)", color: "var(--text)", overflowX: "auto",
            border: `1px solid ${"var(--border)"}`, lineHeight: 1.6,
          }}>{activeInfo}</pre>
        )}
      </div>
    </div>
  );
}
