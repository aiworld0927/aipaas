from conf.lm_config import lm_config
from lm.lm_utils import get_llm_client

if __name__ == '__main__':
    chat_model = get_llm_client(lm_config.vl_model)
    # chat_model.invoke()