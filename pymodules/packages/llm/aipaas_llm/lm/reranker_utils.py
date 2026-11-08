"""
uv add dashscope
"""
import dashscope
from FlagEmbedding import FlagReranker
from aipaas_common.conf.reranker_config import reranker_config

_reranker_model = None

def get_reranker_model():

    global _reranker_model  
    if _reranker_model is None:
        _reranker_model= FlagReranker(
            model_name_or_path=reranker_config.bge_reranker_large,
            device=reranker_config.bge_reranker_device,
            use_fp16=reranker_config.bge_reranker_fp16
        )
    return _reranker_model

def compute_score(rewritten_query: str, documents: list[str]) -> list[float]:
    # 1、获取模型
    reranker = get_reranker_model()
    # 2、拼装请求
    question_paris = []
    for text in documents:
        # 进行判断 512 长度限制  注意：text长度 <= 512 -问题长度 【lm重写】
        # TODO 动态提示词，原答案 + 限制的长度
        question_paris.append([rewritten_query,text])
    # normalize=True 方便进行断崖分值设计
    score_list = reranker.compute_score(question_paris,normalize=True)
    # round(score, 4) # 四舍五入
    return score_list

def rerank_documents(rewritten_query: str, documents: list[str]) -> list[float]:
    response = dashscope.TextReRank.call(
        api_key=reranker_config.text_rerank_api_key,
        model=reranker_config.text_rerank_model,
        query=rewritten_query,
        documents=documents,
        top_n=len(documents),
        return_documents=False,
        instruct=reranker_config.text_rerank_instruct,
    )

    status_code = response.get("status_code")
    if status_code != 200:
        message = response.get("message")
        raise RuntimeError(f"DashScope rerank 调用失败: {message}")

    results = response.output.get("results", [])
    scores = [0.0] * len(documents)
    for item in results:
        index = item.get("index")
        score = item.get("relevance_score")
        scores[int(index)] = float(score)
    return scores

if __name__ == '__main__':
    rewritten_query = "烫金机"
    documents = ["烫金机","吹风机"]
    # print(compute_score(rewritten_query,documents))
    print(rerank_documents(rewritten_query,documents))