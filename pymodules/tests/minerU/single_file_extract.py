import requests

from app.conf.mineru_config import mineru_config
token = mineru_config.api_key

task_id = "a85e9a92-f512-4b65-bc22-3384589ebfe2"
url = f"https://mineru.net/api/v4/extract/task/{task_id}"
header = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}"
}

res = requests.get(url, headers=header)
print(res.status_code)
print(res.json())
print(res.json()["data"])

# 200
# {'code': 0, 'msg': 'ok', 'trace_id': 'cba1f12d09c9f411ae9c8b6e7c8a7015', 'data': {'task_id': 'a85e9a92-f512-4b65-bc22-3384589ebfe2', 'state': 'done', 'err_msg': '', 'full_zip_url': 'https://cdn-mineru.openxlab.org.cn/pdf/2026-07-29/d13cdad7-a945-4fcb-94a7-8547853ec4cd.zip'}}
# {'task_id': 'a85e9a92-f512-4b65-bc22-3384589ebfe2', 'state': 'done', 'err_msg': '', 'full_zip_url': 'https://cdn-mineru.openxlab.org.cn/pdf/2026-07-29/d13cdad7-a945-4fcb-94a7-8547853ec4cd.zip'}
