from modelscope.hub.snapshot_download import snapshot_download

model_dir = snapshot_download(
    model_id="BAAI/bge-reranker-large",
    local_dir='D:/aigc/modelscope_cache/models/BAAI/bge-reranker-large'
)

print("下载完成，模型目录：", model_dir)