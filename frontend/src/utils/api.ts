/**
 * api.ts — 后端 API 请求工具函数
 */

export const API = "http://localhost:8000";

// ── 应用规则管理 API ──
export async function fetchAppRules() {
  const res = await fetch(`${API}/api/app-rules`);
  return res.json();
}

export async function upsertAppRule(process_name: string, category: string) {
  const res = await fetch(`${API}/api/app-rules`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ process_name, category }),
  });
  return res.json();
}

export async function deleteAppRule(process_name: string) {
  const res = await fetch(`${API}/api/app-rules/${encodeURIComponent(process_name)}`, {
    method: "DELETE",
  });
  return res.json();
}

export async function fetchStatus() {
  const res = await fetch(`${API}/api/status`);
  return res.json();
}

export async function fetchActivities() {
  const res = await fetch(`${API}/api/activities/today`);
  return res.json();
}

export async function fetchConfig() {
  const res = await fetch(`${API}/api/config`);
  return res.json();
}

export async function saveConfig(cfg: Record<string, unknown>) {
  const res = await fetch(`${API}/api/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
  return res.json();
}

// ── 主动对话引擎 API ──
export async function fetchProactiveConfig() {
  const res = await fetch(`${API}/api/proactive/config`);
  return res.json();
}

export async function saveProactiveConfig(cfg: Record<string, unknown>) {
  const res = await fetch(`${API}/api/proactive/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
  return res.json();
}

export async function fetchProactiveStatus() {
  const res = await fetch(`${API}/api/proactive/status`);
  return res.json();
}

export async function testProactiveTrigger() {
  const res = await fetch(`${API}/api/proactive/test`, { method: "POST" });
  return res.json();
}
