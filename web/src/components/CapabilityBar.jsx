/**
 * #402 底部能力选择栏（P0 骨架）：
 * - 仅 RAG 单能力参与路由，默认能力可由 VITE_DEFAULT_CAPABILITY 配置；
 * - Skill 选项占位禁用，待 P1 开放；
 * - 与 P1 #403 的输出模式（流式/非流式）是两件事，不在此处切换。
 */
export const DEFAULT_CAPABILITIES = [
  {
    key: 'RAG',
    label: '标准问答',
    desc: '基于知识库标准条款作答并给出引用',
    enabled: true,
  },
  {
    key: 'SKILL',
    label: '技能调用',
    desc: 'P1 开放',
    enabled: false,
  },
];

export function defaultCapability() {
  try {
    const configured = import.meta.env.VITE_DEFAULT_CAPABILITY;
    if (configured && DEFAULT_CAPABILITIES.some((item) => item.key === configured && item.enabled)) {
      return configured;
    }
  } catch {
    /* ignore */
  }
  return 'RAG';
}

export default function CapabilityBar({ value, onChange, capabilities = DEFAULT_CAPABILITIES, disabled }) {
  return (
    <div className="cap-bar" role="toolbar" aria-label="对话能力选择">
      <span className="cap-bar-label">能力</span>
      {capabilities.map((item) => {
        const active = item.key === value;
        const isDisabled = disabled || !item.enabled;
        return (
          <button
            key={item.key}
            type="button"
            className={`cap-chip ${active ? 'active' : ''} ${item.enabled ? '' : 'placeholder'}`}
            aria-pressed={active}
            title={item.desc || item.label}
            disabled={isDisabled}
            onClick={() => item.enabled && onChange && onChange(item.key)}
          >
            <span className="cap-dot" />
            {item.label}
            {!item.enabled && <span className="cap-soon">P1</span>}
          </button>
        );
      })}
      <span className="cap-bar-note">P0 仅 RAG 单能力路由</span>

      <style>{`
        .cap-bar {
          max-width: 860px; margin: 0 auto 8px;
          display: flex; align-items: center; gap: 8px;
          padding: 8px 14px;
          background: rgba(255,255,255,.72);
          border: 1px solid var(--border); border-radius: 999px;
          box-shadow: 0 6px 18px rgba(26,34,51,.05);
        }
        .cap-bar-label { font-size: 12px; color: var(--muted); font-weight: 600; margin-right: 2px; }
        .cap-chip {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 6px 12px; border-radius: 999px;
          border: 1px solid transparent; background: transparent;
          color: #4a5570; font-size: 12.5px; font-weight: 600;
          cursor: pointer; transition: all .18s;
        }
        .cap-chip:hover:not(:disabled) { background: rgba(61,123,255,.08); }
        .cap-chip.active {
          background: rgba(61,123,255,.12); color: var(--brand);
          border-color: rgba(61,123,255,.32);
        }
        .cap-chip.placeholder { color: #aab3c5; cursor: not-allowed; }
        .cap-chip:disabled { opacity: .85; }
        .cap-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; opacity: .7; }
        .cap-soon {
          font-size: 10px; padding: 1px 5px; border-radius: 6px;
          background: #eef1f7; color: #97a2b8;
        }
        .cap-bar-note { margin-left: auto; font-size: 11px; color: var(--muted); }
        @media (max-width: 600px) {
          .cap-bar { overflow-x: auto; }
          .cap-bar-note { display: none; }
        }
      `}</style>
    </div>
  );
}
