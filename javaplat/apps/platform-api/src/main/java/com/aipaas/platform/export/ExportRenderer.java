package com.aipaas.platform.export;

import org.springframework.stereotype.Service;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * 导出渲染：不引入第三方文档库，直接生成最小合规的 PDF / XLSX / DOCX。
 * - PDF 使用 Adobe CJK CID 字体 STSong-Light（UniGB-UCS2-H），中文可正常显示；
 * - XLSX 为 OOXML 工作簿，条款逐行输出；
 * - DOCX 为最小 WordprocessingML，供标准简介导出。
 */
@Service
public class ExportRenderer {

    public byte[] clausesPdf(String title, ExportDataClient.ClauseData data) {
        List<PdfLine> lines = new ArrayList<>();
        lines.add(new PdfLine(title, 15));
        lines.add(new PdfLine("条款数：" + data.clauseCount() + "    文档版本：v" + data.version(), 9));
        lines.add(new PdfLine("", 9));
        int index = 1;
        for (ExportDataClient.ClauseItem clause : data.items()) {
            String heading = index++ + ". " + nullToEmpty(clause.clauseNo()) + "  " + nullToEmpty(clause.title());
            lines.add(new PdfLine(heading, 11));
            String body = "正文：" + nullToEmpty(clause.text());
            for (String part : wrap(body, 515, 10)) {
                lines.add(new PdfLine(part, 10));
            }
            StringBuilder meta = new StringBuilder();
            if (notBlank(clause.chapterPath())) {
                meta.append("所属章节：").append(clause.chapterPath()).append("    ");
            }
            if (clause.pageNo() != null) {
                meta.append("页码：").append(clause.pageNo());
            }
            if (meta.length() > 0) {
                lines.add(new PdfLine(meta.toString(), 8));
            }
            lines.add(new PdfLine("", 8));
        }
        return pdf(lines);
    }

    public byte[] summaryPdf(ExportDataClient.SummaryData summary) {
        List<PdfLine> lines = new ArrayList<>();
        lines.add(new PdfLine("标准简介", 18));
        lines.add(new PdfLine("标准编号：" + orMissing(summary.standardNo()), 11));
        lines.add(new PdfLine("标准名称：" + orMissing(summary.standardName()), 11));
        lines.add(new PdfLine("", 10));
        for (ExportDataClient.SummaryElement element : summary.elements()) {
            lines.add(new PdfLine(element.label() + "：" + orMissing(element.value()), 11));
            for (String part : wrap(orMissing(element.value()), 515, 10)) {
                lines.add(new PdfLine("    " + part, 10));
            }
            if (notBlank(element.sourceClauseNo()) || notBlank(element.sourceQuote())) {
                String source = "    来源：" + orDash(element.sourceClauseNo())
                        + (notBlank(element.sourceQuote()) ? " " + element.sourceQuote() : "");
                for (String part : wrap(source, 500, 8)) {
                    lines.add(new PdfLine(part, 8));
                }
            }
            lines.add(new PdfLine("", 8));
        }
        lines.add(new PdfLine("生成时间：" + orDash(summary.generatedAt()) + "    抽取器："
                + orDash(summary.promptTemplateVersion()) + "    文档版本：v" + summary.documentVersion(), 8));
        return pdf(lines);
    }

    public byte[] clausesXlsx(ExportDataClient.ClauseData data) {
        StringBuilder rows = new StringBuilder();
        rows.append(row(1, List.of("条款编号", "条款标题", "条款正文", "所属章节", "页码")));
        int rowIndex = 2;
        for (ExportDataClient.ClauseItem clause : data.items()) {
            rows.append(row(rowIndex++, List.of(
                    nullToEmpty(clause.clauseNo()),
                    nullToEmpty(clause.title()),
                    nullToEmpty(clause.text()),
                    nullToEmpty(clause.chapterPath()),
                    clause.pageNo() == null ? "" : String.valueOf(clause.pageNo()))));
        }
        String sheet = """
                <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <dimension ref="A1:E%s"/>
                  <cols>
                    <col min="1" max="1" width="14" customWidth="1"/>
                    <col min="2" max="2" width="30" customWidth="1"/>
                    <col min="3" max="3" width="90" customWidth="1"/>
                    <col min="4" max="4" width="32" customWidth="1"/>
                    <col min="5" max="5" width="8" customWidth="1"/>
                  </cols>
                  <sheetData>%s</sheetData>
                </worksheet>
                """.formatted(Math.max(1, rowIndex - 1), rows);
        return zip(List.of(
                entry("[Content_Types].xml", """
                        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
                          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
                          <Default Extension="xml" ContentType="application/xml"/>
                          <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
                          <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
                        </Types>
                        """),
                entry("_rels/.rels", """
                        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
                        </Relationships>
                        """),
                entry("xl/workbook.xml", """
                        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                        <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                                  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                          <sheets><sheet name="条款" sheetId="1" r:id="rId1"/></sheets>
                        </workbook>
                        """),
                entry("xl/_rels/workbook.xml.rels", """
                        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
                        </Relationships>
                        """),
                entry("xl/worksheets/sheet1.xml", sheet)));
    }

    public byte[] summaryDocx(ExportDataClient.SummaryData summary) {
        StringBuilder body = new StringBuilder();
        body.append(paragraph("标准简介", true, 18, null));
        body.append(paragraph("标准编号：" + orMissing(summary.standardNo()), false, null, null));
        body.append(paragraph("标准名称：" + orMissing(summary.standardName()), false, null, null));
        body.append(paragraph("", false, null, null));
        for (ExportDataClient.SummaryElement element : summary.elements()) {
            body.append(paragraph(element.label(), true, null, null));
            body.append(paragraph(orMissing(element.value()), false, null, null));
            if (notBlank(element.sourceClauseNo()) || notBlank(element.sourceQuote())) {
                body.append(paragraph("来源：" + orDash(element.sourceClauseNo()) + " "
                        + nullToEmpty(element.sourceQuote()), false, null, "666666"));
            }
        }
        body.append(paragraph("生成时间：" + orDash(summary.generatedAt()) + "    抽取器："
                + orDash(summary.promptTemplateVersion()) + "    文档版本：v" + summary.documentVersion(),
                false, null, "666666"));
        String document = """
                <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
                  <w:body>%s
                    <w:sectPr><w:pgSz w:w="11906" w:h="16838"/>
                      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>
                  </w:body>
                </w:document>
                """.formatted(body);
        return zip(List.of(
                entry("[Content_Types].xml", """
                        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
                          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
                          <Default Extension="xml" ContentType="application/xml"/>
                          <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
                        </Types>
                        """),
                entry("_rels/.rels", """
                        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
                        </Relationships>
                        """),
                entry("word/document.xml", document)));
    }

    /* ------------------------------------------------------------------ PDF */

    private record PdfLine(String text, int size) {
    }

    private byte[] pdf(List<PdfLine> source) {
        List<PdfLine> lines = new ArrayList<>();
        for (PdfLine line : source) {
            if (line.size() >= 12) {
                lines.add(line);
            } else {
                lines.addAll(wrapLines(line.text(), 515, line.size()));
            }
        }
        int perPage = 54;
        List<String> pages = new ArrayList<>();
        StringBuilder content = new StringBuilder();
        int used = 0;
        for (PdfLine line : lines) {
            if (used >= perPage) {
                pages.add(content.toString());
                content.setLength(0);
                used = 0;
            }
            int y = 800 - used * 14;
            content.append("BT /F1 ").append(line.size()).append(" Tf 40 ").append(y).append(" Td <")
                    .append(hex(line.text())).append("> Tj ET\n");
            used++;
        }
        pages.add(content.toString());
        return buildPdf(pages);
    }

    private byte[] buildPdf(List<String> pageContents) {
        int pageCount = pageContents.size();
        List<String> objects = new ArrayList<>();
        int firstPage = 7;
        StringBuilder kids = new StringBuilder();
        for (int i = 0; i < pageCount; i++) {
            int pageObj = firstPage + i * 2;
            kids.append(pageObj).append(" 0 R ");
        }
        objects.add("<< /Type /Catalog /Pages 2 0 R >>");
        objects.add("<< /Type /Pages /Kids [" + kids.toString().trim() + "] /Count " + pageCount + " >>");
        objects.add("<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H "
                + "/DescendantFonts [4 0 R] >>");
        objects.add("<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
                + "/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
                + "/FontDescriptor 5 0 R /DW 1000 >>");
        objects.add("<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 "
                + "/FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 880 /StemV 93 >>");
        objects.add("<< /Producer (AIPaaS) /Title (AIPaaS Export) >>");
        for (int i = 0; i < pageCount; i++) {
            int contentObj = firstPage + i * 2 + 1;
            objects.add("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    + "/Resources << /Font << /F1 3 0 R >> >> /Contents " + contentObj + " 0 R >>");
            String stream = pageContents.get(i);
            objects.add("<< /Length " + stream.getBytes(StandardCharsets.US_ASCII).length + " >>\nstream\n"
                    + stream + "endstream");
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try {
            out.write("%PDF-1.4\n%".getBytes(StandardCharsets.US_ASCII));
            out.write(new byte[] {(byte) 0xE2, (byte) 0xE3, (byte) 0xCF, (byte) 0xD3});
            out.write('\n');
            List<Integer> offsets = new ArrayList<>();
            for (int i = 0; i < objects.size(); i++) {
                offsets.add(out.size());
                out.write(((i + 1) + " 0 obj\n" + objects.get(i) + "\nendobj\n")
                        .getBytes(StandardCharsets.UTF_8));
            }
            int xref = out.size();
            out.write(("xref\n0 " + (objects.size() + 1) + "\n0000000000 65535 f \n")
                    .getBytes(StandardCharsets.US_ASCII));
            for (Integer offset : offsets) {
                out.write((String.format("%010d 00000 n \n", offset)).getBytes(StandardCharsets.US_ASCII));
            }
            out.write(("trailer\n<< /Size " + (objects.size() + 1) + " /Root 1 0 R /Info 6 0 R >>\nstartxref\n"
                    + xref + "\n%%EOF\n").getBytes(StandardCharsets.US_ASCII));
            return out.toByteArray();
        } catch (IOException ex) {
            throw new UncheckedIOException(ex);
        }
    }

    private static List<PdfLine> wrapLines(String text, int maxWidth, int size) {
        List<PdfLine> result = new ArrayList<>();
        for (String part : wrap(text, maxWidth, size)) {
            result.add(new PdfLine(part, size));
        }
        return result;
    }

    private static List<String> wrap(String text, int maxWidth, int size) {
        List<String> lines = new ArrayList<>();
        String value = nullToEmpty(text).replace("\r", "").replace("\t", "    ");
        for (String paragraph : value.split("\n", -1)) {
            if (paragraph.isEmpty()) {
                lines.add("");
                continue;
            }
            StringBuilder current = new StringBuilder();
            double width = 0;
            for (int i = 0; i < paragraph.length(); i++) {
                char ch = paragraph.charAt(i);
                double charWidth = ch < 128 ? size * 0.55 : size;
                if (width + charWidth > maxWidth && current.length() > 0) {
                    lines.add(current.toString());
                    current.setLength(0);
                    width = 0;
                }
                current.append(ch);
                width += charWidth;
            }
            lines.add(current.toString());
        }
        return lines;
    }

    private static String hex(String text) {
        StringBuilder hex = new StringBuilder();
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (Character.isHighSurrogate(ch) && i + 1 < text.length()
                    && Character.isLowSurrogate(text.charAt(i + 1))) {
                i++;
                ch = '?';
            }
            if (ch < 0x20 && ch != '\t') {
                ch = ' ';
            }
            hex.append(String.format("%04X", (int) ch));
        }
        return hex.toString();
    }

    /* -------------------------------------------------------------- 通用工具 */

    private record Entry(String name, byte[] bytes) {
    }

    private static Entry entry(String name, String content) {
        return new Entry(name, content.stripIndent().getBytes(StandardCharsets.UTF_8));
    }

    private static byte[] zip(List<Entry> entries) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(out, StandardCharsets.UTF_8)) {
            for (Entry entry : entries) {
                zip.putNextEntry(new ZipEntry(entry.name()));
                zip.write(entry.bytes());
                zip.closeEntry();
            }
        } catch (IOException ex) {
            throw new UncheckedIOException(ex);
        }
        return out.toByteArray();
    }

    private static String row(int index, List<String> values) {
        StringBuilder row = new StringBuilder("<row r=\"").append(index).append("\">");
        for (int i = 0; i < values.size(); i++) {
            String ref = (char) ('A' + i) + String.valueOf(index);
            row.append("<c r=\"").append(ref).append("\" t=\"inlineStr\"><is><t xml:space=\"preserve\">")
                    .append(xml(values.get(i))).append("</t></is></c>");
        }
        return row.append("</row>").toString();
    }

    private static String paragraph(String text, boolean bold, Integer size, String color) {
        StringBuilder run = new StringBuilder("<w:r><w:rPr>");
        if (bold) {
            run.append("<w:b/>");
        }
        if (size != null) {
            run.append("<w:sz w:val=\"").append(size * 2).append("\"/>");
        }
        if (color != null) {
            run.append("<w:color w:val=\"").append(color).append("\"/>");
        }
        run.append("</w:rPr><w:t xml:space=\"preserve\">").append(xml(text)).append("</w:t></w:r>");
        return "<w:p>" + run + "</w:p>";
    }

    private static String xml(String value) {
        StringBuilder escaped = new StringBuilder();
        for (int i = 0; i < nullToEmpty(value).length(); i++) {
            char ch = value.charAt(i);
            switch (ch) {
                case '&' -> escaped.append("&amp;");
                case '<' -> escaped.append("&lt;");
                case '>' -> escaped.append("&gt;");
                case '"' -> escaped.append("&quot;");
                case '\'' -> escaped.append("&apos;");
                default -> {
                    if (ch == 0x9 || ch == 0xA || ch == 0xD || ch >= 0x20) {
                        escaped.append(ch);
                    }
                }
            }
        }
        return escaped.toString();
    }

    private static boolean notBlank(String value) {
        return value != null && !value.isBlank();
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String orMissing(String value) {
        return notBlank(value) ? value : "未识别";
    }

    private static String orDash(String value) {
        return notBlank(value) ? value : "-";
    }
}
