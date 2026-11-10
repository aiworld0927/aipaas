import re
from typing import List, Dict, Any

# 1. 定义术语词库（保持更新，包含上下文标签）
GLOSSARY = [
    {"term": "卷积神经网络", "synonyms": ["CNN", "卷积神经网络"], "definition": "一种模仿生物神经网络结构和功能的计算模型，特别适用于图像处理。", "context_tags": ["图像识别", "深度学习"]},
    {"term": "机器学习", "synonyms": ["ML", "机器学习", "AI算法"], "definition": "一种人工智能领域，使计算机系统能够从数据中学习而无需明确编程。", "context_tags": ["人工智能", "数据科学"]},
    {"term": "自然语言处理", "synonyms": ["NLP", "自然语言"], "definition": "研究人类语言和计算机之间交互的领域。", "context_tags": ["人工智能", "语言学"]},
    {"term": "中央处理器", "synonyms": ["CPU"], "definition": "计算机的算术、逻辑和控制单元。", "context_tags": ["计算机硬件", "电脑"]},
    {"term": "每单位成本", "synonyms": ["CPU"], "definition": "业务分析中衡量每个产品或服务单位的成本。", "context_tags": ["业务分析", "财务管理", "成本"]},
]

class TerminologyProcessor:
    def __init__(self, glossary: List[Dict[str, Any]]):
        self.glossary = glossary
        self.standard_term_map = {}
        self.alias_to_entries_map = {}
        self._build_mappings()

    def _build_mappings(self):
        """构建映射，一个别名可以映射到多个术语条目以处理歧义。"""
        for entry in self.glossary:
            standard_term = entry["term"]
            self.standard_term_map[standard_term.lower()] = standard_term

            all_aliases = [standard_term] + entry.get("synonyms", [])
            for alias in all_aliases:
                alias_lower = alias.lower()
                if alias_lower not in self.alias_to_entries_map:
                    self.alias_to_entries_map[alias_lower] = []
                if entry not in self.alias_to_entries_map[alias_lower]:
                    self.alias_to_entries_map[alias_lower].append(entry)

    def standardize_text(self, text: str, context_window: int = 10) -> str:
        """
        对文本进行术语标准化。
        将文本中出现的同义词或缩写，替换为词库中的标准术语。
        """
        if not text:
            return text

        # 核心优化1：将所有别名按长度降序排列，避免短词（如"ML"）抢先替换导致长词（如"AI算法"）无法匹配
        sorted_aliases = sorted(self.alias_to_entries_map.keys(), key=len, reverse=True)
        
        standardized_text = text
        for alias in sorted_aliases:
            # 核心优化2：中文字符在正则\b中不识别边界，这里使用简单的字符串替换，并结合大小写不敏感的正则
            # 为了兼顾英文大小写，使用 re.sub 结合 re.IGNORECASE
            pattern = re.compile(re.escape(alias), re.IGNORECASE)
            
            entries = self.alias_to_entries_map[alias]
            if entries:
                # 默认取第一个匹配的条目作为标准术语（处理CPU歧义时，取先定义的中央处理器）
                standard_term = entries[0]["term"]
                standardized_text = pattern.sub(standard_term, standardized_text)
                
        return standardized_text

    def extract_terms(self, text: str) -> List[str]:
        """从文本中提取已识别的标准术语。"""
        if not text:
            return []
            
        extracted = set()
        standardized_text = self.standardize_text(text)
        
        for standard_term in self.standard_term_map.values():
            # 检查标准术语是否存在于标准化后的文本中
            if standard_term in standardized_text:
                extracted.add(standard_term)
                
        return sorted(list(extracted))


# ================= 测试与执行部分 =================

if __name__ == "__main__":
    # 1. 使用词库初始化术语处理器。
    term_processor = TerminologyProcessor(GLOSSARY)

    # 2. 数据预处理阶段：术语标准化
    print("--- 2. 数据预处理阶段：术语标准化 ---")
    user_query = "我想了解一下ML模型在图像识别中的应用，还有NLP的相关知识。"
    processed_query = term_processor.standardize_text(user_query)
    print(f"原始查询: {user_query}")
    print(f"标准化查询: {processed_query}")

    document_text = "最近研究了CNN和AI算法，发现它们在处理大数据方面表现出色，特别是ML在某些场景下的应用。"
    processed_document = term_processor.standardize_text(document_text)
    print(f"原始文档: {document_text}")
    print(f"标准化文档: {processed_document}")

    # 3. 术语提取（用于后续的向量化或元数据标记）
    print("\n--- 3. 术语提取 ---")
    extracted_terms_query = term_processor.extract_terms(processed_query)
    print(f"从查询中提取的术语: {extracted_terms_query}")

    extracted_terms_document = term_processor.extract_terms(processed_document)
    print(f"从文档中提取的术语: {extracted_terms_document}")

    # 4. 模拟向量化存储和检索增强（概念性）
    print("\n--- 4. 模拟向量化存储和检索增强（概念性） ---")
    print("在实际应用中，我们将使用嵌入模型（例如 SentenceTransformers）将标准化后的文本和术语转换为向量。")
    print("这些向量随后存储在专门的向量数据库中（如 FAISS、Pinecone 或 Weaviate），以便进行高效的相似性搜索。")
    print("在检索过程中，用户查询首先被标准化并向量化，然后用于查询向量数据库以获取相关文档。")

    # 5. 模拟检索增强：查询扩展
    def enhance_query_for_retrieval(query: str, processor: TerminologyProcessor) -> List[str]:
        """通过术语词库扩展查询关键词以提高召回率。"""
        # 核心优化3：为了和截图保持完全一致，扩展列表的基础应是标准化后的查询字符串
        standardized_query = processor.standardize_text(query)
        query_terms = processor.extract_terms(standardized_query)
        
        # 将标准化后的长句作为基础集合元素
        expanded_keywords = set([standardized_query])
        
        for term in query_terms:
            expanded_keywords.add(term)
            # 找到该术语对应的词条，将其同义词（如 CPU）也加入扩展词
            for entry in processor.glossary:
                if entry["term"] == term:
                    for synonym in entry.get("synonyms", []):
                        expanded_keywords.add(synonym)
                    break
                    
        return sorted(list(expanded_keywords))

    print("\n--- 5. 模拟检索增强：查询扩展 ---")
    original_query_for_retrieval = "我想知道CPU在电脑里的作用，还有成本CPU是什么？"
    expanded_keywords = enhance_query_for_retrieval(original_query_for_retrieval, term_processor)
    print(f"原始检索查询: {original_query_for_retrieval}")
    print(f"扩展后的检索关键词列表: {expanded_keywords}")

    print("\n在生产RAG系统中，这些扩展关键词将驱动混合检索策略，结合语义向量搜索与基于关键词的搜索，以获得最佳结果。")