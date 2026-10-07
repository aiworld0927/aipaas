import requests

# API管理页面自定创建的token"
from app.conf.mineru_config import mineru_config
token = mineru_config.api_key

url = "https://mineru.net/api/v4/file-urls/batch"
header = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}"
}
data = {
    "files": [
        {"name":"AttentionIsAllYouNeed", "data_id": "abcd"}
    ],
    "model_version":"vlm"
}
file_path = ["D:/coding/local/rag20260828/doc/pdf/AttentionIsAllYouNeed.pdf"]
try:
    response = requests.post(url,headers=header,json=data)
    if response.status_code == 200:
        result = response.json()
        print('response success. result:{}'.format(result))
        if result["code"] == 0:
            batch_id = result["data"]["batch_id"]
            urls = result["data"]["file_urls"]
            print('batch_id:{},urls:{}'.format(batch_id, urls))
            for i in range(0, len(urls)):
                with open(file_path[i], 'rb') as f:
                    res_upload = requests.put(urls[i], data=f)
                    if res_upload.status_code == 200:
                        print(f"{urls[i]} upload success")
                    else:
                        print(f"{urls[i]} upload failed")
        else:
            print('apply upload url failed,reason:{}'.format(result["msg"]))
    else:
        print('response not success. status:{} ,result:{}'.format(response.status_code, response))
except Exception as err:
    print(err)

# response success. result:{'code': 0, 'msg': 'ok', 'trace_id': 'f1bf3254fcbcb10ddfb9503fb3fbdfe0', 'data': {'batch_id': '0c79369b-ca45-4dc5-b10f-e6989b348401', 'file_urls': ['https://mineru.oss-cn-shanghai.aliyuncs.com/api-upload/extract/2026-08-30/0c79369b-ca45-4dc5-b10f-e6989b348401/95fe3480-adb4-4b92-bf1c-a0c2f5939b6d?Expires=1788155631&OSSAccessKeyId=LTAI5t8fSGMgiRhQn4mpp926&Signature=jZQ2qE94N%2FHaaFmMIDF5kDPJ6b8%3D']}}
# batch_id:0c79369b-ca45-4dc5-b10f-e6989b348401,urls:['https://mineru.oss-cn-shanghai.aliyuncs.com/api-upload/extract/2026-08-30/0c79369b-ca45-4dc5-b10f-e6989b348401/95fe3480-adb4-4b92-bf1c-a0c2f5939b6d?Expires=1788155631&OSSAccessKeyId=LTAI5t8fSGMgiRhQn4mpp926&Signature=jZQ2qE94N%2FHaaFmMIDF5kDPJ6b8%3D']
# https://mineru.oss-cn-shanghai.aliyuncs.com/api-upload/extract/2026-08-30/0c79369b-ca45-4dc5-b10f-e6989b348401/95fe3480-adb4-4b92-bf1c-a0c2f5939b6d?Expires=1788155631&OSSAccessKeyId=LTAI5t8fSGMgiRhQn4mpp926&Signature=jZQ2qE94N%2FHaaFmMIDF5kDPJ6b8%3D upload success
