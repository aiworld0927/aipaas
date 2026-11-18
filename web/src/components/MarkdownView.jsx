import { Fragment } from 'react';

/**
 * 轻量 Markdown 渲染（#110 输出的安全展示）：
 * 支持标题、段落、有序/无序列表、引用、表格、代码块与行内加粗/代码。
 * 引用角标 ``[n]`` 渲染为可点击按钮，交由父组件定位引用卡片。
 * 不引入第三方依赖，不使用 dangerouslySetInnerHTML。
 */
export default function MarkdownView({ text = '', onCitationClick }) {
  const blocks = parseBlocks(text);
  return (
    <div className="md-body">
      {blocks.map((block, index) => (
        <Block key={index} block={block} onCitationClick={onCitationClick} />
      ))}
      <style>{`
        .md-body { font-size: 15px; line-height: 1.75; overflow-wrap: break-word; }
        .md-body > * + * { margin-top: 10px; }
        .md-body p { margin: 0; }
        .md-body h1, .md-body h2, .md-body h3 { margin: 16px 0 6px; line-height: 1.4; }
        .md-body h1 { font-size: 19px; }
        .md-body h2 { font-size: 17px; }
        .md-body h3 { font-size: 15.5px; }
        .md-body ul, .md-body ol { margin: 0; padding-left: 22px; }
        .md-body li + li { margin-top: 4px; }
        .md-body blockquote {
          margin: 0; padding: 8px 14px;
          border-left: 3px solid rgba(61,123,255,.45);
          background: rgba(61,123,255,.05); border-radius: 0 10px 10px 0;
          color: #46536b;
        }
        .md-body pre {
          margin: 0; padding: 12px 14px; overflow-x: auto;
          background: #0f172a; color: #e2e8f0; border-radius: 12px;
          font-size: 13px; line-height: 1.6;
        }
        .md-body code {
          font-family: ui-monospace, Menlo, Consolas, monospace;
          font-size: .92em; background: rgba(15,23,42,.06);
          padding: 1px 5px; border-radius: 5px;
        }
        .md-body pre code { background: transparent; padding: 0; color: inherit; }
        .md-table-wrap { overflow-x: auto; }
        .md-table { border-collapse: collapse; width: 100%; font-size: 14px; }
        .md-table th, .md-table td { border: 1px solid var(--border); padding: 8px 10px; text-align: left; }
        .md-table th { background: #f7f9fe; font-weight: 600; }
        .cite-ref {
          display: inline-flex; align-items: center; justify-content: center;
          min-width: 20px; height: 20px; padding: 0 5px; margin: 0 2px;
          border: none; border-radius: 6px; cursor: pointer;
          background: rgba(61,123,255,.12); color: var(--brand);
          font-size: 11.5px; font-weight: 700; vertical-align: 1px;
          transition: background .15s;
        }
        .cite-ref:hover { background: rgba(61,123,255,.24); }
      `}</style>
    </div>
  );
}

function Block({ block, onCitationClick }) {
  switch (block.type) {
    case 'heading': {
      const Tag = `h${Math.min(block.level, 3)}`;
      return <Tag>{renderInline(block.text, onCitationClick)}</Tag>;
    }
    case 'quote':
      return <blockquote>{renderInline(block.text, onCitationClick)}</blockquote>;
    case 'code':
      return (
        <pre>
          <code>{block.text}</code>
        </pre>
      );
    case 'hr':
      return <hr style={{ border: 'none', borderTop: '1px solid var(--border)' }} />;
    case 'list': {
      const Tag = block.ordered ? 'ol' : 'ul';
      return (
        <Tag start={block.ordered ? block.start : undefined}>
          {block.items.map((item, index) => (
            <li key={index}>{renderInline(item, onCitationClick)}</li>
          ))}
        </Tag>
      );
    }
    case 'table':
      return (
        <div className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {block.header.map((cell, index) => (
                  <th key={index}>{renderInline(cell, onCitationClick)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {block.header.map((_, cellIndex) => (
                    <td key={cellIndex}>{renderInline(row[cellIndex] || '', onCitationClick)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    default:
      return (
        <p>
          {block.text.split('\n').map((line, index) => (
            <Fragment key={index}>
              {index > 0 && <br />}
              {renderInline(line, onCitationClick)}
            </Fragment>
          ))}
        </p>
      );
  }
}

/** 行内语法：**加粗**、`代码`、[n] 引用角标 */
function renderInline(text, onCitationClick) {
  if (!text) return null;
  const tokens = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`|\[\d{1,2}\])/g);
  return tokens.map((token, index) => {
    if (token.startsWith('**') && token.endsWith('**') && token.length > 4) {
      return <strong key={index}>{token.slice(2, -2)}</strong>;
    }
    if (token.startsWith('`') && token.endsWith('`') && token.length > 2) {
      return <code key={index}>{token.slice(1, -1)}</code>;
    }
    const cite = /^\[(\d{1,2})\]$/.exec(token);
    if (cite) {
      return (
        <button
          key={index}
          type="button"
          className="cite-ref"
          title={`查看引用 ${cite[1]}`}
          onClick={() => onCitationClick && onCitationClick(Number(cite[1]))}
        >
          {cite[1]}
        </button>
      );
    }
    return <Fragment key={index}>{token}</Fragment>;
  });
}

function parseBlocks(source) {
  const lines = String(source).replace(/\r\n?/g, '\n').split('\n');
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (line.trim().startsWith('```')) {
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push({ type: 'code', text: code.join('\n') });
      continue;
    }

    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2].trim() });
      index += 1;
      continue;
    }

    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line) || /^\s*---+\s*$/.test(line)) {
      blocks.push({ type: 'hr' });
      index += 1;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^\s*>\s?/, ''));
        index += 1;
      }
      blocks.push({ type: 'quote', text: quote.join('\n') });
      continue;
    }

    if (isTableStart(lines, index)) {
      const header = splitRow(lines[index]);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].trim().startsWith('|')) {
        rows.push(splitRow(lines[index]));
        index += 1;
      }
      blocks.push({ type: 'table', header, rows });
      continue;
    }

    const listMatch = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (listMatch) {
      const ordered = /\d/.test(listMatch[2]);
      const items = [];
      while (index < lines.length) {
        const match = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(lines[index]);
        if (!match || /\d/.test(match[2]) !== ordered) break;
        items.push(match[3].trim());
        index += 1;
      }
      blocks.push({
        type: 'list',
        ordered,
        start: ordered ? Number.parseInt(listMatch[2], 10) : undefined,
        items,
      });
      continue;
    }

    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
      paragraph.push(lines[index]);
      index += 1;
    }
    if (paragraph.length === 0) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push({ type: 'paragraph', text: paragraph.join('\n') });
  }

  return blocks;
}

function isBlockStart(lines, index) {
  const line = lines[index];
  return (
    line.trim().startsWith('```') ||
    /^(#{1,3})\s+/.test(line) ||
    /^\s*>\s?/.test(line) ||
    /^(\s*)([-*+]|\d+[.)])\s+/.test(line) ||
    isTableStart(lines, index)
  );
}

function isTableStart(lines, index) {
  if (index + 1 >= lines.length) return false;
  const head = lines[index].trim();
  const divider = lines[index + 1].trim();
  return head.startsWith('|') && head.endsWith('|')
    && /^\|[\s:|-]+\|$/.test(divider) && divider.includes('-');
}

function splitRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
}
