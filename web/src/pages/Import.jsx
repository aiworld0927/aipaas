import { useState, useRef, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  listKnowledgeBases,
  uploadDocumentWithProgress,
  getIngestTask,
  newIdempotencyKey,
} from '../api/p0Client.js';

/**
 * 文件导入页：拖拽上传 + 任务状态轮询。
 * 调用链路：web → nginx /api/ → platform-gateway → platform-management → svc-ingest。
 * 上传进度由 XHR 提供；任务进度/状态经网关拉取 management 的 ingest task 视图。
 */
const TERMINAL_OK = new Set(['COMPLETED', 'SUCCEEDED', 'DONE']);
const TERMINAL_FAIL = new Set(['FAILED', 'ERROR', 'CANCELLED']);

/** 向日志追加一条阶段信息（去重相邻重复） */
function appendStageLog(logs, stage, suffix = '') {
  const line = `正在进行 ${stage}${suffix}`;
  if (!stage) return logs;
  if (logs.length && logs[logs.length - 1] === line) return logs;
  return [...logs, line];
}

export default function Import() {
  const [tasks, setTasks] = useState([]); // { taskId, backendTaskId, name, size, phase, percent, logs, logOpen }
  const [dragOver, setDragOver] = useState(false);
  const [kbs, setKbs] = useState([]);
  const [selectedKbId, setSelectedKbId] = useState('');
  const fileInputRef = useRef(null);

  // 加载知识库列表，默认选中 kb_default 或第一个；上传必须指定 kbId
  useEffect(() => {
    listKnowledgeBases()
      .then((data) => {
        const list = Array.isArray(data)
          ? data
          : (data && (data.list || data.items || data.records)) || [];
        setKbs(list);
        if (list.length) {
          const def = list.find((k) => k.id === 'kb_default') || list[0];
          setSelectedKbId(def.id);
        }
      })
      .catch((e) => console.error('加载知识库列表失败', e));
  }, []);

  const updateTask = (taskId, patch) => {
    setTasks((prev) => prev.map((t) => (t.taskId === taskId ? { ...t, ...patch } : t)));
  };

  /** 轮询导入任务状态（经网关拉取 management ingest task 视图，2s 间隔） */
  const pollStatus = (backendTaskId) => {
    const interval = setInterval(async () => {
      try {
        const t = await getIngestTask(backendTaskId);
        const status = String(t.status || '').toUpperCase();
        const stage = t.stage ? String(t.stage) : '';
        let progress = Number(t.progress);
        if (!Number.isFinite(progress)) progress = 0;
        if (progress > 0 && progress <= 1) progress = Math.round(progress * 100); // 兼容 0~1 小数
        progress = Math.max(0, Math.min(100, Math.round(progress)));
        const errMsg = t.errorMessage || t.message || t.failureMessage || '';

        setTasks((prev) =>
          prev.map((x) => {
            if (x.backendTaskId !== backendTaskId) return x;
            if (TERMINAL_OK.has(status)) {
              clearInterval(interval);
              return {
                ...x,
                phase: 'completed',
                percent: 100,
                logs: [...x.logs, `${stage || '处理'}已完成`],
              };
            }
            if (TERMINAL_FAIL.has(status)) {
              clearInterval(interval);
              return {
                ...x,
                phase: 'error',
                logs: [...x.logs, `${stage || '处理'}失败${errMsg ? `：${errMsg}` : ''}`],
              };
            }
            return {
              ...x,
              phase: 'processing',
              percent: Math.max(x.percent, progress),
              logs: appendStageLog(x.logs, stage, progress ? `（${progress}%）` : ''),
            };
          }),
        );
      } catch (e) {
        console.error('Polling error', e);
      }
    }, 2000);
  };

  const handleFiles = (files) => {
    Array.from(files).forEach((file) => uploadOne(file));
  };

  const uploadOne = async (file) => {
    if (!selectedKbId) {
      alert('请先选择目标知识库');
      return;
    }
    const taskId = 'file-' + Math.random().toString(36).slice(2, 11);
    setTasks((prev) => [
      {
        taskId,
        backendTaskId: '',
        name: file.name,
        size: (file.size / 1024).toFixed(2),
        phase: 'uploading',
        percent: 0,
        logs: [],
        logOpen: false,
      },
      ...prev,
    ]);

    try {
      // 经 platform-gateway 上传；XHR 提供真实上传进度
      const data = await uploadDocumentWithProgress(
        selectedKbId,
        file,
        { idempotencyKey: newIdempotencyKey(), autoParse: true },
        (p) => updateTask(taskId, { percent: p }),
      );
      const ingestTask = data && data.ingestTask;
      const backendTaskId = ingestTask && ingestTask.id;
      // 关联后端任务 ID 并进入"处理中"，开始经网关轮询任务状态
      setTasks((prev) =>
        prev.map((t) =>
          t.taskId === taskId
            ? {
                ...t,
                backendTaskId: backendTaskId || taskId,
                phase: 'processing',
                percent: Math.max(t.percent, 5),
              }
            : t,
        ),
      );
      if (backendTaskId) pollStatus(backendTaskId);
    } catch (err) {
      console.error(err);
      const msg = err && err.message ? err.message : '上传失败';
      updateTask(taskId, { phase: 'error', logs: [`上传失败：${msg}`] });
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  };

  return (
    <div className="import-page">
      <div className="import-inner">
        {/* 页头 + 返回对话入口 */}
        <div className="import-header">
          <div className="head-icon">
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M12 3C7.02944 3 3 7.02944 3 12C3 16.9706 7.02944 21 12 21C16.9706 21 21 16.9706 21 12C21 7.02944 16.9706 3 12 3Z" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M12 8V16M12 8L9.5 10.5M12 8L14.5 10.5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <h1>知识库文件导入</h1>
          <p>将文档导入金融标准化知识库，系统将自动解析、切分并建立检索索引</p>
          <Link to="/chat" className="back-chat">← 返回对话</Link>
        </div>

        {/* 知识库选择器（上传必须指定 kbId） */}
        {kbs.length > 0 && (
          <div className="kb-selector">
            <label htmlFor="import-kb">导入到知识库：</label>
            <select
              id="import-kb"
              value={selectedKbId}
              onChange={(e) => setSelectedKbId(e.target.value)}
            >
              {kbs.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.name || k.id}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* 拖拽上传区 */}
        <div
          className={`upload-area ${dragOver ? 'dragover' : ''}`}
          onClick={() => fileInputRef.current && fileInputRef.current.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
          onDrop={onDrop}
        >
          <div className="upload-icon">
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M6.5 19C4.01472 19 2 16.9853 2 14.5C2 12.1569 3.69897 10.1989 5.94868 9.84519C6.32828 6.70055 9.03032 4.26172 12.2857 4.26172C15.4572 4.26172 18.1041 6.58233 18.5907 9.61412C20.5869 9.99892 22 11.7409 22 13.8462C22 16.2359 20.0966 18.1731 17.75 18.1731H15" stroke="#3d7bff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M12 20.7383V11.5M12 11.5L8.8 14.7M12 11.5L15.2 14.7" stroke="#7a5cff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <p className="upload-title">点击或拖拽文件到此处</p>
          <p className="upload-sub">支持多文件批量上传，文件将自动上传并解析入库</p>
          <div className="upload-chips"><span>PDF</span><span>Markdown</span></div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.md"
            style={{ display: 'none' }}
            onChange={(e) => { handleFiles(e.target.files); e.target.value = ''; }}
          />
        </div>

        {/* 任务列表 */}
        <div className="task-list">
          {tasks.map((t) => (
            <TaskCard key={t.taskId} task={t} onToggleLog={() =>
              setTasks((prev) => prev.map((x) => (x.taskId === t.taskId ? { ...x, logOpen: !x.logOpen } : x)))
            } />
          ))}
        </div>
      </div>

      <style>{`
        .import-page {
          height: 100%; overflow-y: auto;
          background:
            radial-gradient(1100px 520px at 10% -10%, rgba(61,123,255,.10), transparent 60%),
            radial-gradient(900px 480px at 95% -6%, rgba(122,92,255,.10), transparent 55%),
            var(--bg);
        }
        .import-inner {
          max-width: 820px; margin: 0 auto;
          padding: clamp(20px,4vh,44px) clamp(14px,4vw,32px) 48px;
          display: flex; flex-direction: column;
        }

        .kb-selector {
          display: flex; align-items: center; justify-content: center; gap: 10px;
          margin-bottom: 18px; font-size: 13px; color: var(--muted);
        }
        .kb-selector select {
          padding: 6px 12px; border-radius: 10px; border: 1px solid var(--border);
          background: #fff; font-size: 13px; font-weight: 600; color: var(--brand);
          cursor: pointer; outline: none;
        }
        .kb-selector select:focus { border-color: rgba(61,123,255,.6); }

        .import-header { text-align: center; margin-bottom: 26px; position: relative; }
        .head-icon {
          width: 56px; height: 56px; margin: 0 auto 14px; border-radius: 18px;
          background: linear-gradient(135deg, var(--brand), var(--brand2));
          display: flex; align-items: center; justify-content: center;
          box-shadow: 0 14px 30px rgba(61,123,255,.32);
        }
        .head-icon svg { width: 28px; height: 28px; }
        .import-header h1 { margin: 0 0 8px; font-size: clamp(20px,3vw,26px); letter-spacing: .5px; }
        .import-header p { margin: 0; color: var(--muted); font-size: 14px; line-height: 1.6; }
        .back-chat {
          display: inline-flex; align-items: center; gap: 4px;
          margin-top: 14px; padding: 7px 16px;
          font-size: 13px; font-weight: 600; color: var(--brand);
          border: 1px solid rgba(61,123,255,.35); border-radius: 999px;
          background: #fff; transition: all .2s;
        }
        .back-chat:hover { background: rgba(61,123,255,.06); box-shadow: 0 6px 16px rgba(61,123,255,.14); }

        .upload-area {
          position: relative; text-align: center; cursor: pointer;
          border: 2px dashed rgba(61,123,255,.45); border-radius: 22px;
          padding: clamp(36px,6vh,56px) 24px; background: #f8faff;
          transition: border-color .25s, background-color .25s, transform .25s, box-shadow .25s;
          margin-bottom: 26px; overflow: hidden;
        }
        .upload-area:hover, .upload-area.dragover {
          border-color: rgba(61,123,255,.95);
          background: #eef5ff;
          transform: translateY(-2px);
          box-shadow: 0 18px 40px rgba(61,123,255,.14);
        }
        .upload-area.dragover { border-style: solid; }
        .upload-icon {
          width: 76px; height: 76px; margin: 0 auto 16px; border-radius: 50%;
          background: linear-gradient(135deg, rgba(61,123,255,.12), rgba(122,92,255,.12));
          display: flex; align-items: center; justify-content: center;
          animation: float 3s ease-in-out infinite; transition: transform .25s;
        }
        .upload-area:hover .upload-icon { transform: scale(1.08); }
        .upload-icon svg { width: 36px; height: 36px; }
        @keyframes float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-7px); } }
        .upload-title { margin: 0 0 6px; font-size: 17px; font-weight: 600; }
        .upload-sub { margin: 0 0 16px; font-size: 13px; color: var(--muted); }
        .upload-chips { display: flex; justify-content: center; gap: 8px; flex-wrap: wrap; }
        .upload-chips span {
          font-size: 12px; font-weight: 600; letter-spacing: .5px; color: var(--brand);
          background: rgba(61,123,255,.10); border: 1px solid rgba(61,123,255,.22);
          padding: 4px 14px; border-radius: 999px;
        }

        .task-list { display: flex; flex-direction: column; gap: 12px; }
        .task-card {
          background: #fff; border: 1px solid var(--border); border-radius: 18px;
          padding: 16px 18px; box-shadow: 0 6px 18px rgba(26,34,51,.06);
          animation: fadeInUp .3s ease both;
        }
        .task-card:hover { box-shadow: 0 10px 26px rgba(26,34,51,.10); }
        .task-top { display: flex; align-items: center; gap: 16px; }
        .task-info { flex: 1; min-width: 0; display: flex; flex-direction: column; }
        .task-name { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .task-size { font-size: 12px; color: var(--muted); margin-top: 4px; }
        .task-bar-track {
          height: 6px; border-radius: 999px; background: #edf0f7;
          overflow: hidden; margin-top: 10px; position: relative;
        }
        .task-bar {
          height: 100%; border-radius: 999px; background: var(--brand);
          transition: width .4s ease, background-color .3s;
          position: relative; overflow: hidden;
        }
        .task-bar::after {
          content: ''; position: absolute; inset: 0;
          background: linear-gradient(90deg, transparent, rgba(255,255,255,.45), transparent);
          animation: shimmer 1.6s infinite;
        }
        @keyframes shimmer { from { transform: translateX(-100%); } to { transform: translateX(100%); } }

        .status-badge {
          display: inline-flex; align-items: center; justify-content: center; gap: 6px;
          padding: 5px 12px; border-radius: 999px;
          font-size: 12px; font-weight: 600; min-width: 82px; white-space: nowrap; flex: 0 0 auto;
        }
        .status-uploading { background: #fff4e2; color: #d97706; }
        .status-uploading::before {
          content: ''; width: 7px; height: 7px; border-radius: 50%;
          background: #f39c12; animation: blink 1s ease-in-out infinite;
        }
        @keyframes blink { 0%,100% { opacity: .35; } 50% { opacity: 1; } }
        .status-processing { background: #e9f0ff; color: #2563eb; }
        .status-processing::before {
          content: ''; width: 11px; height: 11px; border-radius: 50%;
          border: 2px solid rgba(37,99,235,.25); border-top-color: #2563eb;
          animation: spin .8s linear infinite;
        }
        .status-completed { background: #e5f8ee; color: #16a34a; }
        .status-completed::before { content: '✓'; font-size: 11px; font-weight: 700; }
        .status-error { background: #fdecec; color: #dc2626; }
        .status-error::before { content: '✕'; font-size: 11px; font-weight: 700; }

        .log-toggle {
          display: inline-flex; align-items: center; gap: 6px;
          border: none; background: transparent; cursor: pointer;
          font-size: 12px; font-weight: 600; color: #6c7a89;
          margin-top: 10px; padding: 0; user-select: none;
        }
        .log-toggle::before { content: '▸'; font-size: 10px; color: var(--brand); transition: transform .2s; }
        .log-toggle.open::before { transform: rotate(90deg); }
        .log-list {
          margin: 8px 0 0 4px; padding: 8px 12px; list-style: none;
          background: #f7f9fd; border: 1px solid var(--border); border-radius: 10px;
        }
        .log-list li { margin: 4px 0; line-height: 1.5; color: #5c6880; font-size: 12px; }

        @media (max-width: 600px) {
          .task-top { flex-direction: column; align-items: stretch; }
          .status-badge { align-self: flex-end; }
        }
      `}</style>
    </div>
  );
}

/** 单个任务卡片 */
function TaskCard({ task, onToggleLog }) {
  const phaseText = {
    uploading: '上传中...',
    processing: '处理中...',
    completed: '已完成',
    error: '失败',
  };
  const barColor = task.phase === 'completed' ? '#16a34a' : task.phase === 'error' ? '#dc2626' : undefined;
  const percent = task.phase === 'completed' ? 100 : task.percent;

  return (
    <div className="task-card">
      <div className="task-top">
        <div className="task-info">
          <span className="task-name">{task.name}</span>
          <span className="task-size">{task.size} KB</span>
          <div className="task-bar-track">
            <div className="task-bar" style={{ width: `${percent}%`, backgroundColor: barColor }} />
          </div>
        </div>
        <div className={`status-badge status-${task.phase}`}>{phaseText[task.phase] || task.phase}</div>
      </div>
      <button className={`log-toggle ${task.logOpen ? 'open' : ''}`} onClick={onToggleLog}>
        日志（已完成{task.logs.filter((l) => l.includes('已完成')).length}，点击展开）
      </button>
      {task.logOpen && (
        <ul className="log-list">
          {task.logs.length === 0 ? <li>暂无日志</li> : task.logs.map((l, i) => <li key={i}>{l}</li>)}
        </ul>
      )}
    </div>
  );
}
