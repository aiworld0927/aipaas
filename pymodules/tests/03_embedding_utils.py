import json
from typing import List

from pymilvus.model.hybrid import BGEM3EmbeddingFunction
from app.core.logger import logger
from app.conf.embedding_config import embedding_config

_bge_m3_bf = None
def get_bge_m3_bf():
    """
    作用：单例模式，获取bge-m3实例
    入参：无
    出参：bge-m3实例
    步骤：
        1、创建全局实例
        2、检验是否存在，存在返回，不存在创建单例
        3、创建单例，返回实例
    :return:
    """
    global _bge_m3_bf
    if _bge_m3_bf is not None:
        return _bge_m3_bf

    try:
        _bge_m3_bf = BGEM3EmbeddingFunction(
            model_name=embedding_config.bge_m3_path,  # Specify the model name
            device=embedding_config.bge_device,  # Specify the device to use, e.g., 'cpu' or 'cuda:0'
            use_fp16=embedding_config.bge_fp16  # Specify whether to use fp16. Set to `False` if `device` is `cpu`.
        )
    except Exception as e:
        logger.error(f"创建嵌入对象报错：{e}")
        raise RuntimeError(f"创建嵌入对象报错：{e}")

    return _bge_m3_bf

def generate_document(texts:List[str]):
    """
    作用：文本嵌入，生成稠密 + 稀疏 向量
    入参：texts 待处理文本
    出参：稠密 + 稀疏 向量
    """
    if not isinstance(texts, list) or len(texts) == 0:
        logger.warning(f"请求参数格式不对，或不能为空：{texts}")
        raise ValueError(f"请求参数格式不对，或不能为空：{texts}")
    embedding_client = get_bge_m3_bf()
    documents = embedding_client.encode_documents(texts)
    logger.info(f"向量嵌入完成，共处理{len(documents)}条数据，下面开始稀疏向量解析")
    """
    indices:
    data
    indptr
    """
    sparse_list = []
    for i in range(len(documents)):
        sparse_indices = documents['sparse'].indices[
            documents['sparse'].indptr[i] : documents['sparse'].indptr[i + 1]
        ].tolist()
        sparse_data = documents['sparse'].data[
            documents['sparse'].indptr[i] : documents['sparse'].indptr[i + 1]
        ].tolist()
        sparse_dict = {k:v for k, v in zip(sparse_indices, sparse_data)}
        sparse_list.append(sparse_dict)
    result = {
        "dense":[dict_dense.tolist() for dict_dense in documents["dense"]],
        "sparse":sparse_list
    }
    return result

if __name__ == '__main__':
    print(json.dumps(generate_document(["你好","hello"]),indent=2,ensure_ascii=False))