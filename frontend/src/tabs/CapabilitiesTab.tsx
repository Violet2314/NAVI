/**
 * CapabilitiesTab.tsx — Skill 管理 + MCP 服务器配置
 */
import { useState, useEffect, useCallback } from "react";
import {
  DeleteOutlined, PlusOutlined, ReloadOutlined,
  ThunderboltOutlined, ApiOutlined, CheckCircleOutlined,
  WarningOutlined, CodeOutlined,
} from "@ant-design/icons";
import { API } from "../utils/api";

// ─── 类型 ──────────────────────────────────────────────────
interface Skill {
  name: string;
  description: string;
  source: "builtin" | "workspace";
  path: string;
}

interface MCPServer {
  name: string;
  type?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
  // nanobot 配置用 camelCase
  toolTimeout?: number;
  enabledTools?: string[];
  // 兼容旧字段（表单提交时统一转为 camelCase）
  tool_timeout?: number;
  enabled_tools?: string[];
}

type TransportType = "stdio" | "sse" | "streamableHttp";

// ─── 工具函数 ─────────────────────────────────────────────
async function apiFetch(path: string, opts?: RequestInit) {
  const r = await fetch(`${API}${path}`, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({ error: r.statusText }));
    throw new Error(err.error || r.statusText);
  }
  return r.json();
}

// ─── 子组件：Badge ─────────────────────────────────────────
function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      display: "inline-block", padding: "1px 8px", borderRadius: 100,
      fontSize: 11, fontWeight: 600, letterSpacing: 0.2,
      background: color === "primary" ? "var(--primary-light)" : "var(--bg-card)",
      color: color === "primary" ? "var(--primary)" : "var(--text-muted)",
      border: `1px solid ${color === "primary" ? "var(--primary)" : "var(--border)"}`,
    }}>
      {label}
    </span>
  );
}

// ─── 子组件：SectionHeader ────────────────────────────────
function SectionHeader({ icon, title, action }: {
  icon: React.ReactNode; title: string; action?: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 16, color: "var(--primary)" }}>{icon}</span>
        <span style={{ fontWeight: 700, fontSize: 15, color: "var(--text)" }}>{title}</span>
      </div>
      {action}
    </div>
  );
}

// ─── 子组件：Skill 列表 ───────────────────────────────────
function SkillsSection() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(false);
  const [deletingName, setDeletingName] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiFetch("/api/skills");
      setSkills(data);
    } catch (e: any) {
      setMsg({ type: "err", text: e.message });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const deleteSkill = async (name: string) => {
    if (!window.confirm(`确定删除 Skill「${name}」？`)) return;
    setDeletingName(name);
    try {
      await apiFetch(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
      setMsg({ type: "ok", text: `Skill「${name}」已删除` });
      load();
    } catch (e: any) {
      setMsg({ type: "err", text: e.message });
    } finally {
      setDeletingName(null);
    }
  };

  return (
    <div style={{ marginBottom: 32 }}>
      <SectionHeader
        icon={<ThunderboltOutlined />}
        title="Skills 技能库"
        action={
          <button className="btn-ghost" onClick={load} title="刷新" style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
            <ReloadOutlined style={{ fontSize: 12 }} /> 刷新
          </button>
        }
      />

      {msg && (
        <div style={{
          marginBottom: 10, padding: "7px 12px", borderRadius: 8, fontSize: 12,
          background: msg.type === "ok" ? "var(--green-light, #f0fdf4)" : "var(--red-light, #fff1f0)",
          color: msg.type === "ok" ? "var(--green, #16a34a)" : "var(--red, #dc2626)",
          border: `1px solid ${msg.type === "ok" ? "var(--green, #16a34a)" : "var(--red, #dc2626)"}`,
          display: "flex", alignItems: "center", gap: 6,
        }}>
          {msg.type === "ok" ? <CheckCircleOutlined /> : <WarningOutlined />}
          {msg.text}
          <span style={{ marginLeft: "auto", cursor: "pointer", opacity: 0.5 }} onClick={() => setMsg(null)}>✕</span>
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: "center", padding: "24px 0", color: "var(--text-muted)", fontSize: 13 }}>加载中…</div>
      ) : skills.length === 0 ? (
        <div style={{ textAlign: "center", padding: "24px 0", color: "var(--text-muted)", fontSize: 13 }}>暂无 Skill</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {skills.map(s => (
            <div key={s.name} style={{
              display: "flex", alignItems: "flex-start", gap: 12,
              background: "var(--bg-card)", border: "1px solid var(--border)",
              borderRadius: "var(--r-md)", padding: "10px 14px",
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
                  <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text)" }}>{s.name}</span>
                  <Badge label={s.source === "builtin" ? "内置" : "自定义"} color={s.source === "builtin" ? "muted" : "primary"} />
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {s.description || "暂无描述"}
                </div>
              </div>
              {s.source === "workspace" && (
                <button
                  className="btn-ghost"
                  onClick={() => deleteSkill(s.name)}
                  disabled={deletingName === s.name}
                  title="删除"
                  style={{ color: "var(--red, #dc2626)", padding: "3px 7px", fontSize: 13, flexShrink: 0 }}
                >
                  <DeleteOutlined />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: 10, padding: "8px 12px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 11, color: "var(--text-muted)" }}>
        <b>内置 Skill</b> 不可删除。<b>自定义 Skill</b> 由 AI 在对话中自动创建，存放于 workspace/skills/ 目录。
      </div>
    </div>
  );
}

// ─── 子组件：MCP 服务器表单 ───────────────────────────────
function MCPForm({ initial, onSave, onCancel }: {
  initial?: MCPServer;
  onSave: (data: MCPServer) => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState({
    name: initial?.name || "",
    type: (initial?.type || "stdio") as TransportType,
    command: initial?.command || "",
    args_str: (initial?.args || []).join(" "),
    url: initial?.url || "",
    tool_timeout: initial?.toolTimeout ?? initial?.tool_timeout ?? 30,
    enabled_tools_str: (initial?.enabledTools ?? initial?.enabled_tools ?? ["*"]).join(", "),
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const set = (k: string, v: unknown) => setForm(f => ({ ...f, [k]: v }));

  const handleSave = async () => {
    if (!form.name.trim()) { setErr("服务器名称不能为空"); return; }
    if (form.type === "stdio" && !form.command.trim()) { setErr("stdio 模式必须填写命令"); return; }
    if ((form.type === "sse" || form.type === "streamableHttp") && !form.url.trim()) { setErr("URL 不能为空"); return; }
    setSaving(true);
    setErr(null);
    try {
      const payload: MCPServer = {
        name: form.name.trim(),
        type: form.type,
        tool_timeout: Number(form.tool_timeout) || 30,
        enabled_tools: form.enabled_tools_str.split(",").map(s => s.trim()).filter(Boolean),
      };
      if (form.type === "stdio") {
        payload.command = form.command.trim();
        if (form.args_str.trim()) payload.args = form.args_str.trim().split(/\s+/);
      } else {
        payload.url = form.url.trim();
      }
      await onSave(payload);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  };

  const inputStyle: React.CSSProperties = {
    width: "100%", padding: "6px 10px", borderRadius: 7,
    border: "1px solid var(--border)", background: "var(--bg-app)",
    color: "var(--text)", fontSize: 13, outline: "none", boxSizing: "border-box",
  };
  const labelStyle: React.CSSProperties = {
    display: "block", fontSize: 12, fontWeight: 600, color: "var(--text-muted)", marginBottom: 4,
  };

  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--primary)", borderRadius: "var(--r-md)",
      padding: 16, marginBottom: 12,
    }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 14, color: "var(--primary)" }}>
        {initial ? `编辑：${initial.name}` : "添加 MCP 服务器"}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <label style={labelStyle}>服务器名称 *</label>
          <input style={inputStyle} value={form.name} disabled={!!initial}
            onChange={e => set("name", e.target.value)} placeholder="my-mcp-server" />
        </div>
        <div>
          <label style={labelStyle}>传输类型</label>
          <select style={inputStyle} value={form.type} onChange={e => set("type", e.target.value)}>
            <option value="stdio">stdio（本地进程）</option>
            <option value="sse">SSE（HTTP 服务）</option>
            <option value="streamableHttp">Streamable HTTP</option>
          </select>
        </div>

        {form.type === "stdio" ? (
          <>
            <div>
              <label style={labelStyle}>命令 * (command)</label>
              <input style={inputStyle} value={form.command} onChange={e => set("command", e.target.value)} placeholder="npx 或 python" />
            </div>
            <div>
              <label style={labelStyle}>参数 (args，空格分隔)</label>
              <input style={inputStyle} value={form.args_str} onChange={e => set("args_str", e.target.value)} placeholder="-y @modelcontextprotocol/server-filesystem ." />
            </div>
          </>
        ) : (
          <div style={{ gridColumn: "span 2" }}>
            <label style={labelStyle}>URL *</label>
            <input style={inputStyle} value={form.url} onChange={e => set("url", e.target.value)} placeholder="http://localhost:3000/sse" />
          </div>
        )}

        <div>
          <label style={labelStyle}>超时 (秒)</label>
          <input style={inputStyle} type="number" value={form.tool_timeout} onChange={e => set("tool_timeout", e.target.value)} min={5} max={300} />
        </div>
        <div>
          <label style={labelStyle}>启用的工具 (逗号分隔，* = 全部)</label>
          <input style={inputStyle} value={form.enabled_tools_str} onChange={e => set("enabled_tools_str", e.target.value)} placeholder="* 或 tool1, tool2" />
        </div>
      </div>

      {err && (
        <div style={{ marginTop: 10, fontSize: 12, color: "var(--red, #dc2626)", display: "flex", alignItems: "center", gap: 5 }}>
          <WarningOutlined /> {err}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 14, justifyContent: "flex-end" }}>
        <button className="btn-ghost" onClick={onCancel} disabled={saving} style={{ fontSize: 13 }}>取消</button>
        <button
          onClick={handleSave} disabled={saving}
          style={{
            padding: "6px 18px", borderRadius: 8, border: "none", cursor: saving ? "not-allowed" : "pointer",
            background: "var(--primary)", color: "#fff", fontSize: 13, fontWeight: 600, opacity: saving ? 0.7 : 1,
          }}
        >
          {saving ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}

// ─── 子组件：MCP 服务器列表 ──────────────────────────────
function MCPSection() {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editTarget, setEditTarget] = useState<MCPServer | null>(null);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiFetch("/api/mcp/servers");
      setServers(data);
    } catch (e: any) {
      setMsg({ type: "err", text: e.message });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSave = async (data: MCPServer) => {
    await apiFetch("/api/mcp/servers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    setMsg({ type: "ok", text: `服务器「${data.name}」已保存，重启后端后生效` });
    setShowForm(false);
    setEditTarget(null);
    load();
  };

  const handleDelete = async (name: string) => {
    if (!window.confirm(`确定删除 MCP 服务器「${name}」？`)) return;
    try {
      await apiFetch(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      setMsg({ type: "ok", text: `服务器「${name}」已删除，重启后端后生效` });
      load();
    } catch (e: any) {
      setMsg({ type: "err", text: e.message });
    }
  };

  return (
    <div>
      <SectionHeader
        icon={<ApiOutlined />}
        title="MCP 服务器"
        action={
          <button
            onClick={() => { setShowForm(true); setEditTarget(null); }}
            style={{
              display: "flex", alignItems: "center", gap: 5, padding: "5px 13px",
              borderRadius: 8, border: "none", cursor: "pointer",
              background: "var(--primary)", color: "#fff", fontSize: 12, fontWeight: 600,
            }}
          >
            <PlusOutlined style={{ fontSize: 11 }} /> 添加服务器
          </button>
        }
      />

      {msg && (
        <div style={{
          marginBottom: 10, padding: "7px 12px", borderRadius: 8, fontSize: 12,
          background: msg.type === "ok" ? "var(--green-light, #f0fdf4)" : "var(--red-light, #fff1f0)",
          color: msg.type === "ok" ? "var(--green, #16a34a)" : "var(--red, #dc2626)",
          border: `1px solid ${msg.type === "ok" ? "var(--green, #16a34a)" : "var(--red, #dc2626)"}`,
          display: "flex", alignItems: "center", gap: 6,
        }}>
          {msg.type === "ok" ? <CheckCircleOutlined /> : <WarningOutlined />}
          {msg.text}
          <span style={{ marginLeft: "auto", cursor: "pointer", opacity: 0.5 }} onClick={() => setMsg(null)}>✕</span>
        </div>
      )}

      {(showForm && !editTarget) && (
        <MCPForm
          onSave={handleSave}
          onCancel={() => setShowForm(false)}
        />
      )}

      {loading ? (
        <div style={{ textAlign: "center", padding: "24px 0", color: "var(--text-muted)", fontSize: 13 }}>加载中…</div>
      ) : servers.length === 0 && !showForm ? (
        <div style={{ textAlign: "center", padding: "24px 0", color: "var(--text-muted)", fontSize: 13 }}>
          暂无 MCP 服务器配置<br />
          <span style={{ fontSize: 12 }}>点击「添加服务器」开始配置</span>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {servers.map(s => (
            editTarget?.name === s.name ? (
              <MCPForm
                key={s.name}
                initial={s}
                onSave={handleSave}
                onCancel={() => setEditTarget(null)}
              />
            ) : (
              <div key={s.name} style={{
                background: "var(--bg-card)", border: "1px solid var(--border)",
                borderRadius: "var(--r-md)", padding: "10px 14px",
              }}>
                <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                      <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text)" }}>{s.name}</span>
                      <Badge label={s.type || "stdio"} color="primary" />
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", display: "flex", gap: 12, flexWrap: "wrap" }}>
                      {s.command && (
                        <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
                          <CodeOutlined style={{ fontSize: 10 }} />
                          <code style={{ background: "var(--bg-app)", padding: "1px 5px", borderRadius: 4 }}>
                            {s.command} {(s.args || []).join(" ")}
                          </code>
                        </span>
                      )}
                      {s.url && <span><b>URL：</b>{s.url}</span>}
                      <span><b>超时：</b>{s.toolTimeout ?? s.tool_timeout ?? 30}s</span>
                      <span><b>工具：</b>{(s.enabledTools ?? s.enabled_tools ?? ["*"]).join(", ")}</span>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                    <button className="btn-ghost" onClick={() => setEditTarget(s)} title="编辑" style={{ fontSize: 12, padding: "3px 8px" }}>
                      编辑
                    </button>
                    <button className="btn-ghost" onClick={() => handleDelete(s.name)} title="删除"
                      style={{ color: "var(--red, #dc2626)", fontSize: 13, padding: "3px 7px" }}>
                      <DeleteOutlined />
                    </button>
                  </div>
                </div>
              </div>
            )
          ))}
        </div>
      )}

      <div style={{ marginTop: 10, padding: "8px 12px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 11, color: "var(--text-muted)" }}>
        注意：MCP 配置修改后需要<b>重启后端</b>才能生效（新连接在启动时建立）。
      </div>
    </div>
  );
}

// ─── 主组件 ───────────────────────────────────────────────
export default function CapabilitiesTab() {
  return (
    <div style={{ maxWidth: 760, margin: "0 auto" }}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontWeight: 800, fontSize: 20, color: "var(--text)", marginBottom: 4 }}>功能配置</div>
        <div style={{ fontSize: 13, color: "var(--text-muted)" }}>管理 AI 的 Skills 技能库和 MCP 工具服务器</div>
      </div>

      <SkillsSection />
      <MCPSection />
    </div>
  );
}
