from pathlib import Path

from app.core.logger import logger
from app.core.logger import node_log
from tests.langgraph.state import get_default_state

@node_log("node_entry")
def node_entry(state):
    local_file_path = state['local_file_path']
    if not local_file_path:
        logger.error("No local file path provided")

    if local_file_path.endswith(".pdf"):
        state['is_pdf_read_enable'] = True
    elif local_file_path.endswith(".md"):
        state['is_md_read_enable'] = True
    else:
        logger.waring(f"不支持的文件类型：{local_file_path}")

    file = Path(local_file_path)
    state['file_title'] = file.stem
    return state


if __name__ == '__main__':

    test_state2 = get_default_state(
        task_id="test_task_002",
        local_file_path="小米用户手册.md"
    )
    state = node_entry(test_state2)
    print(state)