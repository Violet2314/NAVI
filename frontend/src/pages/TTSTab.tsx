/**
 * TTS 设置面板 — 语音合成 Provider 选择 + 配置 + 试听
 */
import { useState, useEffect, useCallback } from "react";
import { SoundOutlined, CheckCircleOutlined, LoadingOutlined, CloseCircleOutlined } from "@ant-design/icons";

const API = "http://localhost:8000";

interface TTSProviderInfo {
  name: string;
  display_name: string;
  supports_clone: boolean;
  is_local: boolean;
}

interface Voice {
  id: string;
  name: string;
  language: string;
}

interface TTSConfig {
  enabled: boolean;
  provider: string;
  providers: Record<string, Record<string, unknown>>;
  available_providers: TTSProviderInfo[];
}

// ── Provider 说明 ────────────────────────────────────────────────────
const PROVIDER_DESC: Record<string, { desc: string; fields: { key: string; label: string; type: "text" | "password" | "number" | "file"; placeholder?: string }[] }> = {
  edge_tts: {
    desc: "微软免费 TTS，无需 API Key，中文音色自然。适合快速验证。",
    fields: [
      { key: "voice_id", label: "音色", type: "text", placeholder: "zh-CN-XiaoxiaoNeural" },
      { key: "speed", label: "语速", type: "number", placeholder: "1.0" },
    ],
  },
  fish_audio: {
    desc: "云端声音克隆，支持上传参考音频生成角色语音。免费额度 $10/月。",
    fields: [
      { key: "api_key", label: "API Key", type: "password", placeholder: "fish.audio 的 API Key" },
      { key: "voice_id", label: "Voice / Model ID", type: "text", placeholder: "角色模型 ID（fish.audio 获取）" },
      { key: "speed", label: "语速", type: "number", placeholder: "1.0" },
    ],
  },
  openai_tts: {
    desc: "OpenAI 官方 TTS 或兼容 API。支持多种音色，延迟极低。",
    fields: [
      { key: "api_key", label: "API Key", type: "password", placeholder: "sk-..." },
      { key: "api_base", label: "API Base URL（可选）", type: "text", placeholder: "https://api.openai.com（留空使用官方）" },
      { key: "model", label: "模型", type: "text", placeholder: "tts-1" },
      { key: "voice_id", label: "音色", type: "text", placeholder: "nova" },
      { key: "speed", label: "语速", type: "number", placeholder: "1.0" },
    ],
  },
  gptsovits: {
    desc: "本地 GPT-SoVITS 声音克隆（需 GPU + 本地部署 GPT-SoVITS 服务）。",
    fields: [
      { key: "api_base", label: "API 地址", type: "text", placeholder: "http://localhost:9880" },
      { key: "refer_wav_path", label: "参考音频路径", type: "text", placeholder: "D:/voices/amiya_ref.wav" },
      { key: "refer_prompt_text", label: "参考音频文本", type: "text", placeholder: "博士，我会一直在你身边的" },
      { key: "speed", label: "语速", type: "number", placeholder: "1.0" },
    ],
  },
};

export function TTSTab() {
  const [config, setConfig] = useState<TTSConfig | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");
  const [testText, setTestText] = useState("博士，今天辛苦了，早点休息吧。");
  const [testing, setTesting] = useState(false);
  const [healthStatus, setHealthStatus] = useState<"ok" | "fail" | "checking" | "idle">("idle");

  // 加载配置
  const loadConfig = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API}/api/tts/config`);
      const data = await res.json();
      setConfig(data);
    } catch (e) {
      console.error("Failed to load TTS config:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  // 加载可用声音
  const loadVoices = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/tts/voices`);
      const data = await res.json();
      setVoices(data.voices || []);
    } catch { setVoices([]); }
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);
  useEffect(() => { if (config?.enabled) loadVoices(); }, [config?.enabled, config?.provider, loadVoices]);

  // 保存配置
  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    setSaveMsg("");
    try {
      const res = await fetch(`${API}/api/tts/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: config.enabled,
          provider: config.provider,
          providers: config.providers,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setSaveMsg("已保存");
        setConfig(data.config);
        if (data.config.enabled) loadVoices();
      } else {
        setSaveMsg("保存失败");
      }
    } catch (e) {
      setSaveMsg("网络错误");
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(""), 3000);
    }
  };

  // 试听（同时发送给 Live2D 伴侣窗口做口型同步）
  const handleTest = async () => {
    if (!config?.enabled || !testText.trim()) return;
    setTesting(true);
    try {
      const res = await fetch(`${API}/api/tts/synthesize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: testText }),
      });
      const data = await res.json();
      if (data.audio) {
        // 发送给 Live2D 伴侣窗口做口型同步 + 播放
        try {
          const { emitTo } = await import("@tauri-apps/api/event" as any);
          await emitTo("live2d-companion", "live2d:tts_audio", {
            audio: data.audio,
            format: data.format || "mp3",
          });
          console.log("[TTS 试听] 已通过 emitTo 发送音频到 live2d-companion 窗口");
        } catch {
          // 非 Tauri 环境 fallback：主窗口直接播放
          const audio = new Audio(`data:audio/mp3;base64,${data.audio}`);
          audio.play();
        }
      }
    } catch (e) {
      console.error("TTS test failed:", e);
    } finally {
      setTesting(false);
    }
  };

  // 健康检查
  const handleHealthCheck = async () => {
    setHealthStatus("checking");
    try {
      const res = await fetch(`${API}/api/tts/health`);
      const data = await res.json();
      setHealthStatus(data.healthy ? "ok" : "fail");
    } catch { setHealthStatus("fail"); }
    setTimeout(() => setHealthStatus("idle"), 5000);
  };

  // 更新 provider 子配置
  const updateProviderField = (provider: string, key: string, value: unknown) => {
    if (!config) return;
    setConfig({
      ...config,
      providers: {
        ...config.providers,
        [provider]: {
          ...(config.providers[provider] || {}),
          [key]: value,
        },
      },
    });
  };

  if (loading) return <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>加载中…</div>;
  if (!config) return <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>加载失败，请确认后端已重启</div>;

  const currentProvider = (config.available_providers || []).find(p => p.name === config.provider);
  const providerFields = PROVIDER_DESC[config.provider]?.fields || [];
  const providerConfig = (config.providers || {})[config.provider] || {};

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* ── 开关 + Provider 选择 ─────────────────────────────────── */}
      <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)", display: "flex", alignItems: "center", gap: 8 }}>
            <SoundOutlined style={{ color: "var(--primary)" }} />
            语音合成 (TTS)
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <span style={{ fontSize: 12, color: "var(--text-sub)" }}>{config.enabled ? "已开启" : "已关闭"}</span>
            <div
              onClick={() => setConfig({ ...config, enabled: !config.enabled })}
              style={{
                width: 40, height: 22, borderRadius: 11, cursor: "pointer",
                background: config.enabled ? "var(--primary)" : "var(--border-dark, #ccc)",
                position: "relative", transition: "background 0.2s",
              }}
            >
              <div style={{
                width: 18, height: 18, borderRadius: 9, background: "#fff",
                position: "absolute", top: 2,
                left: config.enabled ? 20 : 2, transition: "left 0.2s",
                boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
              }} />
            </div>
          </label>
        </div>

        {config.enabled && (
          <>
            <div style={{ fontSize: 12, color: "var(--text-sub)", marginBottom: 12 }}>选择语音引擎</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
              {config.available_providers.map(p => (
                <button
                  key={p.name}
                  onClick={() => setConfig({ ...config, provider: p.name })}
                  style={{
                    padding: "8px 16px", borderRadius: 10, border: `1.5px solid ${config.provider === p.name ? "var(--primary)" : "var(--border)"}`,
                    background: config.provider === p.name ? "var(--primary-light, rgba(8,145,178,0.08))" : "var(--bg-card)",
                    color: config.provider === p.name ? "var(--primary)" : "var(--text-sub)",
                    cursor: "pointer", fontSize: 12, fontWeight: config.provider === p.name ? 700 : 500,
                    transition: "all 0.15s",
                  }}
                >
                  {p.display_name}
                  {p.supports_clone && <span style={{ fontSize: 10, marginLeft: 4, opacity: 0.5, fontWeight: 500 }}>克隆</span>}
                  {p.is_local && <span style={{ fontSize: 10, marginLeft: 4, opacity: 0.5, fontWeight: 500 }}>本地</span>}
                </button>
              ))}
            </div>
            {PROVIDER_DESC[config.provider] && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8, lineHeight: 1.5 }}>
                {PROVIDER_DESC[config.provider].desc}
              </div>
            )}
          </>
        )}
      </div>

      {/* ── Provider 配置 ────────────────────────────────────────── */}
      {config.enabled && providerFields.length > 0 && (
        <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
          <div style={{ fontWeight: 700, marginBottom: 16, fontSize: 13, color: "var(--text)" }}>
            {currentProvider?.display_name || config.provider} 配置
          </div>
          {providerFields.map(f => (
            <div key={f.key} style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: "var(--text-sub)", display: "block", marginBottom: 4 }}>{f.label}</label>
              {f.key === "voice_id" && voices.length > 0 ? (
                <select
                  value={(providerConfig[f.key] as string) || ""}
                  onChange={e => updateProviderField(config.provider, f.key, e.target.value)}
                  style={{
                    width: "100%", padding: "8px 12px", borderRadius: 8,
                    border: "1px solid var(--border)", background: "var(--bg-card)",
                    color: "var(--text)", fontSize: 12, outline: "none",
                  }}
                >
                  <option value="">请选择音色</option>
                  {voices.map(v => <option key={v.id} value={v.id}>{v.name} ({v.language})</option>)}
                </select>
              ) : (
                <input
                  type={f.type === "password" ? "password" : f.type === "number" ? "number" : "text"}
                  value={(providerConfig[f.key] as string | number) ?? (f.type === "number" ? 1.0 : "")}
                  placeholder={f.placeholder}
                  step={f.type === "number" ? 0.1 : undefined}
                  onChange={e => updateProviderField(config.provider, f.key, f.type === "number" ? parseFloat(e.target.value) || 1.0 : e.target.value)}
                  style={{
                    width: "100%", padding: "8px 12px", borderRadius: 8,
                    border: "1px solid var(--border)", background: "var(--bg-card)",
                    color: "var(--text)", fontSize: 12, outline: "none",
                    boxSizing: "border-box",
                  }}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── 试听 + 操作 ──────────────────────────────────────────── */}
      {config.enabled && (
        <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
          <div style={{ fontWeight: 700, marginBottom: 12, fontSize: 13, color: "var(--text)" }}>试听</div>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <input
              value={testText}
              onChange={e => setTestText(e.target.value)}
              placeholder="输入试听文本…"
              style={{
                flex: 1, padding: "8px 12px", borderRadius: 8,
                border: "1px solid var(--border)", background: "var(--bg-card)",
                color: "var(--text)", fontSize: 12, outline: "none",
              }}
            />
            <button
              onClick={handleTest}
              disabled={testing || !testText.trim()}
              style={{
                padding: "8px 18px", borderRadius: 8, border: "none",
                background: "var(--primary)", color: "#fff", fontSize: 12,
                fontWeight: 600, cursor: testing ? "wait" : "pointer",
                opacity: testing ? 0.6 : 1,
              }}
            >
              {testing ? <LoadingOutlined /> : <SoundOutlined />}
              <span style={{ marginLeft: 6 }}>试听</span>
            </button>
          </div>
        </div>
      )}

      {/* ── 保存 + 健康检查 ─────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button
          onClick={handleSave}
          disabled={saving}
          style={{
            background: "var(--accent, #0891b2)", color: "#fff", border: "none",
            borderRadius: 10, padding: "9px 22px", fontWeight: 600, fontSize: 13, cursor: "pointer",
            opacity: saving ? 0.6 : 1,
          }}
        >
          {saving ? "保存中…" : "保存配置"}
        </button>
        {config.enabled && (
          <button
            onClick={handleHealthCheck}
            style={{
              padding: "10px 18px", borderRadius: 12, border: "1px solid var(--border)",
              background: "var(--bg-card)", color: "var(--text-sub)", fontSize: 12,
              cursor: "pointer", fontWeight: 600, display: "flex", alignItems: "center", gap: 6,
            }}
          >
            {healthStatus === "checking" ? <LoadingOutlined /> : healthStatus === "ok" ? <CheckCircleOutlined style={{ color: "var(--green)" }} /> : healthStatus === "fail" ? <CloseCircleOutlined style={{ color: "#ef4444" }} /> : null}
            连通测试
          </button>
        )}
        {saveMsg && <span style={{ fontSize: 12, color: "var(--text-sub)" }}>{saveMsg}</span>}
      </div>
    </div>
  );
}
