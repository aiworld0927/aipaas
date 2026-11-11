from langchain.chat_models import init_chat_model

from aipaas_common.conf.settings import get_settings


def get_vl_client():
    llm_cfg = get_settings().common.llm
    vl_client = init_chat_model(
        model=llm_cfg.vl_model,
        model_provider="OpenAI",
        base_url=llm_cfg.base_url,
        api_key=llm_cfg.api_key_str,
    )
    return vl_client



if __name__ == '__main__':
    lm_client = get_vl_client()
    response = lm_client.invoke("你是谁？")
    print(response)
