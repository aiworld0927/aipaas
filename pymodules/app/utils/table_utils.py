"""用占位符 隐藏 表格内的换行符（推荐）"""
import re

# 占位符：word-joiner + NL + word-joiner，在 embedding 中不可见
_TABLE_NL = "\u2060NL\u2060"

def is_table_line(line: str) -> bool:
    """判断一行是否像表格行（Markdown 表格或 financial 表格）"""
    s = line.strip()
    # Markdown 表格行：以 | 开头和结尾
    if s.startswith("|") and s.endswith("|"):
        return True
    # 包含 $ 且至少有一个数字
    if "$" in s and re.search(r'\d', s):
        return True
    # 包含 3 个以上逗号分隔的大数字（如 1,234,567）
    if len(re.findall(r'\b\d{1,3}(?:,\d{3})+\b', s)) >= 3:
        return True
    # 虚线分隔行
    if re.fullmatch(r'[\s\-\$=]+', s) and len(s) > 4:
        return True
    return False

def protect_tables(text: str) -> str:
    """将连续表格行内部的换行符替换为占位符，使整块表格成为一行"""
    lines = text.split("\n")
    out = []
    in_table = False
    for line in lines:
        if is_table_line(line):
            if not in_table:
                # 表格开始，先清空缓冲区
                in_table = True
            # 表格行：换行符替换为占位符
            out.append(line + _TABLE_NL)
        else:
            if in_table:
                # 表格结束，清理最后一个占位符
                if out:
                    out[-1] = out[-1].replace(_TABLE_NL, "")
                in_table = False
            out.append(line)
    return "\n".join(out)

def restore_tables(chunks):
    """将占位符还原为换行符"""
    for chunk in chunks:
        chunk.page_content = chunk.page_content.replace(_TABLE_NL, "\n")
    return chunks
