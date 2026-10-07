import { useState } from 'react';
import { parseRenderable } from '../utils/answerParser.js';

/** 阶段进度面板：迷你进度条 + 状态徽标 + 时间轴（与重构后 chat.html 风格一致） */
export default function ProgressPanel({ progress }) {
  const { done = [], running = [], status = '' } = progress || {};
  const [open, setOpen] = useState(true);

  const statusMap = { processing: '处理中', completed: '已完成', failed: '失败', pending: '等待中' };
  const statusClassMap = { processing: 'run', completed: 'done', failed: 'fail', pending: 'wait' };
  const displayStatus = statusMap[status] || status || 'unknown';
  const statusClass = statusClassMap[status] || 'wait';

  const totalDone = done.length;
  const totalRun = running.length;
  const totalSteps = totalDone + totalRun;
  let pct = totalSteps > 0 ? Math.round((totalDone / totalSteps) * 100) : 0;
  if (status === 'completed') pct = 100;
  if (status === 'failed' && totalSteps === 0) pct = 0;

  // 完成后自动收起
  const effectiveOpen = status === 'completed' || status === 'failed' ? false : open;
  const toggle = () => {
    if (status === 'completed' || status === 'failed') return;
    setOpen((v) => !v);
  };

  return (
    <details className="progress" open={effectiveOpen}>
      <summary onClick={(e) => { e.preventDefault(); toggle(); }}>
        <span className="prog-title">阶段进度</span>
        <span className="prog-bar"><span className="prog-bar-fill" style={{ width: `${pct}%` }} /></span>
        <span className="prog-stat">{totalDone}/{totalSteps} 完成</span>
        <span className={`prog-status st-${statusClass}`}>{displayStatus}</span>
      </summary>
      {effectiveOpen && (
        <ul>
          {totalDone === 0 && totalRun === 0 && <li className="empty">暂无进度</li>}
          {done.map((x, i) => <li key={`d${i}`} className="done">{x}</li>)}
          {running.map((x, i) => <li key={`r${i}`} className="running">{x}</li>)}
        </ul>
      )}
      <style>{`
        .progress {
          margin-top: 10px; border: 1px solid var(--border); border-radius: 14px;
          padding: 11px 14px; background: rgba(255,255,255,.92);
          box-shadow: 0 4px 14px rgba(26,34,51,.05);
        }
        .progress summary {
          cursor: pointer; user-select: none; list-style: none;
          display: flex; align-items: center; gap: 10px;
          font-size: 12px; min-height: 20px;
        }
        .progress summary::-webkit-details-marker { display: none; }
        .prog-title { font-weight: 600; color: #5c6880; flex: 0 0 auto; }
        .prog-bar {
          flex: 1; min-width: 48px; max-width: 160px; height: 4px;
          border-radius: 999px; background: #edf0f7; overflow: hidden; display: block;
        }
        .prog-bar-fill {
          display: block; height: 100%; border-radius: 999px;
          background: linear-gradient(90deg, var(--brand), var(--brand2));
          transition: width .5s ease;
        }
        .prog-stat { color: var(--muted); flex: 0 0 auto; font-variant-numeric: tabular-nums; }
        .prog-status {
          flex: 0 0 auto; padding: 2px 9px; border-radius: 999px;
          font-size: 11px; font-weight: 600; line-height: 1.5;
        }
        .prog-status.st-run { background: #e9f0ff; color: #2563eb; }
        .prog-status.st-done { background: #e5f8ee; color: #16a34a; }
        .prog-status.st-fail { background: #fdecec; color: #dc2626; }
        .prog-status.st-wait { background: #f1f3f9; color: #8a94a8; }
        .progress ul {
          margin: 10px 0 2px 5px; padding: 0 0 0 4px; list-style: none; position: relative;
        }
        .progress ul::before {
          content: ''; position: absolute; left: 9px; top: 10px; bottom: 10px;
          width: 2px; border-radius: 2px; background: #edf0f7;
        }
        .progress li {
          position: relative; padding: 3px 0 3px 24px;
          font-size: 12px; color: #5c6880; line-height: 1.6;
        }
        .progress li::before {
          content: ''; position: absolute; left: 4px; top: 8px;
          width: 12px; height: 12px; border-radius: 50%;
          background: #fff; border: 2px solid #d8deeb; box-sizing: border-box; z-index: 1;
        }
        .progress li.done { color: #3d4a63; }
        .progress li.done::before {
          content: '✓'; background: #16a34a; border-color: #16a34a;
          color: #fff; font-size: 8px; font-weight: 700; line-height: 1.2; text-align: center;
        }
        .progress li.running { color: #2563eb; font-weight: 500; }
        .progress li.running::before {
          background: #fff; border-color: #3d7bff;
          animation: ringPulse 1.4s ease-in-out infinite;
        }
        .progress li.running::after {
          content: ''; position: absolute; left: 7px; top: 11px;
          width: 6px; height: 6px; border-radius: 50%;
          background: var(--brand);
          animation: ringPulse 1.4s ease-in-out infinite; z-index: 1;
        }
        @keyframes ringPulse {
          0%,100% { box-shadow: 0 0 0 0 rgba(61,123,255,.35); }
          50% { box-shadow: 0 0 0 4px rgba(61,123,255,0); }
        }
        .progress li.empty { color: var(--muted); }
        .progress li.empty::before { display: none; }
      `}</style>
    </details>
  );
}
