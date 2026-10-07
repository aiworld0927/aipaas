import requests
from app.conf.mineru_config import mineru_config
token = mineru_config.api_key
batch_id = "0c79369b-ca45-4dc5-b10f-e6989b348401"
url = f"https://mineru.net/api/v4/extract-results/batch/{batch_id}"
header = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}"
}

res = requests.get(url, headers=header)
print(res.status_code)
print(res.json())
print(res.json()["data"])