/**
 * 文件导入相关 API（对应后端 8000 导入服务）
 * 逻辑与旧版 import.html 保持一致：POST /upload + 轮询 GET /status/{task_id}
 */
import { IMPORT_API_BASE } from './config.js';

/**
 * 上传文件（XHR 以支持真实上传进度）
 * @param {File} file
 * @param {(percent:number)=>void} onProgress
 * @returns {Promise<{task_ids:string[]}>}
 */
export function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append('files', file);

    xhr.open('POST', `${IMPORT_API_BASE}/upload`);
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (err) {
          reject(new Error('响应解析失败'));
        }
      } else {
        reject(new Error('Upload failed'));
      }
    });
    xhr.addEventListener('error', () => reject(new Error('网络错误，上传失败')));
    xhr.send(formData);
  });
}

/**
 * 查询单个任务处理进度
 * @returns {Promise<{task_id:string,status:string,done_list:string[],running_list:string[]}>}
 */
export async function fetchTaskStatus(taskId) {
  const res = await fetch(`${IMPORT_API_BASE}/status/${taskId}`);
  return res.json();
}
