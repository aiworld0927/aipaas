# RAG Agent 评估与优化

## 1 评估的意义与作用

Agent（尤其是 RAG Agent）上线后的好用通常是多因素耦合：切分/索引/召回/重排/图谱/提示词/模型/工具调用等，仅靠主观体验很难定位瓶颈。评估的核心价值是把“感觉”变成“可量化、可回归、可定位”的工程闭环。

- **质量量化**：用指标把输出质量数字化（例如忠实度、相关性、检索召回/精确）。

- **回归保障**：改了切分、TopK、Rerank、Prompt 或模型后，能快速判断整体是否退化。

- **发现短板**：通过细分指标（如检索召回率 vs 生成忠实度），精准定位是检索模块的问题还是大模型生成的问题。

- **自动化监控**：建立自动化测试流，减少人工验收的成本和主观偏差。

## 2 目前主流评估 Agent 的方法

### 2.1 基于数据集的离线评估（Golden Set）

- 方式：准备 question + ground_truth，用当前系统跑出 answer + contexts，再人工对比打分。

- 优点：可复现、可回归、易对比多版本系统。

- 难点：黄金集建设成本，一般是通过人工结合真实业务总结出来的; 人工打分成本较大。

### 2.2 LLM-as-a-Judge（大模型当裁判）

- 方式：让更强的模型对“答案是否正确/是否基于上下文/是否跑题”做判分或打标签。

- 代表：RAGAS 、DeepEval、TruLens 等。

- 优点：更贴近语义质量；无需把所有问题都写成“唯一标准答案”（RAGAS 可同时评估检索与生成）。

- 风险：裁判模型本身有偏差, 生成问题集未必贴近实际业务。

## 3 什么是 RAGAS

### 3.1 概念

- **RAGAS（Retrieval-Augmented Generation Assessment）**是一套面向 **RAG（检索增强生成）系统**的评测框架/方法论，目标是在**缺少人工标注答案**或难以大规模做人工评审的情况下，用相对自动化的方式衡量 RAG 的关键质量：**检索是否找对、答案是否忠于证据、是否覆盖问题要点、是否引入幻觉**等。

### 3.2 RAGAS 评测的核心思路

RAGAS通常把一次问答拆成三要素来评估：

- **Question（问题）**

- **Contexts（检索到的上下文证据）**：通常是 top-k 文档片段

- **Answer (RAG流程生成的答案)**

- **Ground_truth（参考答案）**

然后用一组指标从“检索侧 + 生成侧 + 证据一致性”三个方向打分。

**常见指标 **

#### 3.2.1 Faithfulness（忠实性/证据一致性）

衡量答案中的陈述是否能被给定 contexts 支撑；越高表示越不容易“编”。

主要使用：

```
question + contexts + answer
```

真正的比较对象是：

```
answer ↔ contexts
```

它会先从 `answer` 中提取事实陈述，再判断每条陈述能否由 `contexts` 推导出来,它不需要 `ground_truth`，因此即使没有标准答案，也能检查模型是否产生了“上下文中不存在”的内容。
$$
\text{Faithfulness}
=
\frac{\text{被 contexts 支持的 answer 陈述数}}
{\text{answer 的总陈述数}}
$$

### 3.2.2 Answer Relevancy（回答相关性）

衡量答案是否真正回答了问题、是否跑题或废话。

使用：

```
question + answer
```

比较关系是：

```
answer ↔ question
```

它关注回答是否真正响应了问题、是否遗漏问题中的要求、是否包含过多无关内容；不判断事实是否正确，也不依赖 `contexts` 和 `ground_truth`。

例如：

```
question：法国在哪里，它的首都是哪里？
answer：法国位于西欧。
```

内容可能正确，但没有回答“首都是什么”，因此 Answer Relevancy 会下降。

### 3.2.3 Context Precision（上下文精确率）

检索到的 contexts 里，有用内容占比是否高；越高表示“拿到的证据更干净”。

标准的“有参考答案”版本使用：

```
question + contexts + ground_truth
```

它判断每个 context 是否有助于回答问题，并检查相关 context 是否排在靠前位置：

```
相关内容排在前面 → 分数高
无关内容多，或者无关内容排在前面 → 分数低
```

### 3.2.4 Context Recall（上下文召回率）

contexts 是否覆盖了回答问题所需的关键信息；越高表示“证据拿全了”。

主要使用：

```
question + contexts + ground_truth
```

真正的比较对象是：

```
ground_truth ↔ contexts
```

它会将 `ground_truth` 拆成若干事实，判断这些事实有多少能够从检索到的 `contexts` 中找到
$$
\text{Context Recall}
=
\frac{\text{被 contexts 支持的 ground\_truth 陈述数}}
{\text{ground\_truth 的总陈述数}}
$$

### 3.2.5 Answer Correctness / Similarity（正确性/相似度）

当你有参考答案（ground truth）时可用；没有参考答案时通常以 Faithfulness/Context 相关指标为主。两者主要都是：

```
answer + ground_truth
```

但含义有所区别：

- 
  - **Answer Similarity / Semantic Similarity**：只比较语义相似程度，通常基于向量相似度。
  - **Answer Correctness**：同时考虑事实正确性和语义相似性，一般比单纯 Similarity 更严格。

例如：

```
ground_truth：爱因斯坦于1879年出生在德国。
answer：爱因斯坦于1879年出生在西班牙。
```

两句话语义非常相似，所以 Similarity 可能较高；但出生地错误，所以 Answer Correctness 应该较低。

当前 `AnswerCorrectness` API 通常还接收 `user_input/question`，用于帮助理解和拆解答案，不过核心评价关系仍然是 `answer ↔ ground_truth`。

| 指标                   | question | contexts | answer | ground_truth | 核心评价内容                                          |
| ---------------------- | :------: | :------: | :----: | :----------: | ----------------------------------------------------- |
| **Faithfulness**       |   辅助   |    ✅     |   ✅    |      ❌       | `answer` 中的内容能否被 `contexts` 支持               |
| **Answer Relevancy**   |    ✅     |    ❌     |   ✅    |      ❌       | `answer` 是否切题，是否回答了 `question`              |
| **Context Precision**  |    ✅     |    ✅     |   ❌    |      ✅       | 检索到的 `contexts` 是否相关，相关内容是否排在前面    |
| **Context Recall**     |   辅助   |    ✅     |   ❌    |      ✅       | `ground_truth` 中需要的信息是否都被 `contexts` 检索到 |
| **Answer Correctness** |   辅助   |    ❌     |   ✅    |      ✅       | `answer` 与 `ground_truth` 在事实和语义上是否一致     |
| **Answer Similarity**  |    ❌     |    ❌     |   ✅    |      ✅       | `answer` 与 `ground_truth` 的语义相似程度             |

## 4 评估优化步骤

- 准备评测集：问题列表（可来自真实日志抽样/人工构造/AI合成）

- 编写RAGAS的评估程序

- 运行评估生成评估结果

- 定位问题：

  Context Precision 低：检索噪声大（需要重排序、过滤、改 embedding、改 chunk）

  Context Recall 低：检索没找全（top-k、召回策略、chunk 粒度、query 改写）

  Faithfulness 低：生成在编（提示词约束、引用证据、拒答策略、降低温度等）

- bad case分析

- 优化程序

# RAG评估实操

准备已经从pdf转为md的文本文件，交给任何一个ai生成即可。生产环境推荐由对业务较熟悉的人员手工编写，或者从常见业务问题中收集。

提示词示例

```markdown
【目标】
    帮我生成一份用于评估测试的问题集
【输入来源】
    基于eval\hak180产品安全手册_new.md来生成问题和参考答案
【输出结果】
    1 只输出5对问答
    2 csv结构
    3 编码集为utf-8 BOM编码
    4 列头位：question，ground_truth
    5 文件名为qa.csv
    6 在每个问题的前面加上“HAK 180 烫金机:”
    7 将输出文件保存在eval目录下
```

将ai生成的内容放在eval/eval_doc目录下的qa.csv文件中。

## 6 评估程序代码

评估代码生成提示词

```markdown
【目标】
    生成一个评估程序，用于对评估测试集进行评估
【输入要求】
    1 评估测试的目标为eval\qa.csv
    2 目标文件的编码集为utf-8 BOM编码
    3 目标文件列头为：question，ground_truth，分别代表测试问题和参考答案
【评估过程】
    1 评估指标：Faithfulness、Answer Relevancy、Context Prcision、Context Recall、Answer Correctness
    2 使用大模型的工具程序：knowledge\utils\llm_client_util.py
    3 使用嵌入模型的工具程序knowledge\utils\bge_m3_embedding_util.py
    4 查询流程入口为knowledge\processor\query_process\main_graph.py中的query_app.invoke()
    5 context提取，要从state中的reranked_docs获取
    6 评估过程要求使用ragas
【输出要求】
    1 返回一个csv文件，包含以下字段question、contexts、answer、ground_truth、Faithfulness、Answer 2 2 2 2 Relevancy、Context Prcision、Context Recall、Answer Correctness
    3 输出路径及文件名：eval/eval_result.csv,编码集为utf-8 BOM编码
【程序要求】
    1 使用python语言,名为eval.py，保存在eval目录下
    2 所有函数要求有注释
    3 所有核心步骤函数要求函数名前缀为 step_1_xx,step_2_xx ......
```

### 6.1 代码流程

- 初始化评估组件（LLM、Embedding、Metrics）

- 加载测试数据（qa.csv）

- 运行 RAG pipeline

- 构建评估数据集

- 执行ragas评估

- 导出结果并购提取bad case

### 6.2 主程序代码

```python
if __name__ == "__main__":

# --- step_1: 初始化评估组件 ---

# 确保设置了 OPENAI_API_KEY 环境变量，或者直接在 step_1_init_eval_components() 里传入

# os.environ["OPENAI_API_KEY"] = "sk-..."

ragas_llm, ragas_embeddings, ragas_metrics = step_1_init_eval_components()

# --- step_2: 加载测试数据 ---

ragas_csv_path = os.path.join(os.path.dirname(__file__), "eval_doc/qa.csv")

ragas_test_samples = step_2_load_test_samples_from_csv(ragas_csv_path)

# --- step_3: 运行 RAG pipeline ---

step_3_questions = [item["question"] for item in ragas_test_samples]

step_3_pipeline_results = step_3_run_pipeline(step_3_questions)

# --- step_4: 构建评估数据集 ---

ragas_dataset = step_4_build_dataset(ragas_test_samples, step_3_pipeline_results)

# --- step_5: 执行 Ragas 评估 ---

ragas_results = step_5_run_ragas_evaluation(

dataset=ragas_dataset,

metrics=ragas_metrics,

llm=ragas_llm,

embeddings=ragas_embeddings

)

# --- step_6: 导出结果并提取 bad cases ---

step_6_export_results_and_bad_cases(

results=ragas_results,

metrics=ragas_metrics,

bad_case_threshold=0.5

)
```

### 6.3 Step 1:初始化评估组件（LLM、Embedding、Metrics）

对应代码：

```python
def step_1_init_eval_components(openai_api_key: str = None):

"""

初始化评估组件（LLM、Embedding、Metrics）

"""

if openai_api_key:

os.environ["OPENAI_API_KEY"] = openai_api_key

llm = LangchainLLMWrapper(get_llm_client())

embeddings = LangchainEmbeddingsWrapper(ProjectBGEEmbeddings())

metrics = [

faithfulness,      # 忠实度：答案是否基于检索到的上下文

answer_relevancy,  # 答案相关性：答案是否回答了问题

context_precision, # 上下文精确度：检索到的相关文档在结果中的排名情况

context_recall,    # 上下文召回率：是否检索到了所有相关信息

answer_correctness,# 答案正确性：答案与参考答案（ground_truth）的匹配程度

]

return llm, embeddings, metrics
```

#### 6.3.1 相关Ragas API

**LangchainEmbeddingsWrapper**：把 LangChain Embeddings 接口包装成 Ragas 可用的 embedding 计算器。

- 你们自定义了 ProjectBGEEmbeddings，把项目里的 generate_embeddings()（BGE-M3）适配成 LangChain 的 embed_documents/embed_query。

```python
class ProjectBGEEmbeddings(Embeddings):

def embed_documents(self, texts: List[str]) -> List[List[float]]:

if not texts:

return []

return generate_embeddings(texts)["dense"]

def embed_query(self, text: str) -> List[float]:

dense = self.embed_documents([text])

return dense[0] if dense else []
```

注意：generate_embeddings() 同时产出 dense+sparse，但这里 **只把 dense 交给 Ragas。**

**为什么只需要稠密索引(dense)：**

**稀疏向量（sparse）主要是为了检索阶段的 hybrid search（Milvus 稠密+稀疏双路）**；而 Ragas 评测阶段并不负责检索，它只拿到最终的 contexts 文本来评“相关不相关/覆盖不覆盖/是否忠实”。所以在这里只把 dense 交给 Ragas，**不会让评测失真到不可用**，它评的是“给定这些 contexts 后，答案的质量/证据一致性/语义相关性”。

#### 6.3.2 Ragas 指标的工作原理（结合本文件）

本文件选了 4 个典型 RAG 指标：

- **faithfulness（忠实度）**：通常由 **LLM 参与判定**

原理：把 answer 拆成若干“声明/事实”，让 LLM 判断每个声明是否能在 contexts 中找到充分依据；汇总得到分数。依赖字段：answer + contexts（依赖 Step 3 提取的 reranked 文本质量）。

- **answer_relevancy（答案相关性）**：通常依赖 **embedding（可能辅以 LLM）**

原理：衡量 answer 是否在语义上真正回答了 question（常见做法是 embedding 相似度/问答一致性判断）。依赖字段：question + answer。

- **context_precision（上下文精确度）**：衡量“返回的 contexts 里有多少是真相关/有用的”，通常需要 **LLM/embedding**

原理：逐条判断 contexts 是否与问题相关，并结合排序位置计算精确度倾向（越靠前越重要）。依赖字段： ground_truth(参考答案) + contexts 。

- **context_recall（上下文召回率）**：衡量“contexts 是否覆盖了应有的关键事实”，通常需要 **LLM**

原理：对比 ground_truth（或 reference）中关键点，看 contexts 是否包含支撑这些关键点的信息。依赖字段：ground_truth(参考答案)  + contexts（强依赖你的测试集答案质量与完整性）。

- **Answer Correctness 就是用来衡量生成答案与参考答案（ground truth / reference）的对齐程度，范围 0\~1**

原理：用 embeddings 计算 answer 与 ground_truth 的语义相似度

### 6.4 Step 2: 从 CSV 加载测试样本（question + ground_truth）

对应代码：

```python
def step_2_load_test_samples_from_csv(csv_path: str) -> List[Dict[str, str]]:

"""

从 CSV 加载测试样本（列名要求：question, answer）

"""

test_samples: List[Dict[str, str]] = []

if not os.path.exists(csv_path):

print(f"未找到测试数据文件: {csv_path}")

return test_samples

print(f"正在从 CSV 加载测试数据: {csv_path}")

try:

df_qa = pd.read_csv(csv_path)

for _, row in df_qa.iterrows():

test_samples.append({

"question": str(row["question"]).strip(), # 根据qa.csv的表头

"ground_truth": str(row["answer"]).strip()  # 根据qa.csv的表头

})

print(f"成功加载 {len(test_samples)} 条测试用例。")

except Exception as e:

print(f"读取 CSV 失败: {e}")
```

- 输入文件默认是 kb/eval/eval_doc/qa.csv（两列：question,answer）。

- 代码里把 CSV 的 answer 列映射为 **ground_truth**，后续供 Ragas 的“需要参考答案”的指标使用（尤其是 context_recall）。

你们当前的 qa.csv 结构示例：

question,answer

"HAK180：为什么不要将HAK180烫金机放在化学品附近或可能泼溅到化学品的位置？","化学品接触设备可能引发火灾或触电；尤其有机溶剂/液体..."

### 6.5 Step 3：运行现有 RAG Pipeline，采集 answer 与 contexts

- 对应代码：

```python
def step_3_run_pipeline(questions: List[str]) -> Dict[str, List[Any]]:

"""

运行现有的 RAG 管道 (kb.query_process.main_graph)

:param questions: 问题列表

:return: 包含 answer 和 contexts 的字典

"""

answers = []

contexts = []

print(f"开始运行 Pipeline 推理，共 {len(questions)} 个问题...")

for question in questions:

# 构造输入状态

# 使用 eval_ 前缀避免污染生产环境的 session

input_state = {

"original_query": question,

"session_id": "eval_session",

"is_stream": False, # 评估时不需要流式

"history": [],      # 评估通常针对单轮问答

"item_names": []    # 根据需要可以模拟提取出的商品名

}

try:

# 调用 langgraph

result_state = query_app.invoke(input_state)

# 提取答案

ans = result_state.get("answer", "")

answers.append(strip_image_suffix_for_eval(ans))

# 提取检索到的上下文 (reranked_docs)

# 注意：Ragas 需要 contexts 是 list[str]

reranked_docs = result_state.get("reranked_docs", [])

# 提取文档内容文本

doc_texts = []

if reranked_docs:

for doc in reranked_docs:

# 假设 doc 结构中 text 字段存放内容，或者根据 main_graph 逻辑调整

text = doc.get("text", "")

if text:

doc_texts.append(text)

contexts.append(doc_texts)

except Exception as e:

print(f"Error processing question '{question}': {e}")

answers.append("Error generating response")

contexts.append([])

return {

"answer": answers,

"contexts": contexts

}
```

**调用之前的项目查询流程query_process的langgraph的主流程，提取state中rerank重排之后的文档作为context=**

### 6.6 Step 4：构建 Ragas 数据集

- 对应代码：
  
  ```python
  """
  
  构建 Ragas 数据集
  
  """
  
  questions = [item["question"] for item in test_data]
  
  #ragas 会把 v1 的 ground_truth 自动映射到 v2 的 reference
  
  #v2 schema 里 reference 是 str，因此这里必须是 list[str]（不能是 list[list[str]]）
  
  ground_truths = [item["ground_truth"] for item in test_data]
  
  data_dict = {
  
  "question": questions,
  
  "answer": pipeline_results["answer"],
  
  "contexts": pipeline_results["contexts"],
  
  "ground_truth": ground_truths
  
  }
  
  return Dataset.from_dict(data_dict)
  ```

### 6.9 Ragas 的 schema

在你们代码里，一条样本最终进入 Ragas 的字段是：

- question: 用户问题

- answer: 你们 RAG pipeline 的实际输出

- contexts: rerank 后上下文列表（list[str]）

- ground_truth: CSV 中 的参考答案

### 6.10 Step 5：调用 ragas.evaluate() 计算指标（Ragas 核心 API）

- 对应代码

```python
def step_5_run_ragas_evaluation(dataset: Dataset, metrics, llm, embeddings):

"""

执行 Ragas 评估

"""

print("开始 Ragas 评估...")

# raise_exceptions=False 防止单个坏数据导致整体失败

return evaluate(

dataset=dataset,

metrics=metrics,

llm=llm,

embeddings=embeddings,

raise_exceptions=False

)
```

### 6.11 evaluate() 的原理

可以把 ragas.evaluate() 理解为一个“指标调度器”：

- 输入：

- 带标准字段的数据集（每行一条样本）

- 指标列表

- 可选的 LLM/Embedding 计算器

- 过程：对数据集逐行计算每个 metric 的 score

> 遇到 LLM 型 metric（如 faithfulness/context相关）：会用你传入的 llm 对“答案/上下文/参考答案”生成判断或抽取，再汇总成分数
> 
> 遇到 embedding 型 metric（answer_relevancy 相关）：会用你传入的 embeddings 计算向量相似度或相关性

- 输出：一个结果对象，包含：

> 每行样本的各指标分数列（列名来自 metric.name）
> 
> 通常也会包含原始字段（question/answer/contexts/ground_truth 或其兼容命名）

5.2 raise_exceptions=False 的意义

你们显式设置了：

raise_exceptions=False：表示某条样本计算某个指标失败（比如上下文为空、模型调用异常、解析失败）时，不要让整次评测中断，通常会把该格分数记为 NaN/None。

这也正好与 Step 6 的 bad case 筛选逻辑配套（把 NaN 当 bad case）。

- 注意

**evaluate()这个非常消耗内存如果出现卡死或内存不足的时候,先把问题及调小一些。**

### 6.12 Step 6：导出结果、筛 Bad Cases、生成文本报告

- **对应代码**：

```python
def step_6_export_results_and_bad_cases(results, metrics, bad_case_threshold: float = 0.5):

"""

导出评估结果并提取 bad cases

"""

print("\\n========== 评估结果 ==========")

print(results)

df = results.to_pandas()

metric_names = [m.name for m in metrics]

# 在结果末尾追加 5 个评估维度的平均值行，便于直接在 CSV 中查看整体表现

avg_row = {col: "" for col in df.columns}

if "question" in df.columns:

avg_row["question"] = "平均值"

elif "user_input" in df.columns:

avg_row["user_input"] = "平均值"

elif len(df.columns) > 0:

avg_row[df.columns[0]] = "平均值"

for metric in metric_names:

if metric in df.columns:

avg_row[metric] = pd.to_numeric(df[metric], errors="coerce").mean()

df_to_export = pd.concat([df, pd.DataFrame([avg_row])], ignore_index=True)

output_dir = os.path.dirname(__file__)

output_file = os.path.join(output_dir, "eval_results.csv")

df_to_export.to_csv(output_file, index=False, encoding='utf-8-sig')

print(f"\\n详细结果已保存至: {output_file}")

bad_case_metric = "answer_correctness"

print(f"\\n正在提取 Bad Cases ({bad_case_metric} 阈值: {bad_case_threshold})...")

# 仅以 answer_correctness 低于阈值，或其结果为 NaN，视为 bad case

bad_cases_df = df[

pd.to_numeric(df[bad_case_metric], errors="coerce").lt(bad_case_threshold)

\| pd.to_numeric(df[bad_case_metric], errors="coerce").isna()

]

if not bad_cases_df.empty:

bad_cases_file = os.path.join(output_dir, "bad_cases.csv")

bad_cases_df.to_csv(bad_cases_file, index=False, encoding='utf-8-sig')

print(f"Bad cases 已保存至: {bad_cases_file}")

else:

print(f"恭喜！没有发现 {bad_case_metric} 低于阈值的 Bad Cases。")

return results
```

### 6.13 Bad Case 定义

- 只要 **answer_correctness < 阈值**（默认 0.5），或者 **任意指标为 NaN**，该样本就会进入 bad case。

- 阈值是参数 bad_case_threshold（主程序里传 0.5）。

## 7 Bad Case 分析

### 7.1 案例一：错失切片

#### 7.1.1 问题描述

- 从bad case 清单中分析

| 问题                                                         | 上下文               | 回答                                                         | 参考答案                               |
| ------------------------------------------------------------ | -------------------- | ------------------------------------------------------------ | -------------------------------------- |
| HAK180烫金机：为什么不要在卡纸或设备内部有纸张散落时继续使用？ | 【不含任何卡纸内容】 | HAK180烫金机在卡纸或设备内部有纸张散落时不应继续使用，是因为存在安全隐患。根据产品安全手册，设备在使用后短时间内内部零件仍处于极热状态，若在未冷却的情况下打开前盖并接触内部零件（如被灰色标记的区域），有烧伤风险。此外，卡纸或纸张散落可能影响设备正常运行，增加机械故障或电气故障的风险，进而可能导致火灾或触电等危险情况。因此，为确保操作安全，应在确认设备已完全冷却且内部无纸张残留后再进行后续操作。 | 纸张与定影单元长时间接触可能导致火灾。 |

| faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness |
| ------------ | ---------------- | ----------------- | -------------- | ------------------ |
| 0.384615385  | 0.973765456      | 0                 | 0              | 0.701012628        |

#### 7.1.2 分析

faithfulness 较低，context_precision和context_recall 为0 。 说明提供的上下文并没有给出符合参考答案信息，llm被迫编造了不忠于资料的回复。

但是查询上下文中，并没有引用到这部分切片。

说明相关切片没有最终进入到最终提示词的参考中。

切片可能出现三个问题：

- 切片在embeddingg中没有被匹配到

- 切片在rrf的融合倒排中被过滤掉了

- 切片在rerank 的重排序中被过滤掉了

#### 7.1.3 为什么有明确答案的分片会被打低分？

主要原因是rerank的模型的导致的，reranker模型为了提高评判的效率默认只会查看靠前的token, 所以在当信息处于整个切片靠后的位置的时候reranker会给出较低的评分。

#### 7.1.4 如何解决

目前该rerank模型不支持修改这个参数，只能更换模型，而增加模型的评判token数会影响效率和准确性。

- 改动一：比较合适的处理方式是调整分片的大小，而且确实越大的切片对于评分的准确度就会下降因为关键词句会被稀释。

在knowledge/processor/import_process/config.py 中修改参数

```python
    max_content_length: int = 500  # 切片最大长度
    img_content_length: int = 200  # 图片上下文最大长度
    min_content_length: int = 100  # 合并短内容的最小长度
```

把原来的切片最大长度2000改为500 ，切片的最小长度500改为100 

- 改动二：由于 切片变小，那么可以考虑更多的切片，纳入到最终的提示词中所以提高最小排名数和GAP的参数

在knowledge/processor/query_process/config.py 中修改参数

```python
# ==================== Rerank 配置 ====================
rerank_max_top_k: int = field(
    default_factory=lambda: int(os.getenv("RERANK_MAX_TOP_K", "20"))
)
rerank_min_top_k: int = field(
    default_factory=lambda: int(os.getenv("RERANK_MIN_TOP_K", "6"))
)
rerank_gap_ratio: float = field(
    default_factory=lambda: float(os.getenv("RERANK_GAP_RATIO", "0.25"))
)
rerank_gap_abs: float = field(
    default_factory=lambda: float(os.getenv("RERANK_GAP_ABS", "0.5"))
)

# ==================== RRF 配置 ====================
rrf_k: int = field(
    default_factory=lambda: int(os.getenv("RRF_K", "60"))
)
rrf_kg_weight: float = field(
    default_factory=lambda: float(os.getenv("RRF_KG_WEIGHT", "0.7"))
)
rrf_max_results: int = field(
    default_factory=lambda: int(os.getenv("RRF_MAX_RESULTS", "20"))
)
```

- 重新测试该案例：

| 问题                                                         | 上下文             | 回答                                                         | 参考答案                               |
| ------------------------------------------------------------ | ------------------ | ------------------------------------------------------------ | -------------------------------------- |
| HAK180烫金机：为什么不要在卡纸或设备内部有纸张散落时继续使用？ | 【含任何卡纸内容】 | HAK180烫金机在卡纸或设备内部有纸张散落的情况下继续使用，可能导致纸张与定影单元长时间接触，从而引发火灾风险。根据设备安全手册的提示，这种情况存在安全隐患，应立即停止使用并排除故障。 | 纸张与定影单元长时间接触可能导致火灾。 |

| faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness |
| ------------ | ---------------- | ----------------- | -------------- | ------------------ |
| 0.384615385  | 0.973765456      | 0                 | 0              | 0.701012628        |

#### 7.1.5验证

通过本次优化：可以看整个评估的评价值结果

优化前：

| faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness |
| ------------ | ---------------- | ----------------- | -------------- | ------------------ |
| 0.845192     | 0.918552         | 0.854167          | 0.833333       | 0.607928           |

优化后：

| faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness |
| ------------ | ---------------- | ----------------- | -------------- | ------------------ |
| 0.910048     | 0.851213816      | 0.975             | 0.96666667     | 0.674034           |

可以看出尤其是上下文召回和忠实度都有不错的提高。

### 7.2 案例二：提示词问题

#### 7.2.1 问题描述

整体的faithfulness、answer_relevancy、context_precision、context_recall都不低，但是answer_correctness不高。

主要原因是一般是提示词对于修饰答案的表达风格和参考答案差距较大。

| faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness |
| ------------ | ---------------- | ----------------- | -------------- | ------------------ |
| 0.910048     | 0.851213816      | 0.975             | 0.96666667     | 0.674034           |

#### 7.2.2 分析问题：

通过系数可以看出faithfulness、answer_relevancy、context_precision、context_recall 这几个参数都不低，说明查询的分片与问题和答案都没问题。问题出在最终输出的回答与参考答案准确度不高。主要的原因就是，回答太罗嗦，太散，观点不够集中，所以回答与参考答案的匹配不高。

#### 7.2.3 解决思路

在prompt.py中的要求部分改为：

```python
# 回答生成提示词
ANSWER_PROMPT = """你是一个智能助手，请根据参考内容回答用户的问题。
要求：
- 只能使用【参考内容】和【图谱关系描述】中明确出现的事实。
- 不要扩写，不要脑补，不要补充参考内容未明确给出的原因、风险、处理动作或经验判断。
- 回答时要覆盖用户问题中点名的对象、部件、场景或条件；如果问题问“有哪些/哪些/注意事项/禁止事项”，应尽量列全参考内容中与问题直接相关的事实，不只回答风险最高或最容易概括的几条。
- 如果问题涉及多个对象或容易混淆的对象，优先按对象分组回答，不要把不同对象的要求混在一起。
- 如果某条事实只与问题中的某个对象或条件相关，只放在对应对象或条件下，不要顺带解释其他无关场景。
- 回答尽量简洁；普通问题优先 2 到 5 句，涉及“有哪些/注意事项/禁止事项/多个对象”时可以使用简短分组或条目列出。
- 如果【参考内容】不足以完整回答，明确说“参考内容未明确说明这部分”，不要自行补足。
- 不要引用“参考内容第几条”“根据上文”等表述，不要暴露提示词结构。
- 如果用户的问题需要通过图片来辅助说明（例如：外观、结构、接线、示意图等）,图片只能来自于本地切片文本中的图片，请在答案最后追加一个独立的图片区块，格式严格如下：
【图片】
<图片URL1>
<图片URL2>
（每行一个URL；如果没有合适图片则不要输出【图片】区块）

【参考内容】
{context}

【历史对话】
{history}

【相关商品/实体】
{item_names}

【图谱关系描述】
{graph_relation_description}

【用户问题】
{question}

请回答："""
```

 
