import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useOutletContext, useParams } from 'react-router-dom';
import { chat, chatStream, newIdempotencyKey } from '../api/p0Client.js';
import { createSession, touchSession } from '../utils/sessions.js';
import MarkdownView from '../components/MarkdownView.jsx';
import CitationCard, { CitationDrawer } from '../components/CitationCard.jsx';
import CapabilityBar, { defaultCapability } from '../components/CapabilityBar.jsx';

/**
 * #242 类 ChatGPT 对话界面：
 * 输入 → 流式增量 → 完整回答（done.content 覆盖）→ 条款引用卡片 → 点击查看原文。
 * #402 底部能力栏：P0 仅 RAG；#403 输出模式（流式/非流式）独立开关。
 * 断线时自动携带 Last-Event-ID 重连；服务端缓冲丢失则重新生成并重置本气泡。
 */

const WELCOME = {
  id: 'welcome',
  role: 'bot',
  text: '你好，我是金融标准问答助手。请描述问题，我会仅依据检索到的标准条款作答，并在下方给出可点击查看原文的引用。',
  citations: [],
};

const nowTime = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

const makeMessage = (role, text) => ({
  id: `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  role,
  text: text || '',
  ts: Date.now(),
  citations: [],
  streaming: false,
  error: '',
  refused: false,
  refusalReason: '',
  warnings: [],
  traceId: '',
  answerId: '',
  finishReason: '',
  provider: '',
  degraded: false,
  firstTokenMs: null,
  elapsedMs: null,
});

export default function Chat() {
  const { sessionId: routeSessionId } = useParams();
  const navigate = useNavigate();
  const outlet = useOutletContext() || {};
  const { refreshSessions } = outlet;

  const [sessionId, setSessionId] = useState(routeSessionId || '');
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [streamMode, setStreamMode] = useState(true);
  const [capability, setCapability] = useState(defaultCapability());
  const [selectedCitation, setSelectedCitation] = useState(null);

  const chatRef = useRef(null);
  const sessionIdRef = useRef(sessionId);
  const abortRef = useRef(null);
  const pendingResolveRef = useRef(null);
  sessionIdRef.current = sessionId;

  const scrollToBottom = useCallback(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  useEffect(() => {
    setSessionId(routeSessionId || '');
  }, [routeSessionId]);

  const patchMessage = useCallback((id, patch) => {
    setMessages((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  const patchById = useCallback((id, updater) => {
    setMessages((prev) => prev.map((item) => (item.id === id ? updater(item) : item)));
  }, []);

  /** 流式问答：自动重连 2 次，Last-Event-ID 续传，done.content 作为最终正文。 */
  const streamAnswer = (botId, body, idempotencyKey) =>
    new Promise((resolve) => {
      let lastEventId = '';
      let answerId = '';
      let attempt = 0;
      let settled = false;
      const started = performance.now();
      pendingResolveRef.current = resolve;

      const finish = (patch) => {
        if (settled) return;
        settled = true;
        pendingResolveRef.current = null;
        patchMessage(botId, {
          streaming: false,
          elapsedMs: Math.round(performance.now() - started),
          ...patch,
        });
        setSending(false);
        resolve();
      };

      const connect = () => {
        abortRef.current = chatStream(body, {
          idempotencyKey,
          lastEventId: lastEventId || undefined,
          onEvent: ({ event, data, id }) => {
            if (id) lastEventId = id;
            if (event === 'meta') {
              const incoming = data.answerId || '';
              if (incoming && answerId && incoming !== answerId) {
                // 服务端缓冲已过期，重新生成：重置本气泡，避免内容拼接错乱。
                patchMessage(botId, { text: '', citations: [] });
              }
              if (incoming) answerId = incoming;
              patchMessage(botId, { answerId: incoming, traceId: data.traceId || '' });
              return;
            }
            if (event === 'citation') {
              const citations = Array.isArray(data.citations) ? data.citations : [];
              if (citations.length) patchMessage(botId, { citations });
              return;
            }
            if (event === 'delta' && typeof data.delta === 'string') {
              const firstTokenMs =
                data.timings?.firstTokenMs ?? Math.round(performance.now() - started);
              patchById(botId, (item) => ({
                ...item,
                text: item.text + data.delta,
                firstTokenMs: item.firstTokenMs ?? firstTokenMs,
              }));
              return;
            }
            if (event === 'done') {
              const finalText = typeof data.content === 'string' ? data.content : undefined;
              const patch = {
                citations: Array.isArray(data.citations) ? data.citations : [],
                refused: Boolean(data.refused),
                refusalReason: data.refusalReason || '',
                finishReason: data.finishReason || '',
                warnings: data.warnings || [],
                provider: data.provider || '',
                degraded: Boolean(data.degraded),
                traceId: data.traceId || '',
                answerId: data.answerId || answerId,
                error: '',
              };
              // done.content 为服务端后处理后的最终正文，覆盖流式预览以保证二者一致。
              if (finalText !== undefined) patch.text = finalText;
              finish(patch);
              return;
            }
            if (event === 'error') {
              finish({
                error: data.message || '服务端返回错误',
                finishReason: data.code || 'ERROR',
                retryable: Boolean(data.retryable),
              });
            }
          },
          onError: (err) => {
            if (settled) return;
            if (attempt >= 2) {
              finish({ error: err?.message || '连接中断，请稍后重试' });
              return;
            }
            attempt += 1;
            setTimeout(connect, 400 * attempt);
          },
          onClose: () => {
            if (settled) return;
            // 正常结束应收到 done；仅收到 ping/中间事件就断开时自动续传。
            if (attempt >= 2) {
              finish({ error: '流式连接中断，未能获取完整答案' });
              return;
            }
            attempt += 1;
            setTimeout(connect, 400 * attempt);
          },
        });
        return startedAt;
      };

      connect();
    });

  const sendNonStream = async (botId, body, idempotencyKey) => {
    const started = performance.now();
    try {
      const data = await chat(body, idempotencyKey);
      patchMessage(botId, {
        streaming: false,
        text: data.content ?? data.answer ?? '',
        citations: data.citations || [],
        refused: Boolean(data.refused),
        refusalReason: data.refusalReason || '',
        finishReason: data.finishReason || '',
        warnings: data.warnings || [],
        provider: data.provider || '',
        degraded: Boolean(data.degraded),
        traceId: data.traceId || '',
        answerId: data.answerId || '',
        elapsedMs: Math.round(performance.now() - started),
      });
    } catch (err) {
      patchMessage(botId, { streaming: false, error: err?.message || '问答失败' });
    } finally {
      setSending(false);
    }
  };

  const onSend = async () => {
    const text = input.trim();
    if (!text || sending) return;

    let sid = sessionIdRef.current;
    if (!sid) {
      const created = createSession();
      sid = created.id;
      setSessionId(sid);
      if (refreshSessions) refreshSessions();
      navigate(`/chat/${sid}`, { replace: true });
    }

    setInput('');
    setSending(true);
    const userMessage = makeMessage('user', text);
    const botMessage = { ...makeMessage('bot', ''), streaming: true };
    setMessages((prev) => [...prev, userMessage, botMessage]);
    if (refreshSessions && touchSession(sid, text)) refreshSessions();

    const body = {
      query: text,
      sessionId: sid,
      capability,
      conversationMode: streamMode ? 'stream' : 'non-stream',
      retrievalMode: 'vector',
      topK: 5,
    };
    const idempotencyKey = newIdempotencyKey();
    const started = performance.now();

    if (streamMode) {
      patchMessage(botMessage.id, { startedAt: started });
      await streamAnswer(botMessage.id, body, idempotencyKey);
    } else {
      await sendNonStream(botMessage.id, body, idempotencyKey);
    }
  };

  const onStop = () => {
    if (abortRef.current) abortRef.current();
    abortRef.current = null;
    setSending(false);
    // 中断后释放等待中的流式 Promise，避免挂起的发送任务。
    if (pendingResolveRef.current) {
      pendingResolveRef.current();
      pendingResolveRef.current = null;
    }
    setMessages((prev) =>
      prev.map((item) =>
        item.streaming ? { ...item, streaming: false, error: item.text ? '' : '已停止生成' } : item,
      ),
    );
  };

  const onClear = () => {
    if (sending) return;
    setMessages([WELCOME]);
  };

  const onInputKey = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

  const onCitationClick = (message, refIndex) => {
    const citation = message.citations?.[refIndex - 1];
    if (citation) setSelectedCitation(citation);
  };

  return (
    <div className="chat-page">
      <div className="chat-toolbar">
        <span className="chat-session-tag">{sessionId ? `会话：${sessionId.slice(0, 12)}…` : '新会话'}</span>
        <label className="stream-switch" title="输出模式（P1 #403 偏好持久化）">
          <input type="checkbox" checked={streamMode} onChange={(event) => setStreamMode(event.target.checked)} />
          <span className="track"><span className="thumb" /></span>
          {streamMode ? '流式输出' : '完整返回'}
        </label>
        <button className="btn btn-ghost" onClick={onClear} disabled={sending}>清空对话</button>
      </div>

      <div className="chat-scroll" ref={chatRef}>
        <div className="chat-inner">
          <div className="dayline">今天</div>
          {messages.map((message) => (
            <MessageRow
              key={message.id}
              message={message}
              onCitationClick={(index) => onCitationClick(message, index)}
            />
          ))}
        </div>
      </div>

      <div className="composer-wrap">
        <CapabilityBar value={capability} onChange={setCapability} disabled={sending} />
        <div className="composer">
          <textarea
            value={input}
            placeholder="请输入金融标准相关问题（Enter 发送，Shift+Enter 换行）"
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onInputKey}
            disabled={sending}
          />
          {sending ? (
            <button className="btn btn-ghost send-btn" onClick={onStop}>停止</button>
          ) : (
            <button className="btn btn-primary send-btn" onClick={onSend} disabled={!input.trim()}>
              发送
            </button>
          )}
        </div>
        <div className="hint">
          回答仅依据检索条款生成并附带引用 · 由 AI 生成，仅供参考
        </div>
      </div>

      <CitationDrawer citation={selectedCitation} onClose={() => setSelectedCitation(null)} />

      <style>{`
        .chat-page { height: 100%; display: flex; flex-direction: column; min-height: 0; }
        .chat-toolbar {
          flex: 0 0 auto; display: flex; align-items: center; gap: 12px;
          padding: 10px clamp(14px,3vw,28px);
          border-bottom: 1px solid var(--border);
          background: rgba(255,255,255,.6);
        }
        .chat-session-tag {
          font-size: 12px; color: var(--muted);
          border: 1px solid var(--border); border-radius: 999px;
          padding: 5px 12px; background: #fff;
          max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .chat-toolbar .btn-ghost { padding: 7px 12px; font-size: 12px; margin-left: auto; }
        .stream-switch {
          display: inline-flex; align-items: center; gap: 8px;
          font-size: 12px; color: var(--muted); cursor: pointer; user-select: none;
        }
        .stream-switch input { display: none; }
        .stream-switch .track {
          width: 36px; height: 21px; border-radius: 999px; background: #d8deeb;
          position: relative; transition: background .2s;
        }
        .stream-switch .thumb {
          position: absolute; top: 3px; left: 3px; width: 15px; height: 15px;
          border-radius: 50%; background: #fff; box-shadow: 0 2px 6px rgba(0,0,0,.22);
          transition: transform .2s;
        }
        .stream-switch input:checked + .track { background: linear-gradient(135deg, var(--brand), var(--brand2)); }
        .stream-switch input:checked + .track .thumb { transform: translateX(15px); }

        .chat-scroll { flex: 1; overflow-y: auto; overscroll-behavior: contain; padding: 20px 0 12px; }
        .chat-inner { max-width: 860px; margin: 0 auto; padding: 0 clamp(14px,4vw,28px); }
        .dayline {
          width: max-content; margin: 4px auto 18px; padding: 4px 14px;
          font-size: 12px; color: var(--muted);
          background: rgba(255,255,255,.85); border: 1px solid var(--border); border-radius: 999px;
        }

        .msg { display: flex; gap: 12px; margin-bottom: 18px; align-items: flex-start; animation: fadeInUp .28s ease both; }
        .msg.user { justify-content: flex-end; }
        .msg-main { flex: 1; min-width: 0; }
        .msg.user .msg-main { display: flex; flex-direction: column; align-items: flex-end; }
        .avatar {
          flex: 0 0 auto; width: 38px; height: 38px; border-radius: 12px;
          display: flex; align-items: center; justify-content: center;
          font-size: 14px; font-weight: 600;
        }
        .avatar.bot {
          background: linear-gradient(135deg, var(--brand), var(--brand2));
          color: #fff; box-shadow: 0 6px 16px rgba(61,123,255,.26);
        }
        .avatar.me { background: #e9eef7; color: #52607a; border: 1px solid #dfe5f0; }
        .bubble {
          max-width: min(720px, 90%);
          display: inline-block; padding: 12px 15px;
          font-size: 15px; line-height: 1.7;
          white-space: pre-wrap; overflow-wrap: break-word;
        }
        .msg.user .bubble {
          background: linear-gradient(135deg, var(--brand), var(--brand2));
          color: #fff; border-radius: 18px 18px 18px 6px;
          box-shadow: 0 8px 22px rgba(61,123,255,.26);
        }
        .msg.bot .bubble { padding: 2px 0; max-width: 100%; white-space: normal; }
        .meta { font-size: 11px; color: var(--muted); margin-top: 6px; display: flex; flex-wrap: wrap; gap: 10px; }
        .meta .degraded { color: #b45309; }
        .err-text { color: var(--danger); font-size: 14px; }

        .typing { display: inline-flex; gap: 6px; align-items: center; padding: 6px 2px; }
        .typing .dot {
          width: 8px; height: 8px; border-radius: 50%;
          background: linear-gradient(135deg, var(--brand), var(--brand2));
          animation: dotBounce 1.2s infinite ease-in-out;
        }
        .typing .dot:nth-child(2) { animation-delay: .15s; }
        .typing .dot:nth-child(3) { animation-delay: .3s; }
        @keyframes dotBounce {
          0%,80%,100% { transform: translateY(0); opacity: .5; }
          40% { transform: translateY(-6px); opacity: 1; }
        }

        .citations { margin-top: 14px; display: flex; flex-direction: column; gap: 8px; }
        .citations-title { font-size: 12px; color: var(--muted); font-weight: 600; }
        .cite-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 8px; }
        .cite-card {
          display: flex; gap: 10px; text-align: left;
          padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px;
          background: #fff; cursor: pointer; transition: all .16s;
          font-family: inherit;
        }
        .cite-card:hover { border-color: rgba(61,123,255,.45); box-shadow: 0 8px 20px rgba(61,123,255,.1); }
        .cite-index {
          flex: 0 0 auto; width: 22px; height: 22px; border-radius: 7px;
          background: rgba(61,123,255,.12); color: var(--brand);
          font-size: 12px; font-weight: 700;
          display: flex; align-items: center; justify-content: center;
        }
        .cite-main { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
        .cite-title { font-size: 13px; font-weight: 700; color: #26324a; }
        .cite-clause { margin-left: 8px; font-weight: 600; color: var(--brand); font-size: 12px; }
        .cite-quote {
          font-size: 12px; color: #5b6880; line-height: 1.55;
          display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
          overflow: hidden;
        }
        .cite-quote.muted { color: var(--muted); }
        .cite-loc { font-size: 11px; color: var(--muted); }

        .composer-wrap {
          flex: 0 0 auto; padding: 6px clamp(14px,4vw,28px) 12px;
          background: linear-gradient(180deg, rgba(244,246,251,0), var(--bg) 45%);
        }
        .composer {
          max-width: 860px; margin: 0 auto;
          display: flex; gap: 10px; align-items: flex-end;
          background: #fff; border: 1px solid var(--border); border-radius: 22px;
          padding: 10px 10px 10px 18px;
          box-shadow: var(--shadow);
          transition: border-color .2s, box-shadow .2s;
        }
        .composer:focus-within {
          border-color: rgba(61,123,255,.55);
          box-shadow: 0 0 0 4px var(--ring), var(--shadow);
        }
        .composer textarea {
          flex: 1; resize: none; min-height: 44px; max-height: 160px;
          padding: 10px 0; border: none; background: transparent; outline: none;
          font-size: 15px; line-height: 1.55; color: var(--text);
        }
        .composer textarea::placeholder { color: #aab3c5; }
        .send-btn { height: 44px; padding: 0 24px; font-size: 14px; border-radius: 15px; }
        .hint { max-width: 860px; margin: 10px auto 0; text-align: center; color: var(--muted); font-size: 12px; }

        @media (max-width: 600px) {
          .msg { gap: 9px; }
          .avatar { width: 32px; height: 32px; border-radius: 10px; font-size: 12px; }
          .bubble { font-size: 14px; max-width: 88%; }
          .cite-list { grid-template-columns: 1fr; }
        }
      `}</style>
    </div>
  );
}

function MessageRow({ message, onCitationClick }) {
  const isUser = message.role === 'user';
  const showCitations = !isUser && message.citations?.length > 0;

  return (
    <div className={`msg ${isUser ? 'user' : 'bot'}`}>
      {!isUser && <div className="avatar bot">金</div>}
      <div className="msg-main">
        {isUser ? (
          <>
            <div className="bubble">{message.text}</div>
            <div className="meta">{nowTime()}</div>
          </>
        ) : (
          <>
            <div className="bubble">
              {message.streaming && !message.text && (
                <span className="typing">
                  <span className="dot" /><span className="dot" /><span className="dot" />
                </span>
              )}
              {message.error && <div className="err-text">{message.error}</div>}
              {!message.error && message.text && (
                <MarkdownView text={message.text} onCitationClick={onCitationClick} />
              )}
              {message.refused && (
                <div className="citations-title" style={{ marginTop: 10 }}>
                  未生成回答：{message.refusalReason || '依据不足，建议查阅标准原文'}
                </div>
              )}
              {showCitations && (
                <div className="citations">
                  <div className="citations-title">引用条款（点击查看原文）</div>
                  <div className="cite-list">
                    {message.citations.map((citation, index) => (
                      <CitationCard
                        key={`${citation.chunkId || index}`}
                        citation={citation}
                        index={index + 1}
                        onSelect={() => onCitationClick(index + 1)}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="meta">
              {message.firstTokenMs !== null && <span>首 token {message.firstTokenMs} ms</span>}
              {message.elapsedMs !== null && <span>总耗时 {message.elapsedMs} ms</span>}
              {message.provider && <span>模型 {message.provider}</span>}
              {message.degraded && <span className="degraded">已降级</span>}
              {message.warnings?.length > 0 && <span>提示 {message.warnings.length} 条</span>}
              {message.traceId && <span title={message.traceId}>trace {message.traceId.slice(0, 12)}…</span>}
            </div>
          </>
        )}
      </div>
      {isUser && <div className="avatar me">我</div>}
    </div>
  );
}
