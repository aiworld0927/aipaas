import requests

# API管理页面自定创建的token
from app.conf.mineru_config import mineru_config
token = mineru_config.api_key

url = "https://mineru.net/api/v4/extract/task"
header = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}"
}
data = {
    "url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "model_version": "vlm"
}

res = requests.post(url,headers=header,json=data)
print(res.status_code)
print(res.json())
print(res.json()["data"])

# 200
# {'code': 0, 'msg': 'ok', 'trace_id': 'c89139c00255c4a554c656008e7d23b5', 'data': {'task_id': 'a85e9a92-f512-4b65-bc22-3384589ebfe2'}}
# {'task_id': 'a85e9a92-f512-4b65-bc22-3384589ebfe2'}