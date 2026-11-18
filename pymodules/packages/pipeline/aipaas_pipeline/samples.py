"""样例标准文档生成器。

供解析报告脚本与单元测试共用，避免样例与断言漂移。
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from openpyxl import Workbook


def _pdf(lines: list[tuple[str, int]], *, second_page: list[tuple[str, int]] | None = None,
         with_table: bool = False) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    y = 60
    for text, size in lines:
        page.insert_text((50, y), text, fontsize=size, fontname="china-s")
        y += size + 10
    if with_table:
        top = y + 10
        _draw_table(page, 50, top, [50, 200, 350], [top, top + 28, top + 56])
        cells = [("条款号", "要求", "说明"), ("4.1.1", "最小必要", "收集个人信息应最小必要"),
                 ("4.1.2", "告知同意", "应告知处理目的")]
        for row_index, row in enumerate(cells):
            for col_index, value in enumerate(row):
                page.insert_text(([55, 205, 355][col_index], top + 18 + row_index * 28), value,
                                 fontsize=9, fontname="china-s")
    if second_page:
        page2 = document.new_page()
        y = 60
        for text, size in second_page:
            page2.insert_text((50, y), text, fontsize=size, fontname="china-s")
            y += size + 10
    data = document.tobytes()
    document.close()
    return data


def _draw_table(page: "pymupdf.Page", left: float, top: float, xs: list[float], ys: list[float]) -> None:
    for y in ys:
        page.draw_line((left, y), (xs[-1], y))
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))


def pdf_gbt_35273() -> bytes:
    return _pdf(
        [
            ("GB/T 35273-2020", 12),
            ("信息安全技术 个人信息安全规范", 20),
            ("4 个人信息安全要求", 15),
            ("4.1 数据安全", 13),
            ("4.1.1 总则", 12),
            ("本文件规定了个人信息处理活动应遵循的基本原则。", 11),
            ("4.1.2 最小必要", 12),
            ("收集个人信息应当遵循最小必要原则，不得过度收集。", 11),
        ],
        second_page=[("5 管理要求", 15), ("5.1 安全管理制度", 12), ("应建立个人信息安全管理制度。", 11)],
        with_table=True,
    )


def pdf_jrt_0197() -> bytes:
    return _pdf(
        [
            ("JR/T 0197-2020", 12),
            ("金融数据安全 数据安全分级指南", 19),
            ("1 范围", 14),
            ("本文件适用于金融机构数据安全分级工作。", 11),
            ("4 数据安全分级", 14),
            ("4.1 分级要素", 12),
            ("4.1.1 识别数据资产", 11),
            ("金融机构应识别数据资产并形成数据资产目录。", 11),
            ("附录A 数据安全级别", 12),
            ("A.1 级别定义", 11),
            ("数据安全级别由低到高分为1级至5级。", 11),
        ]
    )


def pdf_scanned() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 400, 300), color=(0.2, 0.2, 0.2), fill=(0.9, 0.9, 0.9))
    page.draw_rect(pymupdf.Rect(60, 60, 380, 120), color=(0.2, 0.2, 0.2))
    data = document.tobytes()
    document.close()
    return data


def pdf_garbled() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    text = "Ã¦Â¸Â¬Ã¨Â¯Â•æ–‡æœ¬ " * 40
    for index in range(12):
        page.insert_text((40, 60 + index * 18), text[:40], fontsize=10)
    data = document.tobytes()
    document.close()
    return data


def _revision(parent, *, tag: str, text: str, identifier: str) -> None:
    element = OxmlElement(tag)
    element.set(qn("w:id"), identifier)
    element.set(qn("w:author"), "reviewer")
    run = OxmlElement("w:r")
    node = OxmlElement("w:delText" if tag == "w:del" else "w:t")
    node.text = text
    run.append(node)
    element.append(run)
    parent.append(element)


def docx_gbt_22239() -> bytes:
    document = Document()
    document.add_heading("GB/T 22239-2019 信息安全技术 网络安全等级保护基本要求", level=0)
    document.add_heading("4 总体要求", level=1)
    document.add_heading("4.1 安全通用要求", level=2)
    document.add_heading("4.1.1 身份鉴别", level=3)
    document.add_paragraph("应对登录用户进行身份鉴别，鉴别信息应具有复杂度要求。")
    document.add_paragraph("身份鉴别失败应记录日志。", style="List Bullet")
    document.add_heading("4.1.2 访问控制", level=3)
    document.add_paragraph("应遵循最小权限原则配置访问控制策略。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "控制项"
    table.cell(0, 1).text = "要求"
    table.cell(1, 0).text = "身份鉴别"
    table.cell(1, 1).text = "双因素认证"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def docx_jrt_0197_revised() -> bytes:
    document = Document()
    document.add_heading("JR/T 0197-2020 金融数据安全 数据安全分级指南", level=0)
    document.add_heading("4 数据安全分级", level=1)
    document.add_heading("4.1 分级要素", level=2)
    paragraph = document.add_paragraph("4.1.1 识别数据资产：")
    run = paragraph.add_run("金融机构应识别数据资产。")
    document.add_comment([run], text="请补充数据资产目录模板。", author="张三", initials="ZS")
    revised = document.add_paragraph("本文件按修订稿执行。")
    _revision(revised._p, tag="w:ins", text="新增：应每年更新数据资产目录。", identifier="101")
    _revision(revised._p, tag="w:del", text="删除：每季度更新。", identifier="102")
    document.add_heading("4.1.2 分级方法", level=3)
    document.add_paragraph("应根据影响程度和影响范围确定数据安全级别。")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def docx_nested() -> bytes:
    document = Document()
    document.add_heading("JR/T 0171-2020 个人金融信息保护技术规范", level=1)
    document.add_heading("1 范围", level=2)
    document.add_paragraph("本文件规定了个人金融信息保护要求。")
    document.add_heading("4 技术要求", level=2)
    document.add_heading("4.1 生命周期", level=3)
    document.add_heading("4.1.1 收集", level=4)
    document.add_paragraph("收集个人金融信息应取得授权同意。")
    document.add_paragraph("一级要求", style="List Number")
    document.add_paragraph("二级要求", style="List Number 2")
    document.add_heading("4.1.2 存储", level=4)
    document.add_paragraph("存储个人金融信息应采取加密措施。")
    table = document.add_table(rows=3, cols=3)
    for row_index, row in enumerate([("阶段", "要求", "级别"), ("收集", "授权", "C3"), ("存储", "加密", "C3")]):
        for col_index, value in enumerate(row):
            table.cell(row_index, col_index).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def xlsx_gbt_clauses() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "条款清单"
    sheet.append(["GB/T 35273-2020 个人信息安全规范"])
    sheet.merge_cells("A1:C1")
    sheet.append(["条款号", "标题", "要求"])
    sheet.append(["4.1.1", "总则", "遵循合法、正当、必要原则"])
    sheet.append(["4.1.2", "最小必要", "不得过度收集个人信息"])
    sheet.append(["4.1.3", "告知同意", "应明示处理目的和方式"])
    sheet.append(["5.1", "管理制度", "建立个人信息安全管理制度"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def xlsx_jrt_multi() -> bytes:
    workbook = Workbook()
    terms = workbook.active
    terms.title = "术语"
    terms.append(["术语", "定义"])
    terms.append(["个人金融信息", "金融机构在业务中获取的客户信息"])
    terms.append(["数据分级", "按影响程度划分数据安全级别"])
    requirements = workbook.create_sheet("要求")
    requirements.append(["条款号", "要求"])
    requirements.append(["4.1.1", "加密存储"])
    requirements.append(["4.1.2", "访问控制"])
    requirements.append(["合计", "=COUNTA(A2:A3)"])
    workbook.create_sheet("空表")
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def xls_legacy() -> bytes:
    import xlwt

    workbook = xlwt.Workbook(encoding="utf-8")
    sheet = workbook.add_sheet("条款")
    rows = [
        ("条款号", "要求", "说明"),
        ("4.1.1", "身份鉴别", "应对用户进行身份鉴别"),
        ("4.1.2", "访问控制", "应遵循最小权限"),
        ("5.1", "安全管理", "应建立安全管理制度"),
    ]
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            sheet.write(row_index, col_index, value)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def md_gbt() -> bytes:
    return """# GB/T 35273-2020 信息安全技术 个人信息安全规范

## 4 个人信息安全要求

4.1 数据安全

4.1.1 总则
处理个人信息应遵循合法、正当、必要和诚信原则。

4.1.2 最小必要
收集个人信息应当限于实现处理目的的最小范围。

| 条款 | 要求 | 级别 |
| --- | --- | --- |
| 4.1.1 | 总则 | 强制 |
| 4.1.2 | 最小必要 | 强制 |

- 不应以捆绑方式收集
- 不应频繁弹窗征求同意
""".encode("utf-8")


def md_jrt_code() -> bytes:
    return """# JR/T 0197-2020 金融数据安全 数据安全分级指南

## 4 数据安全分级

4.1 分级要素

4.1.1 识别数据资产
金融机构应识别数据资产，并形成数据资产目录。

```yaml
dataAsset:
  name: 客户身份信息
  level: 4
  owner: 数据治理部门
```

### 4.1.2 分级规则

> 注：分级结果应经数据安全负责人审批。

| 影响程度 | 影响范围 | 级别 |
| --- | --- | --- |
| 严重 | 广泛 | 5级 |
| 中等 | 局部 | 3级 |
""".encode("utf-8")


def md_nested() -> bytes:
    return """# JR/T 0171-2020 个人金融信息保护技术规范

## 1 范围

本文件规定了个人金融信息保护要求。

## 4 技术要求

### 4.1 生命周期管理

#### 4.1.1 收集

1. 明示收集目的
2. 取得授权同意
   - 特殊场景需单独同意
   - 敏感信息需增强告知

#### 4.1.2 存储

```python
def encrypt(value: str, key: bytes) -> bytes:
    \"\"\"示例：存储加密，代码块必须整体保留。\"\"\"
    return aes_gcm(value, key)
```

##### 4.1.2.1 密钥管理

密钥应定期轮换并双人复核。

```text
KMS-ROTATE-90D
```
""".encode("utf-8")


def md_dirty() -> bytes:
    """#14 清洗回归样例：BOM、控制字符、重复空白、连续空行与代码块保护。"""
    content = (
        "\ufeff# JR/T 0197-2020 金融数据安全 数据安全分级指南\r\n"
        "\r\n"
        "## 第4章  数据分级\r\n"
        "\r\n\r\n\r\n"
        "4.1.1 数据分级\r\n"
        "\r\n"
        "组织应建立\t\t数据分级制度â€™。\r\n"
        "\r\n"
        "4.1.2 加密存储\r\n"
        "\r\n"
        "应采用  \x0c 加密  存储，并  \ufeff留存  日志。\r\n"
        "\r\n"
        "```text\r\n"
        "SELECT  *\r\n"
        "  FROM  grade\r\n"
        "```\r\n"
        "正文结束。\r\n"
    )
    return content.encode("utf-8")


SAMPLES: dict[str, tuple[str, object]] = {
    "pdf/GBT-35273-2020.pdf": ("pdf", pdf_gbt_35273),
    "pdf/JRT-0197-2020.pdf": ("pdf", pdf_jrt_0197),
    "pdf/GBT-22239-2019.pdf": ("pdf", lambda: _pdf([
        ("GB/T 22239-2019", 12), ("信息安全技术 网络安全等级保护基本要求", 18),
        ("7 安全通用要求", 14), ("7.1 身份鉴别", 12),
        ("7.1.1 应对登录用户进行身份鉴别。", 11), ("7.2 访问控制", 12),
        ("7.2.1 应遵循最小权限原则。", 11),
    ], with_table=True)),
    "pdf/scanned-sample.pdf": ("pdf", pdf_scanned),
    "pdf/garbled-sample.pdf": ("pdf", pdf_garbled),
    "word/GBT-22239-2019.docx": ("docx", docx_gbt_22239),
    "word/JRT-0197-2020-revised.docx": ("docx", docx_jrt_0197_revised),
    "word/JRT-0171-2020.docx": ("docx", docx_nested),
    "excel/GBT-35273-2020.xlsx": ("xlsx", xlsx_gbt_clauses),
    "excel/JRT-0197-2020.xlsx": ("xlsx", xlsx_jrt_multi),
    "excel/JRT-0171-2020.xls": ("xls", xls_legacy),
    "markdown/GBT-35273-2020.md": ("md", md_gbt),
    "markdown/JRT-0197-2020.md": ("md", md_jrt_code),
    "markdown/JRT-0171-2020.md": ("md", md_nested),
    "markdown/JRT-0197-2020-dirty.md": ("md", md_dirty),
}


def write_samples(root: str | Path) -> dict[str, Path]:
    """写出全部样例并返回 相对路径→绝对路径。"""
    base = Path(root)
    written: dict[str, Path] = {}
    for relative, (_, builder) in SAMPLES.items():
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(builder())
        written[relative] = target
    return written
