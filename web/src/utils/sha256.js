/** 浏览器端 SHA-256：P0 文件上限 100MB，一次性摘要；失败时回退为不可用提示。 */
export async function sha256File(file) {
  if (!globalThis.crypto || !crypto.subtle) {
    throw new Error('当前环境不支持文件摘要，请通过 HTTPS 访问');
  }
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}
