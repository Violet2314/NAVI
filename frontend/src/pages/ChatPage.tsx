import { useState, useEffect, useRef, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { motion } from "framer-motion";
import { ArrowUpOutlined, ToolOutlined, MessageOutlined, PlusOutlined, PaperClipOutlined, FileImageOutlined, WechatOutlined, WifiOutlined, DisconnectOutlined } from "@ant-design/icons";
import { gsap } from "gsap";
import { inferEmotion } from "../live2d/constants";

const API = "http://localhost:8000";

type MsgRole = "user" | "assistant" | "tool";

interface ChatMsg {
  id: string;
  role: MsgRole;
  content: string;
  ts: string;
  images?: string[];   // base64 data URLs 或远程 URL
}

function nowTime() {
  return new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}
function msgId() {
  return Math.random().toString(36).slice(2);
}

function NaviBubble({ content, images }: { content: string; images?: string[] }) {
  return (
    <div className="navi-bubble">
      {images && images.length > 0 && (
        <div className="chat-msg-images">
          {images.map((src, i) => (
            <img
              key={i}
              src={src}
              alt="图片"
              className="chat-msg-image"
              onClick={() => window.open(src, "_blank")}
            />
          ))}
        </div>
      )}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function StreamCursor() {
  return <span className="stream-cursor" />;
}

const WECHAT_SESSION_ID = "__wechat__";

// ── 微信状态条 ───────────────────────────────────────────────────────────────
function WeChatStatusBar() {
  const [status, setStatus] = useState<{online:boolean;account_id:string;has_credentials:boolean} | null>(null);
  const [showLoginModal, setShowLoginModal] = useState(false);
  const [qrUrl, setQrUrl] = useState("");
  const [loginStatus, setLoginStatus] = useState<"idle"|"pending"|"scanned"|"confirmed"|"error"|"expired">("idle");
  const [loginError, setLoginError] = useState("");
  const evtRef = useRef<EventSource | null>(null);

  const fetchStatus = async () => {
    try {
      const r = await fetch(`${API}/api/wechat/status`);
      setStatus(await r.json());
    } catch { /* ignore */ }
  };

  useEffect(() => {
    fetchStatus();
    const t = setInterval(fetchStatus, 8000);
    return () => clearInterval(t);
  }, []);

  const startLogin = () => {
    setShowLoginModal(true);
    setQrUrl(""); setLoginStatus("pending"); setLoginError("");
    evtRef.current?.close();
    const es = new EventSource(`${API}/api/wechat/qr-login`);
    evtRef.current = es;
    es.onmessage = (e) => {
      const d = JSON.parse(e.data);
      setLoginStatus(d.status);
      if (d.qrcode_url) setQrUrl(d.qrcode_url);
      if (d.error) setLoginError(d.error);
      if (d.status === "confirmed" || d.status === "started") {
        es.close(); fetchStatus();
        setTimeout(() => setShowLoginModal(false), 1500);
      }
      if (d.status === "error" || d.status === "expired") es.close();
    };
    es.onerror = () => { setLoginStatus("error"); setLoginError("连接中断"); es.close(); };
  };

  const doLogout = async () => {
    await fetch(`${API}/api/wechat/logout`, { method: "POST" });
    fetchStatus();
  };

  return (
    <>
      <div style={{
        display: "flex", alignItems: "center", gap: 8, padding: "8px 16px",
        borderBottom: "1px solid var(--border)", background: "var(--bg-card)",
        fontSize: 12,
      }}>
        <WechatOutlined style={{ color: "#07c160", fontSize: 15 }} />
        <span style={{ color: "var(--text-sub)", fontWeight: 500 }}>微信对话</span>
        <span style={{
          marginLeft: 4, padding: "1px 7px", borderRadius: 99, fontSize: 11,
          background: status?.online ? "#e6f9ef" : "var(--bg-app)",
          color: status?.online ? "#07c160" : "var(--text-muted)",
          border: `1px solid ${status?.online ? "#07c160" : "var(--border)"}`,
        }}>
          {status?.online
            ? <><WifiOutlined style={{ marginRight: 3 }} />已连接</>
            : <><DisconnectOutlined style={{ marginRight: 3 }} />未登录</>}
        </span>
        <div style={{ flex: 1 }} />
        {status?.online ? (
          <button onClick={doLogout} style={{
            fontSize: 11, padding: "2px 10px", borderRadius: 6, border: "1px solid var(--border)",
            background: "transparent", color: "var(--text-muted)", cursor: "pointer",
          }}>退出登录</button>
        ) : (
          <button onClick={startLogin} style={{
            fontSize: 11, padding: "2px 10px", borderRadius: 6, border: "1px solid #07c160",
            background: "#07c160", color: "#fff", cursor: "pointer",
          }}>扫码登录</button>
        )}
      </div>

      {/* 登录 Modal */}
      {showLoginModal && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 9999,
          background: "rgba(0,0,0,0.45)", display: "flex", alignItems: "center", justifyContent: "center",
        }} onClick={() => { setShowLoginModal(false); evtRef.current?.close(); }}>
          <div style={{
            background: "var(--bg-card)", borderRadius: 16, padding: "32px 40px",
            minWidth: 280, textAlign: "center", boxShadow: "0 20px 60px rgba(0,0,0,0.25)",
          }} onClick={e => e.stopPropagation()}>
            <WechatOutlined style={{ fontSize: 28, color: "#07c160", marginBottom: 12 }} />
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 4 }}>微信扫码登录</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 20 }}>
              使用微信扫描二维码，授权 Navi 接入
            </div>

            {loginStatus === "pending" && !qrUrl && (
              <div style={{ color: "var(--text-muted)", fontSize: 13, padding: "20px 0" }}>正在获取二维码…</div>
            )}
            {qrUrl && (loginStatus === "pending" || loginStatus === "scanned") && (
              <img src={qrUrl} alt="微信二维码" style={{ width: 200, height: 200, borderRadius: 8, marginBottom: 12 }} />
            )}
            {loginStatus === "scanned" && (
              <div style={{ color: "#07c160", fontSize: 13, marginBottom: 8 }}>✅ 已扫码，请在微信中确认</div>
            )}
            {loginStatus === "confirmed" && (
              <div style={{ color: "#07c160", fontSize: 14, fontWeight: 600, padding: "12px 0" }}>🎉 登录成功！</div>
            )}
            {(loginStatus === "error" || loginStatus === "expired") && (
              <div style={{ color: "var(--error)", fontSize: 13, padding: "12px 0" }}>
                {loginError || "二维码已失效，请重试"}
                <br />
                <button onClick={startLogin} style={{ marginTop: 10, padding: "4px 16px", borderRadius: 6, border: "none", background: "#07c160", color: "#fff", cursor: "pointer" }}>重新获取</button>
              </div>
            )}

            <button onClick={() => { setShowLoginModal(false); evtRef.current?.close(); }} style={{
              marginTop: 16, fontSize: 12, color: "var(--text-muted)", background: "none",
              border: "none", cursor: "pointer", textDecoration: "underline",
            }}>取消</button>
          </div>
        </div>
      )}
    </>
  );
}

// ── 主组件：纯消息区，无侧边栏 ──────────────────────────────────────────────
export function ChatPage({ sessionId }: { sessionId: string | null }) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [plusOpen, setPlusOpen] = useState(false);
  const [pendingImages, setPendingImages] = useState<string[]>([]);  // 待发送图片 base64
  const [pendingLearningDate, setPendingLearningDate] = useState<string | null>(null); // 待回答学习问题的日期
  const plusRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);

  // 点击外部关闭 + 菜单
  useEffect(() => {
    if (!plusOpen) return;
    const handler = (e: MouseEvent) => {
      if (plusRef.current && !plusRef.current.contains(e.target as Node)) setPlusOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [plusOpen]);

  const wsRef = useRef<WebSocket | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const streamBufRef = useRef<string>("");
  const streamingIdRef = useRef<string | null>(null);  // ref 版本，供闭包使用

  // 同步 streamingId 到 ref
  useEffect(() => { streamingIdRef.current = streamingId; }, [streamingId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, thinking]);

  // ── 连接 WS ──────────────────────────────────────────────────────────────
  const connectWS = useCallback((sid: string) => {
    wsRef.current?.close();
    const ws = new WebSocket(`ws://localhost:8000/ws/chat?chat_id=${sid}`);
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (e) => {
      const data = JSON.parse(e.data);
      const ts = nowTime();
      if (data.type === "thinking") {
        setThinking(true);
      } else if (data.type === "tool_hint" || data.type === "progress") {
        setMsgs(m => [...m, { id: msgId(), role: "tool", content: data.content, ts }]);
      } else if (data.type === "reply_start") {
        setThinking(false);
        const newId = msgId();
        streamBufRef.current = "";
        streamingIdRef.current = newId;
        setStreamingId(newId);
        setMsgs(m => [...m, { id: newId, role: "assistant", content: "", ts }]);
        if (data.emotion) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          import("@tauri-apps/api/event" as any).then(({ emitTo }: any) => {
            emitTo("live2d-companion", "live2d:emotion_change", { emotion: data.emotion }).catch(() => {});
          }).catch(() => {});
        }
      } else if (data.type === "stream") {
        streamBufRef.current += data.delta;
        const buf = streamBufRef.current;
        const curId = streamingIdRef.current;
        // 流式过程中完全隐藏 [SPLIT]，stream_end 时再真正拆成独立气泡
        const displayBuf = buf.replace(/\[SPLIT\]/g, "");
        setMsgs(m => m.map(msg => msg.id === curId ? { ...msg, content: displayBuf } : msg));
      } else if (data.type === "stream_end") {
        const finishedId = streamingIdRef.current;  // 先保存，清空前记录
        streamingIdRef.current = null;
        setStreamingId(null);
        const fullText = streamBufRef.current;
        const emotion = inferEmotion(fullText);
        if (emotion !== "neutral") {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          import("@tauri-apps/api/event" as any).then(({ emitTo }: any) => {
            emitTo("live2d-companion", "live2d:emotion_change", { emotion }).catch(() => {});
          }).catch(() => {});
        }
        // 按 [SPLIT] 拆成多条气泡，模拟打字节奏
        const parts = fullText.split("[SPLIT]").map((s: string) => s.trim()).filter(Boolean);
        if (parts.length > 1 && finishedId) {
          // 第一条：把 streaming 那条消息的内容更新为第一段
          setMsgs(m => m.map(msg => msg.id === finishedId ? { ...msg, content: parts[0] } : msg));
          // 后续每段延迟 600ms 插入，模拟打字间隔
          parts.slice(1).forEach((part: string, i: number) => {
            setTimeout(() => {
              setMsgs(m => [...m, { id: msgId(), role: "assistant", content: part, ts: nowTime() }]);
            }, (i + 1) * 600);
          });
        }
      } else if (data.type === "reply" || data.type === "proactive") {
        // 学习收获问题：绕过 chat_id 过滤，直接显示在当前会话，并记录待答日期
        if (data.learning_question) {
          const ldate = data.learning_date || new Date().toISOString().slice(0, 10);
          setPendingLearningDate(ldate);
          setThinking(false);
          const rawParts = (data.content || "").split("[SPLIT]").map((s: string) => s.trim()).filter(Boolean);
          const firstPart = rawParts[0] ?? data.content ?? "";
          setMsgs(m => [...m, { id: msgId(), role: "assistant", content: firstPart, ts }]);
          rawParts.slice(1).forEach((part: string, i: number) => {
            setTimeout(() => {
              setMsgs(m => [...m, { id: msgId(), role: "assistant", content: part, ts: nowTime() }]);
            }, (i + 1) * 600);
          });
          return;
        }
        // cron / proactive 全量消息：broadcaster 广播，按 chat_id 过滤后插入
        // data.chat_id 存在时只处理属于当前 session 的消息
        if (data.chat_id && data.chat_id !== sessionId) return;
        setThinking(false);
        const rawParts = (data.content || "")
          .split("[SPLIT]").map((s: string) => s.trim()).filter(Boolean);
        const firstPart = rawParts[0] ?? data.content ?? "";
        setMsgs(m => [...m, { id: msgId(), role: "assistant", content: firstPart, ts }]);
        rawParts.slice(1).forEach((part: string, i: number) => {
          setTimeout(() => {
            setMsgs(m => [...m, { id: msgId(), role: "assistant", content: part, ts: nowTime() }]);
          }, (i + 1) * 600);
        });
        if (data.emotion) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          import("@tauri-apps/api/event" as any).then(({ emitTo }: any) => {
            emitTo("live2d-companion", "live2d:emotion_change", { emotion: data.emotion }).catch(() => {});
          }).catch(() => {});
        }
      } else if (data.type === "navi:report_inquiry") {
        // 【P0-2】日报追问推送：把问题插入当前 ChatPage 作为 Navi 的提问，并设置 pendingLearningDate
        // 用户的下一条消息会被拦截，转走 /api/report/learning_answer
        const ldate = data.date || new Date().toISOString().slice(0, 10);
        setPendingLearningDate(ldate);
        setThinking(false);
        const intro = data.time_range && data.app_summary
          ? `（关于 ${data.time_range} 那段在 ${data.app_summary}）`
          : "";
        const msg = `${data.question || "今天有一段时间想问问你具体在做什么呢~"}${intro ? "\n" + intro : ""}`;
        setMsgs(m => [...m, { id: msgId(), role: "assistant", content: msg, ts }]);
        // Live2D 表情切到 curious
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        import("@tauri-apps/api/event" as any).then(({ emitTo }: any) => {
          emitTo("live2d-companion", "live2d:emotion_change", { emotion: "curious" }).catch(() => {});
        }).catch(() => {});
      } else if (data.type === "reply_images" && data.images?.length) {
        // 后端截图等附图，追加到最后一条 assistant 消息上
        // 拼接后端 base URL（后端发来 /media/filename）
        const fullUrls = (data.images as string[]).map(
          (src: string) => src.startsWith("http") ? src : `${API}${src}`
        );
        setMsgs(m => {
          const lastNaviIdx = [...m].reverse().findIndex(msg => msg.role === "assistant");
          if (lastNaviIdx === -1) {
            return [...m, { id: msgId(), role: "assistant" as MsgRole, content: "", images: fullUrls, ts }];
          }
          const realIdx = m.length - 1 - lastNaviIdx;
          return m.map((msg, i) => i === realIdx
            ? { ...msg, images: [...(msg.images || []), ...fullUrls] }
            : msg
          );
        });
      } else if (data.type === "tts_audio" && data.audio) {
        // TTS 音频：优先发给 Live2D 窗口（有口型同步），fallback 本地播放
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        import("@tauri-apps/api/event" as any).then(({ emitTo }: any) => {
          emitTo("live2d-companion", "live2d:tts_audio", {
            audio: data.audio,
            format: data.format || "mp3",
          }).catch(() => {
            // Live2D 窗口不存在时，fallback 本地播放
            const audio = new Audio(`data:audio/${data.format || "mp3"};base64,${data.audio}`);
            audio.play().catch(() => {});
          });
        }).catch(() => {
          // 非 Tauri 环境，本地播放
          const audio = new Audio(`data:audio/${data.format || "mp3"};base64,${data.audio}`);
          audio.play().catch(() => {});
        });
      }
    };
  }, []);

  // ── 会话切换：加载历史 + 重连 WS ────────────────────────────────────────
  useEffect(() => {
    if (!sessionId) {
      setMsgs([]);
      setConnected(false);
      wsRef.current?.close();
      wsRef.current = null;
      return;
    }
    setMsgs([]); setThinking(false); setStreamingId(null);
    fetch(`${API}/api/chat/sessions/${sessionId}/messages?limit=100`)
      .then(r => r.json())
      .then((data: {role: string; content: string; created_at: string; images?: string[]}[]) => {
        const expanded: ChatMsg[] = [];
        data.forEach(m => {
          const ts = new Date(m.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
          // 图片路径拼上后端 base URL（后端返回 /media/filename）
          const images = m.images?.length
            ? m.images.map(src => src.startsWith("http") ? src : `${API}${src}`)
            : undefined;
          if (m.role === "assistant" && m.content.includes("[SPLIT]")) {
            const parts = m.content.split("[SPLIT]").map(s => s.trim()).filter(Boolean);
            parts.forEach((part, i) => {
              // 图片只挂在最后一条拆分消息上
              expanded.push({ id: msgId(), role: "assistant", content: part, ts,
                ...(i === parts.length - 1 && images ? { images } : {}),
              });
            });
          } else {
            expanded.push({
              id: msgId(),
              role: (m.role === "assistant" ? "assistant" : m.role) as MsgRole,
              content: m.content,
              ts,
              ...(images ? { images } : {}),
            });
          }
        });
        setMsgs(expanded);
      })
      .catch(() => setMsgs([]));
    connectWS(sessionId);

    // cleanup：effect 重跑或组件卸载时，主动关闭旧连接，防止连接泄漏
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [sessionId, connectWS]);

  // ── 图片选择处理 ──────────────────────────────────────────────────────────
  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    files.forEach(file => {
      const reader = new FileReader();
      reader.onload = (ev) => {
        const dataUrl = ev.target?.result as string;
        if (dataUrl) setPendingImages(prev => [...prev, dataUrl]);
      };
      reader.readAsDataURL(file);
    });
    // 清空 input 以允许重复选同一文件
    e.target.value = "";
  };

  // ── 发送（含图片）─────────────────────────────────────────────────────────
  const send = () => {
    const text = input.trim();
    if ((!text && pendingImages.length === 0) || !connected || !wsRef.current || thinking || streamingId) return;
    const btn = document.querySelector<HTMLElement>(".chat-send-btn");
    if (btn) gsap.fromTo(btn, { scale: 0.88 }, { scale: 1, duration: 0.25, ease: "back.out(2)" });

    // ── 学习回答拦截：有待答学习问题时，走 learning_answer 接口 ──
    if (pendingLearningDate && text) {
      setMsgs(m => [...m, { id: msgId(), role: "user", content: text, ts: nowTime() }]);
      setInput("");
      const learningDate = pendingLearningDate;
      setPendingLearningDate(null);

      fetch(`${API}/api/report/learning_answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: learningDate, answer: text }),
      })
        .then(r => r.json())
        .then(d => {
          const msg = d.message || "✅ 已收到，日报正在生成中...";
          setMsgs(m => [...m, { id: msgId(), role: "assistant", content: msg, ts: nowTime() }]);
        })
        .catch(() => {
          setMsgs(m => [...m, {
            id: msgId(), role: "assistant",
            content: "❌ 提交失败，日报将跳过「今日学习收获」节",
            ts: nowTime(),
          }]);
        });
      return;
    }

    const imgSnapshot = [...pendingImages];
    setMsgs(m => [...m, {
      id: msgId(), role: "user",
      content: text || "（图片）",
      images: imgSnapshot.length > 0 ? imgSnapshot : undefined,
      ts: nowTime()
    }]);

    wsRef.current.send(JSON.stringify({
      content: text || "（图片）",
      images: imgSnapshot.length > 0 ? imgSnapshot : undefined,
    }));

    setInput("");
    setPendingImages([]);
    setTimeout(() => {
      const ta = document.querySelector<HTMLTextAreaElement>(".chat-input-ta");
      if (ta) { ta.style.height = "auto"; ta.style.overflowY = "hidden"; }
    }, 0);
  };

  const isWechat = sessionId === WECHAT_SESSION_ID;

  // 微信会话：WS 收到 wechat_message 时重新从 DB 拉取（避免和初始加载重复）
  useEffect(() => {
    if (!isWechat || !wsRef.current) return;
    const ws = wsRef.current;
    const originalOnMessage = ws.onmessage;
    ws.onmessage = (e) => {
      if (originalOnMessage) (originalOnMessage as (ev: MessageEvent) => void)(e);
      try {
        const data = JSON.parse(e.data);
        if (data.type === "wechat_message" && sessionId) {
          // 重新 fetch DB，避免和初始加载的消息重复
          fetch(`${API}/api/chat/sessions/${sessionId}/messages?limit=100`)
            .then(r => r.json())
            .then((rows: {role: string; content: string; created_at: string; images?: string[]}[]) => {
              const expanded: ChatMsg[] = [];
              rows.forEach(m => {
                const ts = new Date(m.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
                const images = m.images?.length
                  ? m.images.map(src => src.startsWith("http") ? src : `${API}${src}`)
                  : undefined;
                if (m.role === "assistant" && m.content.includes("[SPLIT]")) {
                  m.content.split("[SPLIT]").map(s => s.trim()).filter(Boolean).forEach((part, i, arr) => {
                    expanded.push({ id: msgId(), role: "assistant", content: part, ts,
                      ...(i === arr.length - 1 && images ? { images } : {}),
                    });
                  });
                } else {
                  expanded.push({ id: msgId(), role: m.role as MsgRole, content: m.content, ts,
                    ...(images ? { images } : {}),
                  });
                }
              });
              setMsgs(expanded);
            })
            .catch(() => {});
        }
      } catch { /* ignore */ }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isWechat, connected]);

  // ── 渲染 ──────────────────────────────────────────────────────────────────
  return (
    <div className="chat-main" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* 微信状态条（仅微信会话显示） */}
      {isWechat && <WeChatStatusBar />}

      {/* 消息区 */}
      <div className="chat-messages-wrap">
        {!sessionId ? (
          <div className="chat-empty-hint">
            <MessageOutlined style={{ fontSize: 32, color: "var(--primary)", marginBottom: 12 }} />
            <div style={{ fontSize: 15, color: "var(--text-sub)" }}>从左侧选择或新建一个对话</div>
          </div>
        ) : msgs.length === 0 && !thinking ? (
          <div className="chat-empty-hint">
            {isWechat ? (
              <>
                <WechatOutlined style={{ fontSize: 32, color: "#07c160", marginBottom: 12 }} />
                <div style={{ fontSize: 15, color: "var(--text-sub)" }}>微信对话记录会显示在这里</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
                  登录微信后，用户消息和 Navi 的回复都会同步到这里
                </div>
              </>
            ) : (
              <>
                <div style={{ fontSize: 22, color: "var(--text)", marginBottom: 8 }}>有什么可以帮你的？</div>
                <div style={{ fontSize: 13, color: "var(--text-muted)" }}>Navi 已就绪，随时开始对话</div>
              </>
            )}
          </div>
        ) : (
          <>
            {msgs.map(m => {
              if (m.role === "user") return (
                <motion.div key={m.id} className="chat-msg-user"
                  initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>
                  <div className="chat-msg-user-bubble">
                    {m.images && m.images.length > 0 && (
                      <div className="chat-msg-images">
                        {m.images.map((src, i) => (
                          <img key={i} src={src} alt="图片" className="chat-msg-image"
                            onClick={() => window.open(src, "_blank")} />
                        ))}
                      </div>
                    )}
                    {m.content !== "（图片）" && m.content}
                  </div>
                  <div className="chat-msg-ts">{m.ts}</div>
                </motion.div>
              );
              if (m.role === "tool") return (
                <motion.div key={m.id} className="chat-msg-tool"
                  initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                  <ToolOutlined style={{ fontSize: 10 }} /><span>{m.content}</span>
                </motion.div>
              );
              return (
                <motion.div key={m.id} className="chat-msg-navi"
                  initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>
                  <NaviBubble content={m.content} images={m.images} />
                  {streamingId === m.id ? <StreamCursor /> : <div className="chat-msg-ts">{m.ts}</div>}
                </motion.div>
              );
            })}
            {thinking && (
              <motion.div className="chat-thinking" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                <span /><span /><span />
              </motion.div>
            )}
          </>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 输入区（微信会话只读，不显示输入框） */}
      {sessionId && !isWechat && (
        <div className="chat-input-area">
          {/* 学习问题待答提示条 */}
          {pendingLearningDate && (
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "6px 14px", margin: "0 0 6px 0",
              background: "rgba(var(--primary-rgb, 99,102,241), 0.08)",
              border: "1px solid rgba(var(--primary-rgb, 99,102,241), 0.25)",
              borderRadius: 10, fontSize: 12, color: "var(--text-sub)",
            }}>
              <span style={{ fontSize: 15 }}>📚</span>
              <span>正在回答学习问题，发送后日报将自动生成</span>
              <button
                onClick={() => setPendingLearningDate(null)}
                style={{
                  marginLeft: "auto", fontSize: 11, padding: "1px 8px",
                  borderRadius: 6, border: "1px solid var(--border)",
                  background: "transparent", color: "var(--text-muted)",
                  cursor: "pointer",
                }}
              >跳过</button>
            </div>
          )}
          <div className="chat-input-box">
            {/* 待发送图片预览 */}
            {pendingImages.length > 0 && (
              <div className="chat-pending-images">
                {pendingImages.map((src, i) => (
                  <div key={i} className="chat-pending-image-wrap">
                    <img src={src} className="chat-pending-image" alt="待发送" />
                    <button className="chat-pending-image-rm"
                      onClick={() => setPendingImages(prev => prev.filter((_, idx) => idx !== i))}>
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
            <textarea
              className="chat-input-ta"
              value={input}
              disabled={!connected || !!streamingId || thinking}
              onChange={e => {
                setInput(e.target.value);
                e.target.style.height = "auto";
                const next = Math.min(e.target.scrollHeight, 140);
                e.target.style.height = next + "px";
                e.target.style.overflowY = next >= 140 ? "auto" : "hidden";
              }}
              onPaste={e => {
              // 支持直接粘贴图片
              const items = Array.from(e.clipboardData?.items || []);
              const imageItems = items.filter(item => item.type.startsWith("image/"));
              if (imageItems.length > 0) {
                e.preventDefault();
                imageItems.forEach(item => {
                  const file = item.getAsFile();
                  if (!file) return;
                  const reader = new FileReader();
                  reader.onload = (ev) => {
                    const dataUrl = ev.target?.result as string;
                    if (dataUrl) setPendingImages(prev => [...prev, dataUrl]);
                  };
                  reader.readAsDataURL(file);
                });
              }
            }}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
              placeholder={connected ? "发消息给 Navi…" : "未连接，请启动后端"}
              rows={3}
            />
            {/* 底部工具栏 */}
            <div className="chat-input-actions">
              <div className="chat-input-tools">
                {/* + 按钮：点击展开上传菜单 */}
                <div className="chat-plus-wrap" ref={plusRef}>
                  <button
                    className="chat-tool-btn chat-tool-btn--icon"
                    onClick={() => setPlusOpen(v => !v)}
                    title="添加文件或图片"
                  >
                    <PlusOutlined />
                  </button>
                  {plusOpen && (
                    <div className="chat-plus-menu">
                      <button className="chat-plus-item" onClick={() => { fileRef.current?.click(); setPlusOpen(false); }}>
                        <PaperClipOutlined /> 上传文件
                      </button>
                      <button className="chat-plus-item" onClick={() => { imageRef.current?.click(); setPlusOpen(false); }}>
                        <FileImageOutlined /> 上传图片
                      </button>
                    </div>
                  )}
                  {/* 隐藏的 input */}
                  <input ref={fileRef} type="file" style={{ display: "none" }} />
                  <input ref={imageRef} type="file" accept="image/*" multiple
                    style={{ display: "none" }} onChange={handleImageSelect} />
                </div>

                <button className="chat-tool-btn" title="工具">
                  <ToolOutlined />
                  工具
                </button>
              </div>
              <button className="chat-send-btn" onClick={send}
                disabled={!connected || (!input.trim() && pendingImages.length === 0) || !!streamingId || thinking}>
                <ArrowUpOutlined />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}