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
const REPORT_INQUIRY_SESSION_ID = "__report_inquiry__";

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
  // 微信会话：被 ws.onmessage 复用的"按 session 重新拉历史"函数，
  // 通过 ref 传入避免闭包捕获过期的 sessionId。
  const reloadWechatHistoryRef = useRef<(() => void) | null>(null);
  // msgs 的实时镜像，让 reload 计算"新增气泡"时能拿到最新值，
  // 而不必把 setTimeout 写在 setMsgs 回调里（React 反模式 + StrictMode 会双跑）。
  const msgsRef = useRef<ChatMsg[]>([]);

  // 同步 streamingId 到 ref
  useEffect(() => { streamingIdRef.current = streamingId; }, [streamingId]);
  // 实时同步 msgs 到 ref，供 reloadWechatHistory 比较"新增气泡"用
  useEffect(() => { msgsRef.current = msgs; }, [msgs]);

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
      if (data.type === "wechat_message") {
        // 微信侧有新消息（用户发来 / Bot 回复 / 主动对话），
        // 当前在 __wechat__ 会话时重新从 DB 拉取以保证一致性。
        // 注意：不要把同一条消息再当作 reply 渲染，否则会出现拼接重复气泡。
        if (sid === WECHAT_SESSION_ID) reloadWechatHistoryRef.current?.();
        return;
      }
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
        // 微信渠道的 reply 仅供 Live2D / TTS 同步使用，
        // 聊天气泡已经由 wechat_message → reloadWechatHistory 渲染（带 [SPLIT] 拆分）。
        // 这里若再渲染会出现一条「全部拼接」的重复气泡，所以直接忽略。
        if (data.source === "wechat" || data.source === "wechat_proactive") return;
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
        // 【P0-2】日报追问推送：追问已被后端持久化到 __report_inquiry__ 会话，
        // 所以这里只在"用户正好在日报追问会话"时实时追加一条气泡；
        // 其他会话只触发 Live2D 表情，不污染当前对话。切到追问会话时会从 DB 拉到。
        const ldate = data.date || new Date().toISOString().slice(0, 10);
        setPendingLearningDate(ldate);
        setThinking(false);
        if (sid === REPORT_INQUIRY_SESSION_ID) {
          const intro = data.time_range && data.app_summary
            ? `（关于 ${data.time_range} 那段在 ${data.app_summary}）`
            : "";
          const msg = `${data.question || "今天有一段时间想问问你具体在做什么呢~"}${intro ? "\n" + intro : ""}`;
          setMsgs(m => [...m, { id: msgId(), role: "assistant", content: msg, ts }]);
        }
        // Live2D 表情切到 curious
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        import("@tauri-apps/api/event" as any).then(({ emitTo }: any) => {
          emitTo("live2d-companion", "live2d:emotion_change", { emotion: "curious" }).catch(() => {});
        }).catch(() => {});
      } else if (data.type === "navi:report_done") {
        // 日报生成完成：如果正在日报追问会话，清掉 pendingLearningDate
        // 气泡本身已由后端写入 __report_inquiry__ 会话的 DB，这里不再重复插入
        setPendingLearningDate(null);
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
    const isWechatSid = sessionId === WECHAT_SESSION_ID;
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
              // 微信会话用稳定 id（基于 created_at + index），让 reload 时差量比较生效，避免闪烁
              expanded.push({
                id: isWechatSid ? `${m.created_at}#${i}` : msgId(),
                role: "assistant", content: part, ts,
                ...(i === parts.length - 1 && images ? { images } : {}),
              });
            });
          } else {
            expanded.push({
              id: isWechatSid ? `${m.created_at}#0` : msgId(),
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

    // ── 日报追问会话：用户的回答走 /api/report/learning_answer 接口，不走 WS agent loop ──
    // 兼容旧逻辑：如果在其它会话里通过 pendingLearningDate 兜底进入追问也一样处理
    const inReportInquirySession = sessionId === REPORT_INQUIRY_SESSION_ID;
    if (inReportInquirySession || pendingLearningDate) {
      if (!text) {
        // 在追问会话里只支持文字回答，避免把图片发进 agent loop 造成语义混乱
        if (pendingImages.length > 0) {
          setMsgs(m => [...m, {
            id: msgId(), role: "assistant",
            content: "日报追问目前只支持文字回答哦，简单说几句就好~",
            ts: nowTime(),
          }]);
          setPendingImages([]);
        }
        return;
      }
      // 先在前端乐观渲染一条用户气泡（后端 learning_answer 也会 save_message 一次，
      // 但那是持久化用的，不会立刻推回前端；下次切会话会从 DB 拉）
      setMsgs(m => [...m, { id: msgId(), role: "user", content: text, ts: nowTime() }]);
      setInput("");
      const learningDate = pendingLearningDate || new Date().toISOString().slice(0, 10);
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

  // 微信会话：把"按 sessionId 重新拉历史 + 差量更新 + 新增气泡逐条冒"函数挂到 ref，
  // 让 connectWS 的 ws.onmessage 闭包通过 ref 调用，避免 sessionId 过期，
  // 也避免每次重连都 monkey-patch ws.onmessage 导致重复触发 / 闪烁。
  useEffect(() => {
    if (!isWechat || !sessionId) {
      reloadWechatHistoryRef.current = null;
      return;
    }
    // 逐条冒泡的间隔（毫秒），跟手机微信节奏对齐
    const TYPING_INTERVAL = 600;
    reloadWechatHistoryRef.current = () => {
      fetch(`${API}/api/chat/sessions/${sessionId}/messages?limit=100`)
        .then(r => r.json())
        .then((rows: {role: string; content: string; created_at: string; images?: string[]}[]) => {
          if (!Array.isArray(rows)) return;
          const expanded: ChatMsg[] = [];
          rows.forEach(m => {
            const ts = new Date(m.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
            const images = m.images?.length
              ? m.images.map(src => src.startsWith("http") ? src : `${API}${src}`)
              : undefined;
            if (m.role === "assistant" && m.content.includes("[SPLIT]")) {
              m.content.split("[SPLIT]").map(s => s.trim()).filter(Boolean).forEach((part, i, arr) => {
                expanded.push({
                  // 稳定 id：基于 created_at + index，让差量比较跨 reload 生效
                  id: `${m.created_at}#${i}`,
                  role: "assistant", content: part, ts,
                  ...(i === arr.length - 1 && images ? { images } : {}),
                });
              });
            } else {
              expanded.push({
                id: `${m.created_at}#0`,
                role: m.role as MsgRole, content: m.content, ts,
                ...(images ? { images } : {}),
              });
            }
          });

          // 1) 用当前真实 msgs 计算"新增"，避免在 setMsgs 回调里写副作用
          const currentMsgs = msgsRef.current;
          if (currentMsgs.length === expanded.length
            && currentMsgs.every((p, i) => p.id === expanded[i].id && p.content === expanded[i].content)) {
            return; // 完全一致，直接跳过
          }
          const existingIds = new Set(currentMsgs.map(p => p.id));
          const stillExisting = expanded.filter(e => existingIds.has(e.id));
          const newOnes = expanded.filter(e => !existingIds.has(e.id));
          const instantNew = newOnes.filter(e => e.role !== "assistant");
          const typingNew = newOnes.filter(e => e.role === "assistant");

          console.log("[wechat reload] existing=%d, instantNew=%d, typingNew=%d",
            stillExisting.length, instantNew.length, typingNew.length);

          // 2) 首帧：已有 + 新用户消息 + 第一条新 assistant
          const firstFrame: ChatMsg[] = [...stillExisting, ...instantNew];
          if (typingNew.length > 0) firstFrame.push(typingNew[0]);
          setMsgs(firstFrame);

          // 3) 剩余 assistant 气泡：在 setMsgs 之外、用真正的 setTimeout 排队逐条追加
          typingNew.slice(1).forEach((bubble, idx) => {
            const delay = (idx + 1) * TYPING_INTERVAL;
            setTimeout(() => {
              setMsgs(cur => {
                if (cur.some(c => c.id === bubble.id)) return cur;
                return [...cur, bubble];
              });
            }, delay);
            console.log("[wechat typing] queued bubble #%d in %dms: %s", idx + 1, delay, bubble.content.slice(0, 20));
          });
        })
        .catch(() => {});
    };
  }, [isWechat, sessionId]);



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
          {/* 学习问题待答提示条：
              - 在日报追问会话里：常驻提示"这里的回答会直接生成日报"
              - 在其它会话但 pendingLearningDate 存在（旧逻辑兜底）：提示用户可以直接答 */}
          {(sessionId === REPORT_INQUIRY_SESSION_ID || pendingLearningDate) && (
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "6px 14px", margin: "0 0 6px 0",
              background: "rgba(var(--primary-rgb, 99,102,241), 0.08)",
              border: "1px solid rgba(var(--primary-rgb, 99,102,241), 0.25)",
              borderRadius: 10, fontSize: 12, color: "var(--text-sub)",
            }}>
              <span>
                {sessionId === REPORT_INQUIRY_SESSION_ID
                  ? "在这里回答 Navi 的追问，发送后会立刻生成今天的日报"
                  : "正在回答学习问题，发送后日报将自动生成"}
              </span>
              {sessionId !== REPORT_INQUIRY_SESSION_ID && (
                <button
                  onClick={() => setPendingLearningDate(null)}
                  style={{
                    marginLeft: "auto", fontSize: 11, padding: "1px 8px",
                    borderRadius: 6, border: "1px solid var(--border)",
                    background: "transparent", color: "var(--text-muted)",
                    cursor: "pointer",
                  }}
                >跳过</button>
              )}
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