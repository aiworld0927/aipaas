from modelscope.hub.snapshot_download import snapshot_download

# 下载模型到当前目录下的 models/bge-m3 文件夹
model_dir = snapshot_download(
    model_id='BAAI/bge-m3',
    local_dir='D:/aigc/modelscope_cache/models/BAAI/bge-m3'
)
print(f"模型已下载到: {model_dir}")