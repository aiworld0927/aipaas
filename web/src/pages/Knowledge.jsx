import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listKnowledgeBases,
  createKnowledgeBase,
  updateKnowledgeBase,
  deleteKnowledgeBase,
  getKnowledgeBaseIndexStatus,
  listDocuments,
  deleteDocument,
  initDocumentUpload,
  uploadDocumentPart,
  completeDocumentUpload,
  listDocumentGrants,
  replaceDocumentGrants,
  listUsers,
  listRoles,
} from '../api/p0Client.js';
import { sha256File } from '../utils/sha256.js';

const MAX_BYTES = 100 * 1024 * 1024;
const PAGE_SIZE = 10;
const TERMINAL = new Set(['READY', 'FAILED']);

const DOC_STATUS = {
  QUEUED: ['排队中', 'queued'],
  PARSING: ['解析中', 'running'],
  PROCESSING: ['入库中', 'running'],
  READY: ['已入库', 'ok'],
  FAILED: ['失败', 'bad'],
};

const INDEX_STATUS = {
  EMPTY: '空库',
  BUILDING: '索引构建中',
  READY: '索引就绪',
  FAILED: '索引失败',
  PENDING: '待索引',
  INDEXED: '已索引',
  DELETED: '索引已清理',
};

function describe(error) {
  if (!error) return '操作失败';
  const code = error.code ? `（${error.code}${error.traceId ? ` · ${error.traceId}` : ''}）` : '';
  return `${error.message || '操作失败'}${code}`;
}

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export default function Knowledge() {
  const [kbs, setKbs] = useState([]);
  const [kbLoading, setKbLoading] = useState(false);
  const [selectedId, setSelectedId] = useState('');
  const [docs, setDocs] = useState({ items: [], total: 0 });
  const [index, setIndex] = useState(null);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState('');
  const [docLoading, setDocLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editor, setEditor] = useState(null);
  const [saving, setSaving] = useState(false);
  const [uploads, setUploads] = useState([]);
  const [dragOver, setDragOver] = useState(false);
  const [grantDoc, setGrantDoc] = useState(null);
  const fileRef = useRef(null);

  const selected = kbs.find((item) => item.id === selectedId) || null;

  const loadKbs = useCallback(async (preferredId) => {
    setKbLoading(true);
    try {
      const data = await listKnowledgeBases({ page: 1, pageSize: 100 });
      setKbs(data.items || []);
      const next = preferredId || selectedId || (data.items && data.items[0] ? data.items[0].id : '');
      setSelectedId(next);
      setError('');
      return next;
    } catch (err) {
      setError(describe(err));
      return '';
    } finally {
      setKbLoading(false);
    }
  }, [selectedId]);

  const loadDetail = useCallback(async (kbId, nextPage, nextKeyword, silent) => {
    if (!kbId) return;
    if (!silent) setDocLoading(true);
    try {
      const [documents, status] = await Promise.all([
        listDocuments({ kbId, page: nextPage, pageSize: PAGE_SIZE, keyword: nextKeyword }),
        getKnowledgeBaseIndexStatus(kbId),
      ]);
      setDocs(documents);
      setIndex(status);
      if (!silent) setError('');
    } catch (err) {
      if (!silent) setError(describe(err));
    } finally {
      if (!silent) setDocLoading(false);
    }
  }, []);

  useEffect(() => {
    loadKbs().then((kbId) => {
      if (kbId) loadDetail(kbId, 1, '');
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const refreshRef = useRef(() => {});
  refreshRef.current = () => {
    if (selectedId) loadDetail(selectedId, page, keyword, true);
  };

  useEffect(() => {
    if (!selectedId) return undefined;
    const timer = setInterval(() => refreshRef.current(), 4000);
    return () => clearInterval(timer);
  }, [selectedId]);

  const selectKb = (kbId) => {
    setSelectedId(kbId);
    setPage(1);
    setKeyword('');
    loadDetail(kbId, 1, '');
  };

  const submitEditor = async (event) => {
    event.preventDefault();
    if (!editor || saving) return;
    setSaving(true);
    try {
      const payload = {
        name: editor.name.trim(),
        description: editor.description || undefined,
        domain: editor.domain || undefined,
        standardSystem: editor.standardSystem || undefined,
        config: {
          chunkSize: Number(editor.chunkSize) || 512,
          chunkOverlap: Number(editor.chunkOverlap) || 64,
          retrievalTopK: Number(editor.retrievalTopK) || 5,
        },
      };
      if (editor.id) {
        await updateKnowledgeBase(editor.id, payload);
        setNotice('知识库配置已更新');
        await loadKbs(editor.id);
        await loadDetail(editor.id, page, keyword);
      } else {
        const created = await createKnowledgeBase(payload);
        setNotice('知识库已创建');
        setSelectedId(created.id);
        await loadKbs(created.id);
        await loadDetail(created.id, 1, '');
      }
      setEditor(null);
      setError('');
    } catch (err) {
      setError(describe(err));
    } finally {
      setSaving(false);
    }
  };

  const removeKb = async (kb) => {
    if (!window.confirm(`删除知识库「${kb.name}」？库内文档将软删除并异步清理索引，不影响其他知识库。`)) return;
    try {
      await deleteKnowledgeBase(kb.id);
      setNotice('知识库已删除');
      setSelectedId('');
      setDocs({ items: [], total: 0 });
      setIndex(null);
      await loadKbs();
      setError('');
    } catch (err) {
      setError(describe(err));
    }
  };

  const removeDoc = async (doc) => {
    if (!window.confirm(`删除文档「${doc.fileName}」？删除后立即退出检索，索引异步清理。`)) return;
    try {
      await deleteDocument(doc.id);
      setNotice('文档已删除，索引清理中');
      await loadDetail(selectedId, page, keyword);
      await loadKbs(selectedId);
      setError('');
    } catch (err) {
      setError(describe(err));
    }
  };

  const patchUpload = (id, patch) =>
    setUploads((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));

  const uploadOne = async (file, kbId) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setUploads((prev) => [{ id, name: file.name, size: file.size, percent: 0, phase: 'hashing', message: '计算文件摘要' }, ...prev]);
    try {
      if (!kbId) throw new Error('请先选择知识库');
      if (file.size > MAX_BYTES) throw new Error('单文件不得超过 100MB');
      const fileHash = await sha256File(file);
      patchUpload(id, { phase: 'uploading', percent: 1, message: '初始化上传' });
      const init = await initDocumentUpload(kbId, {
        fileName: file.name,
        sizeBytes: file.size,
        fileHash,
        autoParse: true,
      });
      if (init.duplicate) {
        patchUpload(id, { phase: 'done', percent: 100, message: '文件已存在，已复用解析结果' });
        await loadDetail(kbId, 1, keyword);
        return;
      }
      const { uploadId, partSize, totalParts } = init;
      const missing = Array.isArray(init.missingParts) && init.missingParts.length
        ? init.missingParts
        : Array.from({ length: totalParts }, (_, i) => i + 1);
      let completedBytes = 0;
      for (const partNo of missing) {
        const start = (partNo - 1) * partSize;
        const end = Math.min(start + partSize, file.size);
        const chunk = file.slice(start, end);
        await uploadDocumentPart(kbId, uploadId, partNo, chunk, {
          offset: start,
          onProgress: (partPercent) => {
            const percent = Math.min(99, Math.round(((completedBytes + (chunk.size * partPercent) / 100) / file.size) * 100));
            patchUpload(id, { percent, message: `上传分片 ${partNo}/${totalParts}` });
          },
        });
        completedBytes += chunk.size;
      }
      patchUpload(id, { percent: 99, message: '合并并创建解析任务' });
      await completeDocumentUpload(kbId, uploadId);
      patchUpload(id, { phase: 'done', percent: 100, message: '已提交解析' });
      await loadDetail(kbId, 1, keyword);
      await loadKbs(kbId);
      setError('');
    } catch (err) {
      patchUpload(id, { phase: 'failed', message: describe(err) });
      setError(describe(err));
    }
  };

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (!selectedId) {
      setError('请先选择或创建知识库');
      return;
    }
    for (const file of files) {
      await uploadOne(file, selectedId);
    }
  };

  const totalPages = Math.max(1, Math.ceil((docs.total || 0) / PAGE_SIZE));
  const activeCount = (docs.items || []).filter((doc) => !TERMINAL.has(doc.status)).length;

  return (
    <div className="kb-page">
      <div className="kb-head">
        <div>
          <h1>知识库管理</h1>
          <p>按标准域与标准体系组织知识库，上传后自动创建解析任务并展示索引进度。</p>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => setEditor({ name: '', description: '', domain: '', standardSystem: '', chunkSize: 512, chunkOverlap: 64, retrievalTopK: 5 })}
        >
          新建知识库
        </button>
      </div>

      {error && <div className="kb-banner kb-banner-error">{error}</div>}
      {notice && <div className="kb-banner kb-banner-ok">{notice}</div>}

      <div className="kb-grid">
        <section className="kb-card kb-list">
          <div className="kb-card-head">
            <span>知识库（{kbs.length}）</span>
            {kbLoading && <span className="kb-muted">刷新中</span>}
          </div>
          {kbs.length === 0 && !kbLoading && <div className="kb-empty">暂无知识库，请先创建</div>}
          {kbs.map((kb) => (
            <button
              key={kb.id}
              className={`kb-item ${kb.id === selectedId ? 'active' : ''}`}
              onClick={() => selectKb(kb.id)}
            >
              <div className="kb-item-top">
                <strong>{kb.name}</strong>
                <span className={`kb-chip idx-${(kb.indexStatus || '').toLowerCase()}`}>
                  {INDEX_STATUS[kb.indexStatus] || kb.indexStatus}
                </span>
              </div>
              <div className="kb-item-meta">
                <span>{kb.domain || '未分类域'}</span>
                <span>{kb.standardSystem || '未指定体系'}</span>
              </div>
              <div className="kb-item-meta">
                <span>{kb.documentCount} 篇文档</span>
                <span>{kb.chunkCount} 个片段</span>
              </div>
            </button>
          ))}
        </section>

        <section className="kb-card kb-detail">
          {!selected && <div className="kb-empty">选择左侧知识库查看详情</div>}
          {selected && (
            <>
              <div className="kb-detail-head">
                <div>
                  <h2>{selected.name}</h2>
                  <p>{selected.description || '暂无描述'}</p>
                </div>
                <div className="kb-actions">
                  <button className="btn" onClick={() => setEditor({
                    id: selected.id,
                    name: selected.name,
                    description: selected.description || '',
                    domain: selected.domain || '',
                    standardSystem: selected.standardSystem || '',
                    chunkSize: selected.config?.chunkSize || 512,
                    chunkOverlap: selected.config?.chunkOverlap || 64,
                    retrievalTopK: selected.config?.retrievalTopK || 5,
                  })}>
                    重命名 / 配置
                  </button>
                  <button className="btn kb-danger" onClick={() => removeKb(selected)}>删除</button>
                </div>
              </div>

              <div className="kb-stats">
                <div><span>标准域</span><strong>{selected.domain || '-'}</strong></div>
                <div><span>标准体系</span><strong>{selected.standardSystem || '-'}</strong></div>
                <div><span>索引进度</span><strong>{index ? `${INDEX_STATUS[index.indexStatus] || index.indexStatus} · ${index.chunkCount} chunks` : '-'}</strong></div>
                <div><span>解析队列</span><strong>{activeCount} 篇处理中</strong></div>
              </div>

              <div
                className={`kb-drop ${dragOver ? 'over' : ''}`}
                onClick={() => fileRef.current && fileRef.current.click()}
                onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
                onDragLeave={(event) => { event.preventDefault(); setDragOver(false); }}
                onDrop={(event) => { event.preventDefault(); setDragOver(false); handleFiles(event.dataTransfer.files); }}
              >
                <strong>点击或拖拽上传文档</strong>
                <span>支持 PDF / Word / Excel / MD / TXT，单文件不超过 100MB；分片上传 5MB/片</span>
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  hidden
                  accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.md,.txt"
                  onChange={(event) => { handleFiles(event.target.files); event.target.value = ''; }}
                />
              </div>

              {uploads.length > 0 && (
                <div className="kb-uploads">
                  {uploads.slice(0, 6).map((item) => (
                    <div className="kb-upload" key={item.id}>
                      <div className="kb-upload-top">
                        <span className="kb-upload-name">{item.name}</span>
                        <span className={`kb-chip phase-${item.phase}`}>
                          {item.phase === 'done' ? '完成' : item.phase === 'failed' ? '失败' : '上传中'}
                        </span>
                      </div>
                      <div className="kb-progress"><i style={{ width: `${item.percent}%` }} /></div>
                      <div className="kb-upload-msg">{item.message}</div>
                    </div>
                  ))}
                </div>
              )}

              <div className="kb-toolbar">
                <input
                  className="input"
                  placeholder="按文件名搜索"
                  value={keyword}
                  onChange={(event) => setKeyword(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      setPage(1);
                      loadDetail(selectedId, 1, keyword);
                    }
                  }}
                />
                <button className="btn" onClick={() => { setPage(1); loadDetail(selectedId, 1, keyword); }}>
                  搜索
                </button>
                <span className="kb-muted">共 {docs.total || 0} 篇</span>
              </div>

              <div className="kb-table-wrap">
                <table className="kb-table">
                  <thead>
                    <tr>
                      <th>文件名</th>
                      <th>大小</th>
                      <th>解析状态</th>
                      <th>索引状态</th>
                      <th>进度</th>
                      <th>上传时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(docs.items || []).length === 0 && (
                      <tr><td colSpan="7" className="kb-empty-cell">{docLoading ? '加载中' : '暂无文档'}</td></tr>
                    )}
                    {(docs.items || []).map((doc) => {
                      const [label, tone] = DOC_STATUS[doc.status] || [doc.status, 'queued'];
                      return (
                        <tr key={doc.id}>
                          <td className="kb-name" title={doc.fileName}>{doc.fileName}</td>
                          <td>{formatSize(doc.sizeBytes)}</td>
                          <td><span className={`kb-chip st-${tone}`}>{label}</span></td>
                          <td><span className={`kb-chip idx-${(doc.indexStatus || '').toLowerCase()}`}>{INDEX_STATUS[doc.indexStatus] || doc.indexStatus}</span></td>
                          <td>
                            <div className="kb-inline-progress">
                              <i style={{ width: `${doc.status === 'READY' ? 100 : doc.progress || 0}%` }} />
                              <span>{doc.status === 'READY' ? 100 : doc.progress || 0}%</span>
                            </div>
                          </td>
                          <td>{doc.createdAt ? new Date(doc.createdAt).toLocaleString('zh-CN') : '-'}</td>
                          <td>
                            <button className="kb-link" onClick={() => setGrantDoc(doc)}>权限</button>
                            <span style={{ margin: '0 8px', color: 'var(--border)' }}>|</span>
                            <button className="kb-link kb-danger" onClick={() => removeDoc(doc)}>删除</button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="kb-pager">
                <button className="btn" disabled={page <= 1} onClick={() => { const next = page - 1; setPage(next); loadDetail(selectedId, next, keyword); }}>上一页</button>
                <span>{page} / {totalPages}</span>
                <button className="btn" disabled={page >= totalPages} onClick={() => { const next = page + 1; setPage(next); loadDetail(selectedId, next, keyword); }}>下一页</button>
              </div>
            </>
          )}
        </section>
      </div>

      {editor && (
        <div className="kb-mask" onClick={() => !saving && setEditor(null)}>
          <form className="kb-modal" onClick={(event) => event.stopPropagation()} onSubmit={submitEditor}>
            <h3>{editor.id ? '知识库配置' : '新建知识库'}</h3>
            <label>
              名称
              <input className="input" required maxLength={128} value={editor.name}
                onChange={(event) => setEditor({ ...editor, name: event.target.value })} />
            </label>
            <label>
              描述
              <input className="input" maxLength={1024} value={editor.description}
                onChange={(event) => setEditor({ ...editor, description: event.target.value })} />
            </label>
            <div className="kb-form-row">
              <label>
                标准域
                <input className="input" maxLength={64} placeholder="如 JR / PBOC" value={editor.domain}
                  onChange={(event) => setEditor({ ...editor, domain: event.target.value })} />
              </label>
              <label>
                标准体系
                <input className="input" maxLength={128} placeholder="如 JR/T 0088" value={editor.standardSystem}
                  onChange={(event) => setEditor({ ...editor, standardSystem: event.target.value })} />
              </label>
            </div>
            <div className="kb-form-row">
              <label>
                分片大小（token）
                <input className="input" type="number" min="64" max="2048" value={editor.chunkSize}
                  onChange={(event) => setEditor({ ...editor, chunkSize: event.target.value })} />
              </label>
              <label>
                重叠大小
                <input className="input" type="number" min="0" max="512" value={editor.chunkOverlap}
                  onChange={(event) => setEditor({ ...editor, chunkOverlap: event.target.value })} />
              </label>
              <label>
                召回条数
                <input className="input" type="number" min="1" max="20" value={editor.retrievalTopK}
                  onChange={(event) => setEditor({ ...editor, retrievalTopK: event.target.value })} />
              </label>
            </div>
            <div className="kb-modal-actions">
              <button type="button" className="btn" disabled={saving} onClick={() => setEditor(null)}>取消</button>
              <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? '保存中…' : '保存'}</button>
            </div>
          </form>
        </div>
      )}

      {grantDoc && (
        <DocumentGrantModal
          doc={grantDoc}
          onClose={() => setGrantDoc(null)}
          onSaved={() => {
            setGrantDoc(null);
            setNotice('文档权限已更新');
            setTimeout(() => setNotice(''), 2600);
          }}
        />
      )}

      <style>{`
        .kb-page { height: 100%; overflow-y: auto; padding: clamp(16px, 3vw, 30px); background: var(--bg); }
        .kb-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
        .kb-head h1 { margin: 0 0 6px; font-size: 22px; }
        .kb-head p { margin: 0; color: var(--muted); font-size: 13px; }
        .kb-banner { margin-bottom: 14px; padding: 10px 14px; border-radius: 12px; font-size: 13px; }
        .kb-banner-error { background: #fdecec; color: #b91c1c; border: 1px solid rgba(220,38,38,.25); }
        .kb-banner-ok { background: #e9f8ef; color: #15803d; border: 1px solid rgba(22,163,74,.25); }
        .kb-grid { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 16px; align-items: start; }
        .kb-card { background: #fff; border: 1px solid var(--border); border-radius: 16px; box-shadow: 0 8px 24px rgba(26,34,51,.05); }
        .kb-list { padding: 10px; max-height: calc(100vh - 190px); overflow-y: auto; }
        .kb-card-head { display: flex; justify-content: space-between; padding: 8px 10px 12px; font-size: 13px; font-weight: 700; color: #39445c; }
        .kb-item { display: block; width: 100%; text-align: left; border: 1px solid transparent; background: #f8faff; border-radius: 12px; padding: 11px 12px; margin-bottom: 8px; cursor: pointer; transition: all .18s; }
        .kb-item:hover { border-color: rgba(61,123,255,.35); }
        .kb-item.active { background: rgba(61,123,255,.08); border-color: rgba(61,123,255,.55); }
        .kb-item-top { display: flex; justify-content: space-between; gap: 8px; align-items: center; font-size: 14px; }
        .kb-item-meta { display: flex; justify-content: space-between; gap: 8px; margin-top: 7px; color: #6b768c; font-size: 12px; }
        .kb-detail { padding: 18px; min-height: 420px; }
        .kb-detail-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
        .kb-detail-head h2 { margin: 0 0 5px; font-size: 18px; }
        .kb-detail-head p { margin: 0; color: var(--muted); font-size: 13px; }
        .kb-actions { display: flex; gap: 8px; flex-wrap: wrap; }
        .kb-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 16px 0; }
        .kb-stats div { background: #f8faff; border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; display: flex; flex-direction: column; gap: 5px; }
        .kb-stats span { color: var(--muted); font-size: 12px; }
        .kb-stats strong { font-size: 13px; color: #2c3852; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .kb-drop { border: 2px dashed rgba(61,123,255,.4); border-radius: 14px; padding: 20px; text-align: center; cursor: pointer; background: #f8faff; display: flex; flex-direction: column; gap: 6px; transition: all .2s; }
        .kb-drop:hover, .kb-drop.over { border-color: var(--brand); background: #eef5ff; }
        .kb-drop strong { font-size: 14px; color: #2c3852; }
        .kb-drop span { color: var(--muted); font-size: 12px; }
        .kb-uploads { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin-top: 12px; }
        .kb-upload { border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; background: #fff; }
        .kb-upload-top { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
        .kb-upload-name { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .kb-progress { height: 6px; border-radius: 999px; background: #edf0f7; overflow: hidden; margin: 8px 0 6px; }
        .kb-progress i { display: block; height: 100%; background: linear-gradient(90deg, var(--brand), var(--brand2)); transition: width .25s; }
        .kb-upload-msg { color: var(--muted); font-size: 12px; }
        .kb-toolbar { display: flex; gap: 8px; align-items: center; margin: 16px 0 10px; }
        .kb-toolbar .input { max-width: 260px; }
        .kb-table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 12px; }
        .kb-table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .kb-table th { text-align: left; padding: 10px 12px; background: #f8faff; color: #59647a; font-weight: 600; white-space: nowrap; }
        .kb-table td { padding: 10px 12px; border-top: 1px solid var(--border); color: #39445c; }
        .kb-name { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .kb-empty-cell { text-align: center; color: var(--muted); padding: 28px; }
        .kb-chip { display: inline-flex; align-items: center; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; white-space: nowrap; background: #eef1f7; color: #59647a; }
        .kb-chip.st-running, .kb-chip.idx-building, .kb-chip.idx-pending, .kb-chip.phase-uploading, .kb-chip.phase-hashing { background: #e9f0ff; color: #2563eb; }
        .kb-chip.st-ok, .kb-chip.idx-ready, .kb-chip.idx-indexed, .kb-chip.phase-done { background: #e5f8ee; color: #16a34a; }
        .kb-chip.st-bad, .kb-chip.idx-failed, .kb-chip.phase-failed { background: #fdecec; color: #dc2626; }
        .kb-inline-progress { display: flex; align-items: center; gap: 8px; min-width: 110px; }
        .kb-inline-progress div, .kb-inline-progress i { }
        .kb-inline-progress { position: relative; }
        .kb-inline-progress:before { content: ''; position: absolute; left: 0; right: 36px; height: 6px; background: #edf0f7; border-radius: 999px; }
        .kb-inline-progress i { position: relative; z-index: 1; height: 6px; max-width: calc(100% - 36px); background: var(--brand); border-radius: 999px; }
        .kb-inline-progress span { margin-left: auto; font-size: 12px; color: var(--muted); }
        .kb-pager { display: flex; align-items: center; gap: 12px; justify-content: flex-end; margin-top: 12px; color: var(--muted); font-size: 13px; }
        .kb-link { border: none; background: transparent; cursor: pointer; color: var(--brand); font-weight: 600; padding: 0; }
        .kb-danger { color: #dc2626; }
        .btn.kb-danger { color: #dc2626; border-color: rgba(220,38,38,.35); }
        .kb-muted { color: var(--muted); font-size: 12px; }
        .kb-empty { color: var(--muted); font-size: 13px; padding: 26px 10px; text-align: center; }
        .kb-mask { position: fixed; inset: 0; background: rgba(18,24,38,.42); display: flex; align-items: center; justify-content: center; z-index: 50; padding: 16px; }
        .kb-modal { width: min(560px, 96vw); background: #fff; border-radius: 18px; padding: 22px; box-shadow: 0 24px 60px rgba(18,24,38,.3); display: flex; flex-direction: column; gap: 12px; }
        .kb-modal h3 { margin: 0 0 4px; font-size: 17px; }
        .kb-modal label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; font-weight: 600; color: #59647a; }
        .kb-form-row { display: flex; gap: 10px; flex-wrap: wrap; }
        .kb-form-row label { flex: 1; min-width: 120px; }
        .kb-modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 6px; }
        @media (max-width: 980px) {
          .kb-grid { grid-template-columns: 1fr; }
          .kb-list { max-height: none; }
          .kb-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
      `}</style>
    </div>
  );
}

/** 文档级 ACL：按用户/角色授予 READ 或 WRITE；留空表示沿用知识库级授权。 */
function DocumentGrantModal({ doc, onClose, onSaved }) {
  const [rows, setRows] = useState([]);
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listDocumentGrants(doc.id),
      listUsers({ page: 1, pageSize: 100 }),
      listRoles({ page: 1, pageSize: 100 }),
    ])
      .then(([grantData, userData, roleData]) => {
        if (cancelled) return;
        setRows((grantData.items || []).map((item) => ({
          subjectType: item.subjectType,
          subjectId: item.subjectId,
          permission: item.permission,
        })));
        setUsers(userData.items || []);
        setRoles(roleData.items || []);
      })
      .catch((err) => {
        if (!cancelled) setError(describe(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [doc.id]);

  const update = (index, patch) =>
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const addRow = () => {
    const firstUser = users[0];
    setRows((prev) => [...prev, {
      subjectType: 'USER',
      subjectId: firstUser ? firstUser.id : '',
      permission: 'READ',
    }]);
  };

  const save = async (event) => {
    event.preventDefault();
    if (saving) return;
    const invalid = rows.find((row) => !row.subjectId);
    if (invalid) {
      setError('请为每条授权选择用户或角色');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await replaceDocumentGrants(doc.id, rows.map((row) => ({
        subjectType: row.subjectType,
        subjectId: row.subjectId,
        permission: row.permission,
      })));
      onSaved();
    } catch (err) {
      setError(describe(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="kb-mask" onClick={() => !saving && onClose()}>
      <form className="kb-modal" onClick={(event) => event.stopPropagation()} onSubmit={save}>
        <h3>文档权限 · {doc.fileName}</h3>
        <div className="kb-muted">
          未配置任何授权时，沿用知识库级授权；一旦配置，仅列表中的用户/角色（及文档上传者、平台管理员）可访问。
        </div>

        {rows.length === 0 && !loading && (
          <div className="kb-empty">当前未配置文档级授权</div>
        )}

        {rows.map((row, index) => {
          const options = row.subjectType === 'USER' ? users : roles;
          return (
            <div key={index} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <select
                className="input"
                value={row.subjectType}
                onChange={(event) => {
                  const type = event.target.value;
                  const list = type === 'USER' ? users : roles;
                  update(index, { subjectType: type, subjectId: list[0] ? list[0].id : '' });
                }}
              >
                <option value="USER">用户</option>
                <option value="ROLE">角色</option>
              </select>
              <select
                className="input"
                value={row.subjectId}
                onChange={(event) => update(index, { subjectId: event.target.value })}
              >
                <option value="">请选择</option>
                {options.map((item) => (
                  <option key={item.id} value={item.id}>
                    {row.subjectType === 'USER'
                      ? `${item.displayName || item.username}（${item.username}）`
                      : `${item.name}（${item.code}）`}
                  </option>
                ))}
              </select>
              <select
                className="input"
                value={row.permission}
                onChange={(event) => update(index, { permission: event.target.value })}
              >
                <option value="READ">只读</option>
                <option value="WRITE">读写</option>
              </select>
              <button type="button" className="btn kb-danger" onClick={() => setRows((prev) => prev.filter((_, i) => i !== index))}>
                移除
              </button>
            </div>
          );
        })}

        {loading && <div className="kb-muted">加载中…</div>}
        {error && <div className="kb-banner kb-banner-error" style={{ marginBottom: 0 }}>{error}</div>}

        <div className="kb-modal-actions">
          <button type="button" className="btn" onClick={addRow} disabled={saving}>添加授权</button>
          <span style={{ flex: 1 }} />
          <button type="button" className="btn" onClick={onClose} disabled={saving}>取消</button>
          <button type="submit" className="btn btn-primary" disabled={saving || loading}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </div>
  );
}
