/** 个人偏好：当前仅输出模式，落 localStorage，问答页与个人中心共用。 */
const STREAM_KEY = 'aipaas.prefs.stream';

export function getStreamPref() {
  try {
    const value = localStorage.getItem(STREAM_KEY);
    return value === null ? true : value === '1';
  } catch {
    return true;
  }
}

export function setStreamPref(value) {
  try {
    localStorage.setItem(STREAM_KEY, value ? '1' : '0');
  } catch {
    /* ignore quota/private mode */
  }
}
