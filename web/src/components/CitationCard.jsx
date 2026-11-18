import { useEffect } from 'react';

/**
 * 引用卡片：展示 chunkId / docId / clauseNo / chapterPath / pageNo / quote。
 * P0 结构已包含 P1 #408 条款级溯源所需字段，缺失字段自动降级隐藏。
 */
export default function CitationCard({ citation = {}, index, onSelect }) {
  const label = citation.standardNo || citation.documentTitle || '标准条款';
  const clause = citation.clauseNo ? `第 ${citation.clauseNo} 条` : '';
  const quote = citation.quote || '';

  return (
    <button type="button" className="cite-card" onClick={() => onSelect && onSelect(citation)}>
      <span className="cite-index">{index}</span>
      <span className="cite-main">
        <span className="cite-title">
          {label}
          {clause && <span className="cite-clause">{clause}</span>}
        </span>
        {quote ? <span className="cite-quote">{quote}</span> : <span className="cite-quote muted">暂无摘录</span>}
        {(citation.chapterPath || citation.pageNo) && (
          <span className="cite-loc">
            {citation.chapterPath}
            {citation.chapterPath && citation.pageNo ? ' · ' : ''}
            {citation.pageNo ? `第 ${citation.pageNo} 页` : ''}
          </span>
        )}
      </span>
    </button>
  );
}

/** 引用原文抽屉：点击卡片后展示可核对的完整摘录与定位信息。 */
export function CitationDrawer({ citation, onClose }) {
  useEffect(() => {
    if (!citation) return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [citation, onClose]);

  if (!citation) return null;

  const rows = [
    ['标准编号', citation.standardNo || citation.documentTitle],
    ['条款编号', citation.clauseNo],
    ['章节路径', citation.chapterPath],
    ['页码', citation.pageNo],
    ['文档 ID', citation.docId || citation.documentId],
    ['分块 ID', citation.chunkId],
  ].filter(([, value]) => value !== undefined && value !== null && value !== '');

  return (
    <div className="cite-drawer-mask" role="presentation" onClick={onClose}>
      <aside
        className="cite-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="引用原文"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="cite-drawer-head">
          <div>
            <div className="cite-drawer-kicker">引用原文</div>
            <div className="cite-drawer-title">
              {citation.standardNo || '标准条款'}
              {citation.clauseNo ? ` · 第 ${citation.clauseNo} 条` : ''}
            </div>
          </div>
          <button type="button" className="cite-drawer-close" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </header>

        <blockquote className="cite-drawer-quote">
          {citation.quote || '当前引用未包含摘录文本，可结合条款编号查阅标准原文。'}
        </blockquote>

        <dl className="cite-drawer-meta">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>

        <p className="cite-drawer-hint">
          引用内容来自检索命中的标准条款；如需完整上下文，请按上方条款编号查阅标准原文。
        </p>

        <style>{`
          .cite-drawer-mask {
            position: fixed; inset: 0; z-index: 60;
            background: rgba(15, 23, 42, .38);
            backdrop-filter: blur(2px);
            display: flex; justify-content: flex-end;
            animation: fadeInUp .18s ease both;
          }
          .cite-drawer {
            width: min(440px, 92vw); height: 100%;
            background: #fff; border-left: 1px solid var(--border);
            box-shadow: -18px 0 48px rgba(15,23,42,.18);
            padding: 20px 22px; overflow-y: auto;
            display: flex; flex-direction: column; gap: 16px;
          }
          .cite-drawer-head { display: flex; align-items: flex-start; gap: 12px; }
          .cite-drawer-kicker { font-size: 12px; color: var(--muted); font-weight: 600; }
          .cite-drawer-title { font-size: 16px; font-weight: 700; margin-top: 4px; }
          .cite-drawer-close {
            margin-left: auto; width: 32px; height: 32px;
            border: 1px solid var(--border); border-radius: 10px;
            background: #fff; color: var(--muted); cursor: pointer; font-size: 13px;
          }
          .cite-drawer-close:hover { color: var(--text); }
          .cite-drawer-quote {
            margin: 0; padding: 14px 16px;
            background: #f7f9fe; border: 1px solid var(--border);
            border-left: 3px solid rgba(61,123,255,.55);
            border-radius: 0 12px 12px 0;
            font-size: 14px; line-height: 1.8; color: #33405a;
            white-space: pre-wrap;
          }
          .cite-drawer-meta { margin: 0; display: flex; flex-direction: column; gap: 10px; }
          .cite-drawer-meta > div { display: flex; gap: 12px; font-size: 13px; }
          .cite-drawer-meta dt { flex: 0 0 72px; color: var(--muted); margin: 0; }
          .cite-drawer-meta dd { margin: 0; color: #33405a; word-break: break-all; }
          .cite-drawer-hint { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.7; }
        `}</style>
      </aside>
    </div>
  );
}
