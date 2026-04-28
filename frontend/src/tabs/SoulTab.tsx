/**
 * SoulTab.tsx — 灵魂系统 Tab（灵魂仓库 + 灵魂工坊）
 */
import { useState, useEffect } from "react";
import { API } from "../utils/api";

type SoulMaterial = {
  id: string; type: string; label: string; filename: string;
  analyzed: boolean; preview: string;
};
type SoulValidationResult = {
  valid: boolean; completeness: number;
  missing_required: string[]; missing_recommended: string[];
  missing_scenarios: string[]; warnings: string[];
};

type VaultSoul = {
  id: string; name: string; active: boolean; created_at: string;
  updated_at: string; completeness: number; valid: boolean; preview: string;
};

export function SoulTab() {
  // ── 视图切换 ──────────────────────────────────────────────────────────
  const [view, setView] = useState<"vault" | "workshop">("vault");

  // ── Vault State ───────────────────────────────────────────────────────
  const [vaultSouls, setVaultSouls] = useState<VaultSoul[]>([]);
  const [, setVaultActiveId] = useState<string | null>(null);
  const [editingSoul, setEditingSoul] = useState<{ id: string; name: string; content: string } | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editName, setEditName] = useState("");

  // ── Workshop State ────────────────────────────────────────────────────
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [materials, setMaterials] = useState<SoulMaterial[]>([]);
  const [analyses, setAnalyses] = useState<{ material_id: string; material_type: string; analysis_preview: string }[]>([]);
  const [soulMd, setSoulMd] = useState("");
  const [validation, setValidation] = useState<SoulValidationResult | null>(null);
  const [relationship, setRelationship] = useState("朋友");
  const [userNickname, setUserNickname] = useState("你");

  // UI state
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState("");
  const [msg, setMsg] = useState("");
  const [msgOk, setMsgOk] = useState(true);
  const [textInput, setTextInput] = useState("");
  const [textType, setTextType] = useState("setting");
  const [textLabel, setTextLabel] = useState("");
  const [refineInput, setRefineInput] = useState("");
  const [importMode, setImportMode] = useState(false);
  const [importText, setImportText] = useState("");
  const [showGuide, setShowGuide] = useState(false);
  const [guideContent, setGuideContent] = useState("");

  const flash = (text: string, ok = true) => { setMsg(text); setMsgOk(ok); setTimeout(() => setMsg(""), 4000); };

  // ── 初始化加载 ────────────────────────────────────────────────────────
  const reloadVault = () => {
    fetch(`${API}/api/soul/vault`).then(r => r.json()).then(d => {
      if (d && d.souls) { setVaultSouls(d.souls); setVaultActiveId(d.active_id ?? null); }
    }).catch(() => {});
  };

  useEffect(() => {
    reloadVault();
    fetch(`${API}/api/soul/materials`).then(r => r.json()).then(d => {
      if (d && d.materials) setMaterials(d.materials);
    }).catch(() => {});
  }, []);

  // ── Vault 操作 ────────────────────────────────────────────────────────
  const activateSoul = async (id: string) => {
    try {
      const res = await fetch(`${API}/api/soul/vault/${id}/activate`, { method: "POST" });
      const d = await res.json();
      if (d.ok) { flash(`[已激活]: ${d.name}`); reloadVault(); }
      else flash(`${d.error}`, false);
    } catch { flash("激活失败", false); }
  };

  const deleteSoul = async (id: string) => {
    try {
      const res = await fetch(`${API}/api/soul/vault/${id}`, { method: "DELETE" });
      const d = await res.json();
      if (d.ok) { flash("[已删除]"); reloadVault(); }
      else flash(`${d.error}`, false);
    } catch { flash("删除失败", false); }
  };

  const openEditor = async (id: string) => {
    try {
      const res = await fetch(`${API}/api/soul/vault/${id}`);
      const d = await res.json();
      if (d.content !== undefined) {
        setEditingSoul({ id: d.id, name: d.name, content: d.content });
        setEditContent(d.content);
        setEditName(d.name);
      } else flash(`${d.error}`, false);
    } catch { flash("加载失败", false); }
  };

  const saveEditor = async () => {
    if (!editingSoul) return;
    try {
      const res = await fetch(`${API}/api/soul/vault/${editingSoul.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editContent, name: editName }),
      });
      const d = await res.json();
      if (d.ok) { flash(`[已保存]: ${d.name}`); setEditingSoul(null); reloadVault(); }
      else flash(`${d.error}`, false);
    } catch { flash("保存失败", false); }
  };

  const addToVault = async (content: string, name?: string) => {
    try {
      const res = await fetch(`${API}/api/soul/vault`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, name: name || "" }),
      });
      const d = await res.json();
      if (d.ok) { flash(`[已入库]: ${d.name}`); reloadVault(); return d; }
      else flash(`${d.error}`, false);
    } catch { flash("入库失败", false); }
    return null;
  };

  // ── 素材管理 ──────────────────────────────────────────────────────────
  const addTextMaterial = async () => {
    if (!textInput.trim()) return;
    const formData = new FormData();
    formData.append("content", textInput);
    formData.append("material_type", textType);
    formData.append("label", textLabel || (textType === "setting" ? "角色设定" : textType === "dialogue" ? "角色台词" : "素材"));
    try {
      const res = await fetch(`${API}/api/soul/materials`, { method: "POST", body: formData });
      const d = await res.json();
      if (d.ok) {
        flash(`[已添加]素材: ${d.material.label}`);
        setTextInput(""); setTextLabel("");
        reloadMaterials();
      } else flash(`${d.error}`, false);
    } catch { flash("请求失败", false); }
  };

  const addFileMaterial = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    formData.append("material_type", file.type.startsWith("image/") ? "image" : "general");
    try {
      const res = await fetch(`${API}/api/soul/materials`, { method: "POST", body: formData });
      const d = await res.json();
      if (d.ok) { flash(`[已上传]: ${d.material.label}`); reloadMaterials(); }
      else flash(`${d.error}`, false);
    } catch { flash("上传失败", false); }
    e.target.value = "";
  };

  const removeMaterial = async (id: string) => {
    await fetch(`${API}/api/soul/materials/${id}`, { method: "DELETE" });
    reloadMaterials();
  };

  const reloadMaterials = () => {
    fetch(`${API}/api/soul/materials`).then(r => r.json()).then(d => setMaterials(d.materials ?? [])).catch(() => {});
  };

  // ── 分析 ──────────────────────────────────────────────────────────────
  const doAnalyze = async () => {
    setLoading(true); setLoadingMsg("正在分析素材…每份约 10-30 秒");
    try {
      const res = await fetch(`${API}/api/soul/analyze`, { method: "POST" });
      const d = await res.json();
      if (d.ok) {
        setAnalyses(d.analyses ?? []);
        flash(`[分析完成]，共 ${d.total_analyzed} 份结果`);
        reloadMaterials();
        setStep(3);
      } else flash(`${d.error}`, false);
    } catch { flash("分析失败", false); }
    setLoading(false); setLoadingMsg("");
  };

  // ── 合成 ──────────────────────────────────────────────────────────────
  const doGenerate = async () => {
    setLoading(true); setLoadingMsg("正在合成灵魂…约 30-60 秒");
    try {
      const res = await fetch(`${API}/api/soul/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ relationship, user_nickname: userNickname }),
      });
      const d = await res.json();
      if (d.ok) {
        setSoulMd(d.soul_md);
        setValidation(d.validation ?? null);
        flash("[灵魂合成完成]");
        setStep(4);
      } else flash(`${d.error}`, false);
    } catch { flash("合成失败", false); }
    setLoading(false); setLoadingMsg("");
  };

  // ── 微调 ──────────────────────────────────────────────────────────────
  const doRefine = async () => {
    if (!refineInput.trim()) return;
    setLoading(true); setLoadingMsg("正在微调…");
    try {
      const res = await fetch(`${API}/api/soul/refine`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback: refineInput }),
      });
      const d = await res.json();
      if (d.ok) {
        setSoulMd(d.soul_md);
        setValidation(d.validation ?? null);
        flash("[微调完成]");
        setRefineInput("");
      } else flash(`${d.error}`, false);
    } catch { flash("微调失败", false); }
    setLoading(false); setLoadingMsg("");
  };

  // ── 导入 ──────────────────────────────────────────────────────────────
  const doImport = async () => {
    if (!importText.trim()) return;
    try {
      const res = await fetch(`${API}/api/soul/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: importText }),
      });
      const d = await res.json();
      if (d.ok) {
        setSoulMd(importText);
        setValidation(d.validation ?? null);
        flash("[导入成功]");
        setImportMode(false); setImportText("");
        setStep(4);
      } else flash(`${d.error}`, false);
    } catch { flash("导入失败", false); }
  };

  // ── 查看指南 ──────────────────────────────────────────────────────────
  const loadGuide = async () => {
    if (guideContent) { setShowGuide(true); return; }
    try {
      const res = await fetch(`${API}/api/soul/guide`);
      const d = await res.json();
      setGuideContent(d.content ?? "");
      setShowGuide(true);
    } catch { flash("无法加载指南", false); }
  };

  // ── Helpers ───────────────────────────────────────────────────────────
  const typeLabel: Record<string, string> = { setting: "设定", dialogue: "台词", image: " 图片", text: "文本", general: "通用" };
  const stepLabels = ["", "① 准备素材", "② 分析素材", "③ 合成灵魂", "④ 预览 & 保存"];

  // ── Render ────────────────────────────────────────────────────────────
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

      {/* ══════ 顶部：标题 + 视图切换 ══════ */}
      <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "16px 22px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
          <div>
            <div style={{ fontWeight: 800, fontSize: 15, color: "var(--text)" }}>灵魂系统</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>管理和创建 Navi 的角色灵魂</div>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <button onClick={loadGuide} style={{
              background: "transparent", border: `1px solid ${"var(--border)"}`,
              borderRadius: 8, padding: "5px 12px", fontSize: 11, color: "var(--text-sub)", cursor: "pointer",
            }}>指南</button>
          </div>
        </div>
        {/* 视图切换 Tab */}
        <div style={{ display: "flex", gap: 0, background: "var(--bg-hover)", borderRadius: "var(--r-md)", padding: 3 }}>
          {([["vault", "灵魂仓库"], ["workshop", "灵魂工坊"]] as const).map(([k, label]) => (
            <button key={k} onClick={() => setView(k)} style={{
              flex: 1, padding: "7px 0", borderRadius: 8, border: "none", fontSize: 12, fontWeight: 700,
              background: view === k ? "#fff" : "transparent",
              color: view === k ? "var(--primary)" : "var(--text-muted)",
              boxShadow: view === k ? "0 1px 4px rgba(0,0,0,0.08)" : "none",
              cursor: "pointer", transition: "all .15s",
            }}>{label}</button>
          ))}
        </div>
      </div>

      {/* 提示消息 */}
      {msg && (
        <div style={{
          padding: "8px 16px", borderRadius: 10, fontSize: 12, fontWeight: 600,
          background: msgOk ? "var(--green-bg)" : "var(--red-bg)", color: msgOk ? "var(--green)" : "var(--red)",
        }}>{msg}</div>
      )}

      {/* ══════ 仓库视图 ══════ */}
      {view === "vault" && !editingSoul && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {vaultSouls.length === 0 ? (
            <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "40px 24px", textAlign: "center" }}>
              <div style={{ fontSize: 32, marginBottom: 12, color: "var(--text-muted)" }}>
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22c4-4 8-8 8-13A8 8 0 0 0 4 9c0 5 4 9 8 13z"/><path d="M12 22V10"/></svg>
              </div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text)", marginBottom: 6 }}>灵魂仓库为空</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 16 }}>去灵魂工坊创建你的第一个角色灵魂吧</div>
              <button onClick={() => setView("workshop")} style={{
                background: "var(--accent, #7c3aed)", color: "#fff",
                border: "none", borderRadius: 10, padding: "8px 24px",
                fontWeight: 700, fontSize: 13, cursor: "pointer",
              }}>前往工坊</button>
            </div>
          ) : (
            vaultSouls.map(s => (
              <div key={s.id} style={{
                background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "16px 20px",
                borderLeft: s.active ? `4px solid ${"var(--green)"}` : `4px solid transparent`,
              }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 16 }}>{s.active ? "" : ""}</span>
                    <span style={{ fontWeight: 700, fontSize: 14, color: "var(--text)" }}>{s.name}</span>
                    {s.active && <span style={{
                      fontSize: 10, fontWeight: 700, color: "var(--green)",
                      background: "var(--green-bg)", padding: "2px 8px", borderRadius: 6,
                    }}>当前生效</span>}
                    <span style={{
                      fontSize: 10, fontWeight: 600,
                      color: s.completeness >= 0.8 ? "var(--green)" : s.completeness < 0.3 ? "var(--red)" : "var(--yellow)",
                    }}>完整度 {Math.round(s.completeness * 100)}%</span>
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    {!s.active && (
                      <button onClick={() => activateSoul(s.id)} style={{
                background: "var(--green, #059669)", color: "#fff",
                border: "none", borderRadius: 8, padding: "5px 14px",
                fontWeight: 700, fontSize: 11, cursor: "pointer",
              }}>激活</button>
                    )}
                    <button onClick={() => openEditor(s.id)} style={{
                      background: "transparent", border: `1px solid ${"var(--border)"}`,
                      borderRadius: 8, padding: "5px 12px", fontSize: 11, color: "var(--text-sub)", cursor: "pointer",
                    }}>编辑</button>
                    {!s.active && (
                      <button onClick={() => { if (confirm(`确定删除「${s.name}」？`)) deleteSoul(s.id); }} style={{
                        background: "transparent", border: `1px solid ${"var(--border)"}`,
                        borderRadius: 8, padding: "5px 10px", fontSize: 11, color: "var(--text-muted)", cursor: "pointer",
                      }}>删除</button>
                    )}
                  </div>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-sub)", lineHeight: 1.6 }}>{s.preview}</div>
              </div>
            ))
          )}
        </div>
      )}

      {/* ══════ 仓库：编辑器 ══════ */}
      {view === "vault" && editingSoul && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <div style={{ fontWeight: 700, fontSize: 14, color: "var(--text)" }}>编辑灵魂</div>
              <button onClick={() => setEditingSoul(null)} style={{
                background: "none", border: "none", fontSize: 18, color: "var(--text-muted)", cursor: "pointer",
              }}>×</button>
            </div>
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 11, color: "var(--text-sub)", marginBottom: 4 }}>灵魂名称</div>
              <input value={editName} onChange={e => setEditName(e.target.value)} style={{
                width: "100%", boxSizing: "border-box", background: "var(--bg-app)",
                border: `1.5px solid ${"var(--border)"}`, borderRadius: 8,
                padding: "8px 12px", fontSize: 13, fontWeight: 600, color: "var(--text)", outline: "none",
              }} />
            </div>
            <textarea
              value={editContent}
              onChange={e => setEditContent(e.target.value)}
              rows={20}
              style={{
                width: "100%", boxSizing: "border-box",
                background: "var(--bg-app)", border: `1.5px solid ${"var(--border)"}`,
                borderRadius: 10, padding: "12px 16px", color: "var(--text)", fontSize: 12,
                outline: "none", resize: "vertical", fontFamily: "monospace", lineHeight: 1.7,
              }}
            />
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button onClick={saveEditor} style={{
                background: "var(--green, #059669)", color: "#fff",
                border: "none", borderRadius: 10, padding: "8px 22px",
                fontWeight: 700, fontSize: 13, cursor: "pointer",
              }}>保存</button>
              <button onClick={() => setEditingSoul(null)} style={{
                background: "transparent", border: `1px solid ${"var(--border)"}`,
                borderRadius: 8, padding: "8px 16px", fontSize: 12, color: "var(--text-sub)", cursor: "pointer",
              }}>取消</button>
            </div>
          </div>
        </div>
      )}

      {/* ══════ 工坊视图 ══════ */}
      {view === "workshop" && <>

      {/* 工坊顶部：步骤指示器 */}
      <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "14px 22px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-sub)" }}>从素材生成灵魂</div>
          <button onClick={() => setImportMode(!importMode)} style={{
            background: importMode ? "var(--primary-light)" : "transparent",
            border: `1px solid ${importMode ? "var(--primary)" : "var(--border)"}`,
            borderRadius: 8, padding: "5px 12px", fontSize: 11,
            color: importMode ? "var(--primary)" : "var(--text-sub)", cursor: "pointer",
          }}>直接导入</button>
        </div>

        {/* 步骤条 */}
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          {[1, 2, 3, 4].map(s => (
            <div key={s} style={{ display: "flex", alignItems: "center", flex: 1, gap: 4 }}>
              <div
                onClick={() => { if (s <= step || (s === 2 && materials.length > 0)) setStep(s as 1|2|3|4); }}
                style={{
                  width: 28, height: 28, borderRadius: "50%", display: "flex",
                  alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700,
                  background: step >= s ? "linear-gradient(135deg,#0891b2,#06b6d4)" : "#e5e7eb",
                  color: step >= s ? "#fff" : "var(--text-muted)", cursor: s <= step ? "pointer" : "default",
                  transition: "all .2s",
                }}>{s}</div>
              <span style={{ fontSize: 11, color: step >= s ? "var(--text)" : "var(--text-muted)", fontWeight: step === s ? 700 : 400 }}>
                {stepLabels[s]}
              </span>
              {s < 4 && <div style={{ flex: 1, height: 2, background: step > s ? "var(--primary)" : "#e5e7eb", borderRadius: 1, marginLeft: 4, marginRight: 4 }} />}
            </div>
          ))}
        </div>
      </div>

      {/* Loading 遮罩 */}
      {loading && (
        <div style={{
          background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "20px 24px", textAlign: "center",
        }}>
          <div style={{
            width: 24, height: 24, border: `3px solid ${"var(--border)"}`,
            borderTopColor: "var(--primary)", borderRadius: "50%",
            animation: "spin 0.8s linear infinite", margin: "0 auto 12px",
          }} />
          <div style={{ fontSize: 13, color: "var(--text)", fontWeight: 600 }}>{loadingMsg}</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>请耐心等待，不要关闭页面</div>
        </div>
      )}

      {/* ── 导入模式 ──────────────────────────────────────────────── */}
      {importMode && !loading && (
        <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
          <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)", marginBottom: 4 }}>导入 soul.md</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 12 }}>
            把你在其他 AI 生成的 soul.md 内容粘贴到下方
          </div>
          <textarea
            value={importText}
            onChange={e => setImportText(e.target.value)}
            placeholder={"# 角色名 · Navi 的灵魂\n\n## 身份\n...\n\n## 性格内核\n..."}
            rows={12}
            style={{
              width: "100%", boxSizing: "border-box",
              background: "var(--bg-app)", border: `1.5px solid ${"var(--border)"}`,
              borderRadius: 10, padding: "10px 14px", color: "var(--text)", fontSize: 12,
              outline: "none", resize: "vertical", fontFamily: "monospace", lineHeight: 1.6,
            }}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
            <button onClick={doImport} disabled={!importText.trim()} style={{
              background: importText.trim() ? "linear-gradient(135deg,#0891b2,#06b6d4)" : "#d1d5db",
              color: "var(--text-inverse)", border: "none", borderRadius: 10, padding: "8px 20px",
              fontWeight: 700, fontSize: 12, cursor: importText.trim() ? "pointer" : "not-allowed",
            }}>导入并校验</button>
            <button onClick={() => setImportMode(false)} style={{
              background: "transparent", border: `1px solid ${"var(--border)"}`,
              borderRadius: 8, padding: "6px 14px", fontSize: 11, color: "var(--text-sub)", cursor: "pointer",
            }}>取消</button>
          </div>
        </div>
      )}

      {/* ── 指南弹窗 ──────────────────────────────────────────────── */}
      {showGuide && (
        <div style={{
          position: "fixed", top: 0, left: 0, right: 0, bottom: 0, zIndex: 999,
          background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center",
        }} onClick={() => setShowGuide(false)}>
          <div onClick={e => e.stopPropagation()} style={{
            width: "85%", maxWidth: 700, maxHeight: "80vh", overflowY: "auto",
            background: "var(--bg-app)", borderRadius: 16, padding: "24px 28px",
            boxShadow: "0 8px 40px rgba(0,0,0,0.2)",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <div style={{ fontWeight: 800, fontSize: 16, color: "var(--text)" }}>灵魂制作指南</div>
              <button onClick={() => setShowGuide(false)} style={{
                background: "none", border: "none", fontSize: 20, color: "var(--text-muted)", cursor: "pointer",
              }}>×</button>
            </div>
            <pre style={{
              whiteSpace: "pre-wrap", wordBreak: "break-word",
              fontSize: 12, lineHeight: 1.8, color: "var(--text)", fontFamily: "inherit",
            }}>{guideContent}</pre>
          </div>
        </div>
      )}

      {/* ── Step 1: 准备素材 ──────────────────────────────────────── */}
      {step === 1 && !loading && !importMode && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* 添加文本素材 */}
          <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
            <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)", marginBottom: 12 }}>添加文本素材</div>
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <div>
                <div style={{ fontSize: 11, color: "var(--text-sub)", marginBottom: 4 }}>素材类型</div>
                <select value={textType} onChange={e => setTextType(e.target.value)} style={{
                  background: "var(--bg-app)", border: `1.5px solid ${"var(--border)"}`,
                  borderRadius: 8, padding: "6px 10px", fontSize: 12, color: "var(--text)", outline: "none",
                }}>
                  <option value="setting">角色设定</option>
                  <option value="dialogue">角色台词</option>
                  <option value="general">其他素材</option>
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 11, color: "var(--text-sub)", marginBottom: 4 }}>标签（可选）</div>
                <input value={textLabel} onChange={e => setTextLabel(e.target.value)}
                  placeholder="如：萌娘百科设定、游戏语音文本…"
                  style={{
                    width: "100%", boxSizing: "border-box", background: "var(--bg-app)",
                    border: `1.5px solid ${"var(--border)"}`, borderRadius: 8,
                    padding: "6px 10px", fontSize: 12, color: "var(--text)", outline: "none",
                  }}
                />
              </div>
            </div>
            <textarea
              value={textInput}
              onChange={e => setTextInput(e.target.value)}
              placeholder="粘贴角色设定、台词、对话语料等文本内容…"
              rows={8}
              style={{
                width: "100%", boxSizing: "border-box",
                background: "var(--bg-app)", border: `1.5px solid ${"var(--border)"}`,
                borderRadius: 10, padding: "10px 14px", color: "var(--text)", fontSize: 12,
                outline: "none", resize: "vertical", lineHeight: 1.6,
              }}
            />
            <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
              <button onClick={addTextMaterial} disabled={!textInput.trim()} style={{
                background: textInput.trim() ? "linear-gradient(135deg,#0891b2,#06b6d4)" : "#d1d5db",
                color: "var(--text-inverse)", border: "none", borderRadius: 10, padding: "8px 18px",
                fontWeight: 700, fontSize: 12, cursor: textInput.trim() ? "pointer" : "not-allowed",
              }}>＋ 添加素材</button>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>或</span>
              <label style={{
                background: "transparent", border: `1px solid ${"var(--border)"}`,
                borderRadius: 8, padding: "6px 14px", fontSize: 11, color: "var(--text-sub)",
                cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4,
              }}>
                上传文件/图片
                <input type="file" accept=".txt,.md,.json,.jpg,.jpeg,.png,.gif,.webp" onChange={addFileMaterial} style={{ display: "none" }} />
              </label>
            </div>
          </div>

          {/* 已添加的素材列表 */}
          <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
            <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)", marginBottom: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>已添加素材 ({materials.length})</span>
              {materials.length > 0 && (
                <button onClick={() => setStep(2)} style={{
                  background: "linear-gradient(135deg,#0891b2,#06b6d4)", color: "var(--text-inverse)",
                  border: "none", borderRadius: 10, padding: "7px 18px",
                  fontWeight: 700, fontSize: 12, cursor: "pointer",
                  boxShadow: "0 2px 8px rgba(6,182,212,0.25)",
                }}>下一步：分析 →</button>
              )}
            </div>
            {materials.length === 0 ? (
              <div style={{ textAlign: "center", padding: "24px 0", color: "var(--text-muted)", fontSize: 12 }}>
                还没有素材，请在上方添加
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {materials.map(m => (
                  <div key={m.id} style={{
                    display: "flex", alignItems: "center", gap: 10, padding: "8px 12px",
                    background: m.analyzed ? "var(--green-bg)" : "rgba(0,0,0,0.02)", borderRadius: 8,
                  }}>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{typeLabel[m.type] ?? "文件"}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {m.label || m.filename || m.id}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-muted)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {m.preview}
                    </span>
                    {m.analyzed && <span style={{ fontSize: 10, color: "var(--green)", fontWeight: 600 }}>已分析</span>}
                    <button onClick={() => removeMaterial(m.id)} style={{
                      background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 14,
                    }}>×</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Step 2: 分析 ──────────────────────────────────────────── */}
      {step === 2 && !loading && !importMode && (
        <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
          <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)", marginBottom: 8 }}>分析素材</div>
          <div style={{ fontSize: 12, color: "var(--text-sub)", marginBottom: 16, lineHeight: 1.8 }}>
            将对 <b>{materials.filter(m => !m.analyzed).length}</b> 份未分析的素材调用 AI 进行角色分析。<br/>
            每份素材约需 10-30 秒，请确保 LLM API 已正确配置。
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => setStep(1)} style={{
              background: "transparent", border: `1px solid ${"var(--border)"}`,
              borderRadius: 8, padding: "8px 16px", fontSize: 12, color: "var(--text-sub)", cursor: "pointer",
            }}>← 返回添加素材</button>
            <button onClick={doAnalyze} style={{
              background: "linear-gradient(135deg,#0891b2,#06b6d4)", color: "var(--text-inverse)",
              border: "none", borderRadius: 10, padding: "8px 24px",
              fontWeight: 700, fontSize: 13, cursor: "pointer",
              boxShadow: "0 3px 10px rgba(6,182,212,0.3)",
            }}>开始分析</button>
          </div>

          {/* 分析结果预览 */}
          {analyses.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 8 }}>分析结果预览</div>
              {analyses.map((a, i) => (
                <div key={i} style={{
                  background: "rgba(0,0,0,0.02)", borderRadius: 8, padding: "8px 12px", marginBottom: 6,
                }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--primary)", marginBottom: 4 }}>
                    {typeLabel[a.material_type] ?? "文件"} {a.material_id}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-sub)", lineHeight: 1.6 }}>{a.analysis_preview}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Step 3: 合成 ──────────────────────────────────────────── */}
      {step === 3 && !loading && !importMode && (
        <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
          <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)", marginBottom: 12 }}>合成灵魂</div>
          <div style={{ fontSize: 12, color: "var(--text-sub)", marginBottom: 16, lineHeight: 1.8 }}>
            分析完成！现在需要你补充一些信息，告诉 AI 你和这个角色的关系。
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-sub)", marginBottom: 4 }}>你和角色的关系</div>
              <input value={relationship} onChange={e => setRelationship(e.target.value)}
                placeholder="如：博士和助手、朋友、同事…"
                style={{
                  width: "100%", boxSizing: "border-box", background: "var(--bg-app)",
                  border: `1.5px solid ${"var(--border)"}`, borderRadius: 8,
                  padding: "8px 12px", fontSize: 12, color: "var(--text)", outline: "none",
                }}
              />
            </div>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-sub)", marginBottom: 4 }}>你希望她叫你什么</div>
              <input value={userNickname} onChange={e => setUserNickname(e.target.value)}
                placeholder="如：博士、主人、你这家伙…"
                style={{
                  width: "100%", boxSizing: "border-box", background: "var(--bg-app)",
                  border: `1.5px solid ${"var(--border)"}`, borderRadius: 8,
                  padding: "8px 12px", fontSize: 12, color: "var(--text)", outline: "none",
                }}
              />
            </div>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => setStep(2)} style={{
              background: "transparent", border: `1px solid ${"var(--border)"}`,
              borderRadius: 8, padding: "8px 16px", fontSize: 12, color: "var(--text-sub)", cursor: "pointer",
            }}>← 返回</button>
            <button onClick={doGenerate} style={{
              background: "linear-gradient(135deg,#8b5cf6,#a78bfa)", color: "var(--text-inverse)",
              border: "none", borderRadius: 10, padding: "8px 24px",
              fontWeight: 700, fontSize: 13, cursor: "pointer",
              boxShadow: "0 3px 10px rgba(139,92,246,0.3)",
            }}>合成灵魂</button>
          </div>
        </div>
      )}

      {/* ── Step 4: 预览 & 保存 ──────────────────────────────────── */}
      {step === 4 && !loading && !importMode && soulMd && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* 校验结果 */}
          {validation && (
            <div style={{
              background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "14px 18px",
              borderLeft: `4px solid ${validation.valid ? "var(--green)" : validation.completeness < 0.3 ? "var(--red)" : "var(--yellow)"}`,
            }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text)" }}>
                    {validation.valid ? "灵魂完整" : validation.completeness < 0.3 ? "灵魂不完整" : "部分内容缺失"}
                  </span>
                  <span style={{
                    marginLeft: 10, fontSize: 11, fontWeight: 600,
                    color: validation.completeness >= 0.8 ? "var(--green)" : validation.completeness < 0.3 ? "var(--red)" : "var(--yellow)",
                  }}>
                    完整度 {Math.round(validation.completeness * 100)}%
                  </span>
                </div>
                {validation.completeness < 0.5 && (
                  <button onClick={() => setStep(1)} style={{
                    background: "linear-gradient(135deg,#8b5cf6,#a78bfa)", color: "var(--text-inverse)",
                    border: "none", borderRadius: 10, padding: "6px 16px",
                    fontWeight: 700, fontSize: 12, cursor: "pointer",
                  }}>重新生成完整灵魂</button>
                )}
              </div>
              {validation.completeness < 0.3 && (
                <div style={{ fontSize: 12, color: "var(--text-sub)", marginTop: 8, lineHeight: 1.7 }}>
                  当前是一份基础版灵魂，缺少角色设定、说话风格、场景反应等关键内容。<br/>
                  点击「重新生成完整灵魂」从 Step 1 开始，上传角色素材来生成一份完整的灵魂。
                </div>
              )}
              {validation.missing_required?.length > 0 && (
                <div style={{ fontSize: 11, color: "var(--red)", marginTop: 6 }}>
                  缺少必须章节: {validation.missing_required.join("、")}
                </div>
              )}
              {validation.missing_recommended?.length > 0 && (
                <div style={{ fontSize: 11, color: "var(--yellow)", marginTop: 4 }}>
                  建议补充: {validation.missing_recommended.join("、")}
                </div>
              )}
              {(validation.warnings ?? []).map((w, i) => (
                <div key={i} style={{ fontSize: 11, color: "var(--text-sub)", marginTop: 3 }}>{w}</div>
              ))}
            </div>
          )}

          {/* soul.md 预览 */}
          <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
            <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)", marginBottom: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>soul.md 预览</span>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{soulMd.length} 字</span>
            </div>
            <pre style={{
              whiteSpace: "pre-wrap", wordBreak: "break-word",
              fontSize: 12, lineHeight: 1.8, color: "var(--text)",
              maxHeight: 400, overflowY: "auto", fontFamily: "inherit",
              background: "rgba(0,0,0,0.02)", borderRadius: 10, padding: "14px 16px",
            }}>{soulMd}</pre>
          </div>

          {/* 微调 */}
          <div style={{ background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "18px 22px" }}>
            <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text)", marginBottom: 8 }}>微调</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8 }}>
              如果有不满意的地方，描述你的修改意见
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <input value={refineInput} onChange={e => setRefineInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && doRefine()}
                placeholder="如：她说话应该更毒舌一点、日报评语太温柔了…"
                style={{
                  flex: 1, background: "var(--bg-app)",
                  border: `1.5px solid ${"var(--border)"}`, borderRadius: 10,
                  padding: "8px 14px", color: "var(--text)", fontSize: 12, outline: "none",
                }}
              />
              <button onClick={doRefine} disabled={!refineInput.trim()} style={{
                background: refineInput.trim() ? "linear-gradient(135deg,#f59e0b,#fbbf24)" : "#d1d5db",
                color: refineInput.trim() ? "#78350f" : "#fff", border: "none", borderRadius: 10,
                padding: "8px 18px", fontWeight: 700, fontSize: 12,
                cursor: refineInput.trim() ? "pointer" : "not-allowed",
              }}>微调</button>
            </div>
          </div>

          {/* 操作按钮 */}
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <button onClick={async () => {
              const result = await addToVault(soulMd);
              if (result) {
                await activateSoul(result.id);
                flash(`[已入库]并激活: ${result.name}`);
                setView("vault");
              }
            }} style={{
              background: "linear-gradient(135deg,#059669,#10b981)", color: "var(--text-inverse)",
              border: "none", borderRadius: 12, padding: "10px 28px",
              fontWeight: 700, fontSize: 13, cursor: "pointer",
              boxShadow: "0 4px 14px rgba(5,150,105,0.35)",
            }}>入库并激活</button>
            <button onClick={async () => { await addToVault(soulMd); }} style={{
              background: "transparent", border: `1px solid ${"var(--primary)"}`,
              borderRadius: 10, padding: "8px 16px", fontSize: 12, color: "var(--primary)", cursor: "pointer",
            }}>仅入库（不激活）</button>
            <button onClick={() => setStep(1)} style={{
              background: "transparent", border: `1px solid ${"var(--border)"}`,
              borderRadius: 10, padding: "8px 16px", fontSize: 12, color: "var(--text-sub)", cursor: "pointer",
            }}>← 重新开始</button>
          </div>
        </div>
      )}

      </>}
    </div>
  );
}

