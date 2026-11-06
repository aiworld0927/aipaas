/**
 * 答案/图片解析工具（逻辑与旧版 chat.html 完全一致）
 */

export function isImageUrl(url) {
  try {
    const u = new URL(url);
    return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(u.pathname);
  } catch (e) {
    return /\.(png|jpe?g|gif|webp|bmp|svg)(\?|#|$)/i.test(url || '');
  }
}

export function normalizeUrl(rawUrl) {
  const s = String(rawUrl || '').trim();
  if (!s) return '';
  return s.replace(/\s/g, '%20');
}

export function dedupeKeepOrder(arr) {
  const seen = new Set();
  const out = [];
  for (const x of Array.isArray(arr) ? arr : []) {
    const v = String(x || '');
    if (!v) continue;
    if (seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

export function extractUrlsLoose(text) {
  const s = String(text || '');
  const regex = /(https?:\/\/[^\s]+)/g;
  const matches = s.match(regex) || [];
  const urls = [];
  const trimTailPunct = (u) => String(u || '').replace(/[)\]}'">，。,;；\]】）＞]+$/g, '');
  const trimHeadPunct = (u) => String(u || '').replace(/^[<([{'"]+|^[＜（【\[]+/g, '');
  for (const m of matches) {
    const u = trimHeadPunct(trimTailPunct(m));
    if (u) urls.push(u);
  }
  return dedupeKeepOrder(urls);
}

function findLastImageMarkerIndex(raw) {
  const s = String(raw || '');
  const re = /【\s*图片\s*】|\[\s*图片\s*\]/g;
  let m;
  let lastIdx = -1;
  let lastLen = 0;
  while ((m = re.exec(s)) !== null) {
    lastIdx = m.index;
    lastLen = m[0].length;
  }
  return { idx: lastIdx, len: lastLen };
}

export function parseAnswerAndImages(text) {
  const raw = String(text || '');
  const { idx, len } = findLastImageMarkerIndex(raw);
  if (idx === -1) return { text: raw, images: [] };

  const before = raw.slice(0, idx).trimEnd();
  const after = raw.slice(idx + len).trim();
  const urls = [];

  const lines = after.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  for (const line of lines) {
    if (line.startsWith('http://') || line.startsWith('https://')) {
      urls.push(line);
    } else {
      for (const u of extractUrlsLoose(line)) urls.push(u);
    }
  }

  const seen = new Set();
  const images = [];
  for (const u of urls) {
    const normalized = normalizeUrl(u);
    if (!isImageUrl(normalized)) continue;
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    images.push(normalized);
  }
  return { text: before, images };
}

/** 解析出最终要渲染的 { text, images }（Block 标记 > 后端候选 > 文本宽松提取，取并集） */
export function parseRenderable(answerText, candidateImageUrls) {
  const { text, images: imagesFromBlock } = parseAnswerAndImages(answerText);
  const candidates = Array.isArray(candidateImageUrls)
    ? candidateImageUrls.map(normalizeUrl).filter(isImageUrl)
    : [];
  const looseImages = extractUrlsLoose(answerText).map(normalizeUrl).filter(isImageUrl);

  const allImages = Array.from(new Set([...imagesFromBlock, ...candidates, ...looseImages]));
  return {
    text: (text || '').trim().length > 0 ? text : '（已完成，但未返回答案）',
    images: allImages,
  };
}
