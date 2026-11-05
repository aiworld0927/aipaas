from langchain.chat_models import init_chat_model

from app.conf.llm_config import lm_config


def get_vl_client():
    vl_client = init_chat_model(
        model=lm_config.vl_model,
        model_provider="OpenAI",
        base_url =lm_config.base_url,
        api_key=lm_config.api_key,
    )
    return vl_client



if __name__ == '__main__':
    lm_client = get_vl_client()
    response = lm_client.invoke("你是谁？")
    print(response)
