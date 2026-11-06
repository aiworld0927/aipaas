import { useState, useRef, useEffect, useCallback } from 'react';
import { useParams, useOutletContext, useNavigate } from 'react-router-dom';
import { submitQuery, openStream, fetchHistory, clearHistory } from '../api/chat.js';
import { createSession, touchSession } from '../utils/sessions.js';
import { parseRenderable } from '../utils/answerParser.js';
import ProgressPanel from '../components/ProgressPanel.jsx';
import AnswerBody from '../components/AnswerBody.jsx';

const nowTime = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
const formatTime = (ts) => {
  if (!ts) return nowTime();
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return nowTime();
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
};

/** 一条消息的数据结构 */
const makeMsg = (role, text, ts, imageUrls) => ({
  id: 'm-' + Math.random().toString(36).slice(2),
  role,
  text: text || '',
  ts: ts || Math.floor(Date.now() / 1000),
  imageUrls: imageUrls || [],
  // 仅发送中的机器人消息有：typing / progress / error
  typing: false,
  progress: null, // { done:[], running:[], status:'' }
  error: '',
});

export default function Chat() {
  const { sessionId: routeSessionId } = useParams();
  const navigate = useNavigate();
  const outlet = useOutletContext();
  const { refreshSessions } = outlet || {};

  const [sessionId, setSessionId] = useState(routeSessionId || '');
  const [messages, setMessages] = useState(() => [
    makeMsg('bot', '你好，我是金融标准化知识库客服，小金。你可以直接提问，我会在"阶段进度"里展示处理过程。'),
  ]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [isStream, setIsStream] = useState(true);

  const chatRef = useRef(null);
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  const scrollToBottom = useCallback(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, []);

  useEffect(scrollToBottom, [messages, scrollToBottom]);

  const patchLast = (patch) => {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === 'bot') next[next.length - 1] = { ...last, ...patch };
      return next;
    });
  };

  /** 切换会话：拉取历史 */
  useEffect(() => {
    setSessionId(routeSessionId || '');
    if (!routeSessionId) return;
    let cancelled = false;
    (async () => {
      try {
        const { items } = await fetchHistory(routeSessionId);
        if (cancelled) return;
        if (items.length === 0) return;
        setMessages((prev) => [
          prev[0],
          ...items.map((it) =>
            it.role === 'user'
              ? makeMsg('user', it.text || '', it.ts)
              : makeMsg('bot', it.text || '', it.ts, it.image_urls || [])
          ),
        ]);
      } catch {
        /* 忽略历史加载失败 */
      }
    })();
    return () => { cancelled = true; };
  }, [routeSessionId]);

  /** 发送消息（逻辑与旧版 chat.html onSend 完全一致） */
  const onSend = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    setSending(true);

    // 无会话时（直接访问 /chat）自动创建
    let sid = sessionIdRef.current;
    if (!sid) {
      const s = createSession();
      sid = s.id;
      setSessionId(sid);
      if (refreshSessions) refreshSessions();
      navigate(`/chat/${sid}`, { replace: true });
    }

    const userMsg = makeMsg('user', text);
    const botMsg = makeMsg('bot', '');
    botMsg.typing = true;
    botMsg.progress = { done: [], running: [], status: 'pending' };
    setMessages((prev) => [...prev, userMsg, botMsg]);
    if (refreshSessions) touchSession(sid, text) && refreshSessions();

    try {
      const data = await submitQuery({ query: text, sessionId: sid, isStream });

      if (!isStream) {
        // 非流式：直接渲染结果
        patchLast({
          typing: false,
          progress: { done: data.done_list || [], running: [], status: 'completed' },
          text: data.answer || '',
          error: data.error || '',
          imageUrls: data.image_urls || [],
        });
        setSending(false);
        return;
      }

      // 流式：SSE
      const es = openStream(data.session_id);
      let rawAnswerText = '';

      es.addEventListener('progress', (e) => {
        try {
          const d = JSON.parse(e.data || '{}');
          patchLast({ progress: { done: d.done_list || [], running: d.running_list || [], status: d.status } });
          if (d && d.status === 'completed') {
            patchLast({ typing: false });
            setSending(false);
          }
        } catch { /* ignore */ }
      });

      es.addEventListener('delta', (e) => {
        try {
          const d = JSON.parse(e.data || '{}');
          const delta = d.delta || '';
          if (delta) {
            rawAnswerText += delta;
            patchLast({ text: rawAnswerText, imageUrls: [] });
          }
        } catch { /* ignore */ }
      });

      const finalize = (e, eventName) => {
        patchLast({ typing: false, progressDone: true });
        try {
          const d = JSON.parse(e.data || '{}');
          const finalText =
            d && typeof d.answer === 'string' && d.answer.trim().length > 0
              ? d.answer
              : rawAnswerText;
          patchLast({ text: finalText, imageUrls: d.image_urls || [] });
        } catch { /* ignore */ }
        // 收起进度
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'bot') {
            last.progress = last.progress ? { ...last.progress, status: last.progress.status === 'failed' ? 'failed' : 'completed' } : last.progress;
          }
          return next;
        });
        es.close();
        setSending(false);
        if (refreshSessions) refreshSessions();
      };
      es.addEventListener('final', (e) => finalize(e, 'final'));
      es.addEventListener('final_answer', (e) => finalize(e, 'final_answer'));

      es.addEventListener('error', (e) => {
        patchLast({ typing: false });
        try {
          const msg = e && e.data ? JSON.parse(e.data).error || 'SSE 连接中断/失败' : 'SSE 连接中断/失败';
          rawAnswerText += `\n\n（错误：${msg}）`;
        } catch {
          rawAnswerText += '\n\n（错误：SSE 连接中断/失败）';
        }
        patchLast({ text: rawAnswerText });
        es.close();
        setSending(false);
      });
    } catch (err) {
      patchLast({ typing: false, error: err.message || String(err) });
      setSending(false);
    }
  };

  /** 清空当前会话 */
  const onClear = async () => {
    if (!window.confirm('确定要清空当前会话的历史记录吗？这将无法恢复。')) return;
    try {
      if (sessionId) await clearHistory(sessionId);
    } catch {
      window.alert('服务端清空失败，仅清空本地显示');
    }
    setMessages((prev) => [prev[0]]);
  };

  const onInputKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  return (
    <div className="chat-page">
      <div className="chat-toolbar">
        <span className="chat-session-tag">{sessionId ? `会话：${sessionId.slice(0, 12)}…` : '新会话'}</span>
        <label className="stream-switch">
          <input type="checkbox" checked={isStream} onChange={(e) => setIsStream(e.target.checked)} />
          <span className="track"><span className="thumb" /></span>
          流式输出
        </label>
        <button className="btn btn-ghost" onClick={onClear}>清空对话</button>
      </div>

      <div className="chat-scroll" ref={chatRef}>
        <div className="chat-inner">
          <div className="dayline">今天</div>
          {messages.map((m) => (
            <MessageRow key={m.id} msg={m} />
          ))}
        </div>
      </div>

      <div className="composer-wrap">
        <div className="composer">
          <textarea
            value={input}
            placeholder="请输入问题（Enter 发送，Shift+Enter 换行）"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onInputKey}
          />
          <button className="btn btn-primary send-btn" onClick={onSend} disabled={sending}>
            {sending ? '回答中…' : '发送'}
          </button>
        </div>
        <div className="hint">
          快捷键：<kbd>Enter</kbd> 发送，<kbd>Shift</kbd>+<kbd>Enter</kbd> 换行 · 由 AI 生成，仅供参考
        </div>
      </div>

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
        .msg.bot .bubble { padding: 2px 0; max-width: 100%; }
        .meta { font-size: 11px; color: var(--muted); margin-top: 6px; }
        .err-text { color: var(--danger); }

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
        .hint kbd {
          font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px;
          border: 1px solid var(--border); padding: 1px 6px; border-radius: 6px;
          background: #fff; color: #3b4a5a;
        }

        @media (max-width: 600px) {
          .msg { gap: 9px; }
          .avatar { width: 32px; height: 32px; border-radius: 10px; font-size: 12px; }
          .bubble { font-size: 14px; max-width: 88%; }
        }
      `}</style>
    </div>
  );
}

/** 单条消息渲染 */
function MessageRow({ msg }) {
  return (
    <div className={`msg ${msg.role === 'user' ? 'user' : 'bot'}`}>
      {msg.role === 'bot' && <div className="avatar bot">小金</div>}
      <div className="msg-main">
        {msg.role === 'user' ? (
          <>
            <div className="bubble">{msg.text}</div>
            <div className="meta">{formatTime(msg.ts)}</div>
          </>
        ) : (
          <>
            <div className="bubble">
              {msg.typing && (
                <span className="typing">
                  <span className="dot" /><span className="dot" /><span className="dot" />
                </span>
              )}
              {msg.error && <div className="err-text">{`抱歉，本次处理失败：\n${msg.error}`}</div>}
              {!msg.error && msg.text && <AnswerBody answerText={msg.text} imageUrls={msg.imageUrls} />}
              {msg.progress && <ProgressPanel progress={msg.progress} />}
            </div>
            <div className="meta">{formatTime(msg.ts)}</div>
          </>
        )}
      </div>
      {msg.role === 'user' && <div className="avatar me">我</div>}
    </div>
  );
}
