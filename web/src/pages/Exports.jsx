import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  createKbExport,
  downloadExport,
  generateSummary,
  getExport,
  getLatestSummary,
  listDocuments,
  listKnowledgeBases,
  retryExport,
} from '../api/p0Client.js';

const TYPE_LABEL = { CLAUSES: '条目化条款', STANDARD_SUMMARY: '标准简介' };
const FORMATS = { CLAUSES: ['PDF', 'XLSX'], STANDARD_SUMMARY: ['PDF', 'DOCX'] };
const STATUS = {
  QUEUED: { text: '排队中', color: '#8a6d1f', bg: '#fff7e0' },
  RUNNING: { text: '生成中', color: '#1d4ed8', bg: '#e8f0ff' },
  SUCCEEDED: { text: '已完成', color: '#067647', bg: '#e7f8ef' },
  FAILED: { text: '失败', color: '#b42318', bg: '#feeceb' },
  EXPIRED: { text: '已过期', color: '#667085', bg: '#f2f4f7' },
};
const PAGE_BG = 'var(--bg, #f6f8fb)';

const s = {
  page: { padding: '24px 28px 40px', maxWidth: 1280, margin: '0 auto' },
  head: { display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' },
  title: { margin: 0, fontSize: 24, letterSpacing: '-0.02em' },
  sub: { margin: '6px 0 0', color: '#667085', fontSize: 13 },
  grid: { display: 'grid', gridTemplateColumns: 'minmax(0, 1.55fr) minmax(320px, 1fr)', gap: 18, marginTop: 20 },
  card: { background: '#fff', borderRadius: 16, border: '1px solid #e5e9f0', padding: 20, boxShadow: '0 8px 26px rgba(16,24,40,.04)' },
  label: { fontSize: 12, fontWeight: 600, color: '#475467', marginBottom: 6, display: 'block' },
  select: { width: '100%', height: 38, borderRadius: 10, border: '1px solid #d0d5dd', padding: '0 10px', background: '#fff', fontSize: 13 },
  row: { display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' },
  btn: { height: 38, borderRadius: 10, border: '1px solid #d0d5dd', background: '#fff', padding: '0 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600, color: '#344054' },
  primary: { background: '#1d4ed8', borderColor: '#1d4ed8', color: '#fff' },
  ghost: { background: '#f8fafc' },
  hint: { fontSize: 12, color: '#667085', lineHeight: 1.7 },
  error: { marginTop: 12, padding: '10px 12px', borderRadius: 10, background: '#fef3f2', color: '#b42318', fontSize: 13 },
  notice: { marginTop: 12, padding: '10px 12px', borderRadius: 10, background: '#ecfdf3', color: '#067647', fontSize: 13 },
  element: { padding: '12px 0', borderBottom: '1px dashed #e5e9f0' },
  elementHead: { display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', marginBottom: 6 },
  elementLabel: { fontSize: 13, fontWeight: 700, color: '#1d2939' },
  elementValue: { fontSize: 14, lineHeight: 1.75, color: '#344054', whiteSpace: 'pre-wrap' },
  missing: { fontSize: 11, color: '#b54708', background: '#fffaeb', borderRadius: 99, padding: '2px 8px' },
  source: { marginTop: 6, fontSize: 12, color: '#667085' },
  code: { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 11, wordBreak: 'break-all' },
  task: { border: '1px solid #e5e9f0', borderRadius: 12, padding: 14, marginTop: 10 },
  progressTrack: { height: 6, background: '#eef2f7', borderRadius: 99, overflow: 'hidden', marginTop: 10 },
  progressBar: { height: '100%', background: 'linear-gradient(90deg,#2563eb,#60a5fa)', transition: 'width .3s' },
  badge: { fontSize: 11, fontWeight: 700, borderRadius: 99, padding: '3px 9px' },
  tiny: { fontSize: 12, color: '#667085' },
  empty: { padding: '28px 8px', textAlign: 'center', color: '#98a2b3', fontSize: 13 },
};

function statusBadge(status) {
  const meta = STATUS[status] || { text: status, color: '#475467', bg: '#f2f4f7' };
  return <span style={{ ...s.badge, color: meta.color, background: meta.bg }}>{meta.text}</span>;
}

function formatTime(value) {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export default function Exports() {
  const [kbs, setKbs] = useState([]);
  const [kbId, setKbId] = useState('');
  const [docs, setDocs] = useState([]);
  const [docId, setDocId] = useState('');
  const [summary, setSummary] = useState(null);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [type, setType] = useState('CLAUSES');
  const [format, setFormat] = useState('PDF');
  const [jobs, setJobs] = useState([]);

  const selectedDoc = useMemo(() => docs.find((item) => item.id === docId) || null, [docs, docId]);

  useEffect(() => {
    let alive = true;
    listKnowledgeBases()
      .then((data) => {
        if (!alive) return;
        const items = data?.items || data || [];
        setKbs(items);
        if (items.length) setKbId(items[0].id);
      })
      .catch((err) => alive && setError(err.message || '知识库加载失败'));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!kbId) return undefined;
    let alive = true;
    setDocs([]);
    setDocId('');
    listDocuments({ kbId, page: 1, pageSize: 100 })
      .then((data) => {
        if (!alive) return;
        const items = (data?.items || []).filter((item) => item.status === 'READY');
        setDocs(items);
        if (items.length) setDocId(items[0].id);
      })
      .catch((err) => alive && setError(err.message || '文档加载失败'));
    return () => {
      alive = false;
    };
  }, [kbId]);

  useEffect(() => {
    if (!docId) {
      setSummary(null);
      return undefined;
    }
    let alive = true;
    setLoadingSummary(true);
    setSummary(null);
    getLatestSummary(docId)
      .then((data) => alive && setSummary(data))
      .catch(() => alive && setSummary(null))
      .finally(() => alive && setLoadingSummary(false));
    return () => {
      alive = false;
    };
  }, [docId]);

  useEffect(() => {
    const active = jobs.filter((job) => job.status === 'QUEUED' || job.status === 'RUNNING');
    if (!active.length) return undefined;
    const timer = setInterval(async () => {
      const updates = await Promise.all(active.map((job) => getExport(job.id).catch(() => null)));
      setJobs((prev) => prev.map((job) => updates.find((next) => next && next.id === job.id) || job));
    }, 1200);
    return () => clearInterval(timer);
  }, [jobs]);

  const onGenerate = useCallback(async () => {
    if (!docId) return;
    setBusy('summary');
    setError('');
    setNotice('');
    try {
      const data = await generateSummary(docId, { regenerate: Boolean(summary) });
      setSummary(data);
      setNotice(data ? '标准简介已生成，可核对五要素来源。' : '生成完成');
    } catch (err) {
      setError(err.message || '简介生成失败');
    } finally {
      setBusy('');
    }
  }, [docId, summary]);

  const onCreateExport = useCallback(async () => {
    if (!docId || !kbId) return;
    setBusy('export');
    setError('');
    setNotice('');
    try {
      const job = await createKbExport(kbId, { documentId: docId, type, format });
      setJobs((prev) => [job, ...prev.filter((item) => item.id !== job.id)]);
      setNotice(`已创建${TYPE_LABEL[type]}导出任务，正在生成 ${format} 文件。`);
    } catch (err) {
      setError(err.message || '创建导出任务失败');
    } finally {
      setBusy('');
    }
  }, [docId, kbId, type, format]);

  const onDownload = useCallback(async (job) => {
    setBusy(job.id);
    setError('');
    try {
      const blob = await downloadExport(job.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = job.fileName || `export-${job.id}.${String(job.format).toLowerCase()}`;
      link.click();
      URL.revokeObjectURL(url);
      setNotice('下载已开始。');
    } catch (err) {
      setError(err.message || '下载失败');
    } finally {
      setBusy('');
    }
  }, []);

  const onRetry = useCallback(async (job) => {
    setBusy(job.id);
    setError('');
    try {
      const next = await retryExport(job.id);
      setJobs((prev) => prev.map((item) => (item.id === next.id ? next : item)));
      setNotice('已重新排队导出任务。');
    } catch (err) {
      setError(err.message || '重试失败');
    } finally {
      setBusy('');
    }
  }, []);

  const formats = FORMATS[type] || ['PDF'];
  const changeType = (value) => {
    setType(value);
    setFormat((FORMATS[value] || ['PDF'])[0]);
  };

  return (
    <div style={{ ...s.page, background: PAGE_BG, minHeight: '100%' }}>
      <div style={s.head}>
        <div>
          <h1 style={s.title}>标准简介与条目化导出</h1>
          <p style={s.sub}>简介五要素抽取自原文，缺失项标注“未识别”；导出异步执行，支持 PDF / Excel / Word。</p>
        </div>
        <div style={{ ...s.hint, textAlign: 'right' }}>
          导出前会再次校验知识库权限<br />
          下载文件带有效期与 SHA-256 指纹
        </div>
      </div>

      {error ? <div style={s.error}>{error}</div> : null}
      {notice ? <div style={s.notice}>{notice}</div> : null}

      <div style={s.grid}>
        <section style={s.card}>
          <div style={s.row}>
            <div style={{ flex: '1 1 240px' }}>
              <label style={s.label}>知识库</label>
              <select style={s.select} value={kbId} onChange={(event) => setKbId(event.target.value)}>
                {kbs.length === 0 ? <option value="">暂无知识库</option> : null}
                {kbs.map((kb) => (
                  <option key={kb.id} value={kb.id}>{kb.name || kb.id}</option>
                ))}
              </select>
            </div>
            <div style={{ flex: '1 1 280px' }}>
              <label style={s.label}>文档（仅已完成解析）</label>
              <select style={s.select} value={docId} onChange={(event) => setDocId(event.target.value)}>
                {docs.length === 0 ? <option value="">暂无可导出文档</option> : null}
                {docs.map((doc) => (
                  <option key={doc.id} value={doc.id}>
                    {(doc.standardNo ? `${doc.standardNo} · ` : '') + (doc.filename || doc.id)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: 16 }}>标准简介</h3>
              <div style={s.tiny}>
                {summary
                  ? `版本 v${summary.summaryVersion || summary.documentVersion || '-'} · 抽取器 ${summary.promptTemplateVersion || '-'} · 置信度 ${summary.confidence ?? '-'}`
                  : '尚未生成简介'}
              </div>
            </div>
            <button
              type="button"
              style={{ ...s.btn, ...s.primary, opacity: busy === 'summary' || !docId ? 0.65 : 1 }}
              disabled={busy === 'summary' || !docId}
              onClick={onGenerate}
            >
              {busy === 'summary' ? '生成中…' : summary ? '重新生成' : '生成简介'}
            </button>
          </div>

          <div style={{ marginTop: 8 }}>
            {loadingSummary ? <div style={s.empty}>加载简介…</div> : null}
            {!loadingSummary && !summary ? (
              <div style={s.empty}>
                暂无简介。点击“生成简介”从条目化条款中抽取编号、名称、适用范围、技术内容与实施日期。
              </div>
            ) : null}
            {!loadingSummary && summary
              ? (summary.elements || []).map((element) => (
                  <div key={element.key} style={s.element}>
                    <div style={s.elementHead}>
                      <span style={s.elementLabel}>{element.label}</span>
                      {element.value === '未识别' ? <span style={s.missing}>未识别</span> : null}
                    </div>
                    <div style={s.elementValue}>{element.value}</div>
                    {element.sourceClauseNo || element.sourceQuote ? (
                      <div style={s.source}>
                        来源：{element.sourceClauseNo || '-'}
                        {element.sourceQuote ? ` · ${element.sourceQuote}` : ''}
                      </div>
                    ) : null}
                  </div>
                ))
              : null}
          </div>
        </section>

        <section style={s.card}>
          <h3 style={{ margin: 0, fontSize: 16 }}>创建导出任务</h3>
          <p style={{ ...s.hint, marginTop: 6 }}>
            条款清单导出 PDF / Excel，逐条包含编号、标题、正文与所属章节；标准简介导出 PDF / Word。
          </p>

          <div style={{ marginTop: 14 }}>
            <label style={s.label}>导出内容</label>
            <select style={s.select} value={type} onChange={(event) => changeType(event.target.value)}>
              <option value="CLAUSES">条目化条款（#406）</option>
              <option value="STANDARD_SUMMARY">标准简介（#405）</option>
            </select>
          </div>

          <div style={{ marginTop: 12 }}>
            <label style={s.label}>文件格式</label>
            <select style={s.select} value={format} onChange={(event) => setFormat(event.target.value)}>
              {formats.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </div>

          <button
            type="button"
            style={{ ...s.btn, ...s.primary, width: '100%', marginTop: 16, opacity: busy === 'export' || !docId ? 0.65 : 1 }}
            disabled={busy === 'export' || !docId}
            onClick={onCreateExport}
          >
            {busy === 'export' ? '创建中…' : `导出${TYPE_LABEL[type]}`}
          </button>

          <div style={{ ...s.hint, marginTop: 12 }}>
            当前文档：{selectedDoc ? (selectedDoc.filename || selectedDoc.id) : '未选择'}
            <br />
            导出任务会记录操作人、范围、条款数与文件 hash，文件到期自动清理。
          </div>
        </section>
      </div>

      <section style={{ ...s.card, marginTop: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>导出任务</h3>
          <span style={s.tiny}>{jobs.length ? `共 ${jobs.length} 个任务` : '暂无任务'}</span>
        </div>
        {jobs.length === 0 ? <div style={s.empty}>创建导出任务后，可在此查看进度并下载文件。</div> : null}
        {jobs.map((job) => (
          <div key={job.id} style={s.task}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 14 }}>
                  {TYPE_LABEL[job.type] || job.type} · {job.format}
                  <span style={{ marginLeft: 8 }}>{statusBadge(job.status)}</span>
                </div>
                <div style={{ ...s.tiny, marginTop: 6 }}>
                  {job.fileName || '等待生成文件名'} · 条款数 {job.clauseCount ?? '-'}
                  {job.retryCount ? ` · 重试 ${job.retryCount}/${job.maxRetries}` : ''}
                </div>
              </div>
              <div style={s.row}>
                {job.status === 'SUCCEEDED' ? (
                  <button type="button" style={s.btn} disabled={busy === job.id} onClick={() => onDownload(job)}>
                    {busy === job.id ? '处理中…' : '下载'}
                  </button>
                ) : null}
                {job.status === 'FAILED' || job.status === 'EXPIRED' ? (
                  <button type="button" style={s.btn} disabled={busy === job.id} onClick={() => onRetry(job)}>
                    重试
                  </button>
                ) : null}
              </div>
            </div>
            <div style={s.progressTrack}>
              <div style={{ ...s.progressBar, width: `${Math.max(0, Math.min(100, job.progress || 0))}%` }} />
            </div>
            <div style={{ ...s.tiny, marginTop: 8, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              <span>进度 {job.progress || 0}%</span>
              <span>创建 {formatTime(job.createdAt)}</span>
              {job.finishedAt ? <span>完成 {formatTime(job.finishedAt)}</span> : null}
              {job.expiresAt ? <span>有效期至 {formatTime(job.expiresAt)}</span> : null}
              {job.sizeBytes ? <span>{(job.sizeBytes / 1024).toFixed(1)} KB</span> : null}
            </div>
            {job.fileHash ? <div style={{ ...s.tiny, ...s.code, marginTop: 6 }}>SHA-256 {job.fileHash}</div> : null}
            {job.errorMessage ? <div style={{ ...s.tiny, color: '#b42318', marginTop: 6 }}>{job.errorCode} · {job.errorMessage}</div> : null}
          </div>
        ))}
      </section>
    </div>
  );
}
