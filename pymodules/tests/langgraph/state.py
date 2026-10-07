import copy
from typing import TypedDict


class BuildKnowledgeGraph(TypedDict):
    # 1、node_entry
    # 入参：
    task_id: str
    local_file_path: str
    # 出参：
    is_pdf_read_enable: bool
    is_md_read_enable: bool
    md_path: str
    pdf_path: str
    file_title: str

    # 2、conditional_edge
    # is_pdf_read_enable: bool
    # is_md_read_enable: bool

    # 3、node_pdf_to_md
    # 入参：
    # task_id: str
    # pdf_path: str
    local_dir: str # 输出文件路径
    # 出参：
    # md_path: str
    md_content: str
    local_dir: str  # 输出文件路径

    # 4、node_md_img
    # 入参：
    # task_id: str
    # md_path: str
    # md_content: str
    # 出参：
    # md_path: str
    # md_content: str

    # 5、node_document_split
    # 入参：
    # task_id: str
    # md_path: str
    md_content:str
    # file_title
    # 出参：
    chunks:list

    # 6、node_item_name_recognition
    # 入参：
    # task_id: str
    # chunks:list
    # file_title
    # md_path: str
    md_content:str
    # 出参：
    item_name:str
    # chunks:List[str]

    # 6、node_bge_embedding
    # 入参：
    # task_id: str
    # chunks: list
    # 出参：
    # chunks:list

    # 7、node_import_milvus
    # 入参：
    # task_id: str
    # chunks:list
    # 出参：

build_default_knowledge_state:BuildKnowledgeGraph = {
    "task_id": "",
    "is_pdf_read_enable": False,
    "is_md_read_enable": False,
    "local_file_path": "",
    "local_dir": "",
    "md_path": "",
    "pdf_path": "",
    "file_title": "",
    "md_content": "",
    "chunks": [],
    "item_name":"",
}

def get_default_state(**overwrite):
    default_knowledge_state = copy.deepcopy(build_default_knowledge_state)
    # print(default_knowledge_state)
    default_knowledge_state.update(overwrite)
    # print(my_knowledge_state)

    return default_knowledge_state


if __name__ == '__main__':
    print(get_default_state(file_title="hello",file_title1="hello2",))

