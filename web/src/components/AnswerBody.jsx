import { parseRenderable } from '../utils/answerParser.js';

/** 答案正文：文本 + 参考图片（逻辑与旧版 renderAnswerWithImages 一致） */
export default function AnswerBody({ answerText, imageUrls }) {
  const { text, images } = parseRenderable(answerText, imageUrls);

  return (
    <div className="answer">
      <div className="answer-text">{text}</div>
      {images.length > 0 && (
        <div className="answer-images">
          {images.map((url) => (
            <div key={url} className="answer-img-item">
              <img src={url} alt="参考图片" loading="lazy" referrerPolicy="no-referrer"
                onError={(e) => { e.target.style.display = 'none'; }} />
              <a href={url} target="_blank" rel="noopener noreferrer">{url}</a>
            </div>
          ))}
        </div>
      )}
      <style>{`
        .answer-text { white-space: pre-wrap; overflow-wrap: break-word; }
        .answer-images { margin-top: 12px; display: flex; flex-direction: column; gap: 12px; }
        .answer-img-item { display: flex; flex-direction: column; gap: 6px; }
        .answer-images img {
          display: block; max-width: min(100%, 600px); max-height: 600px;
          width: auto; height: auto; border-radius: 14px;
          border: 1px solid var(--border);
          box-shadow: 0 12px 28px rgba(26,34,51,.12); background: #fff;
        }
        .answer-images a {
          font-size: 12px; color: var(--brand); word-break: break-all;
        }
        .answer-images a:hover { text-decoration: underline; }
      `}</style>
    </div>
  );
}
