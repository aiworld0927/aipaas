package com.aipaas.platform.export;

import org.junit.jupiter.api.Test;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.zip.ZipInputStream;

import static org.junit.jupiter.api.Assertions.*;

/** 导出格式自检：PDF 结构、DOCX 五要素文本、XLSX 行数与条款数一致。 */
class ExportRendererTest {

    private final ExportRenderer renderer = new ExportRenderer();

    @Test
    void clausePdfIsWellFormed() {
        byte[] pdf = renderer.clausesPdf("条款清单", clauses());
        String text = new String(pdf, StandardCharsets.ISO_8859_1);
        assertTrue(text.startsWith("%PDF-1.4"));
        assertTrue(text.contains("/STSong-Light"));
        assertTrue(text.contains("UniGB-UCS2-H"));
        assertTrue(text.endsWith("%%EOF\n"));
    }

    @Test
    void summaryDocxContainsAllFiveElements() {
        byte[] docx = renderer.summaryDocx(summary());
        String documentXml = unzip(docx, "word/document.xml");
        for (String label : List.of("标准编号", "标准名称", "适用范围", "主要技术内容概述", "实施日期")) {
            assertTrue(documentXml.contains(label), "DOCX 缺少 " + label);
        }
        assertTrue(documentXml.contains("未识别"));
        assertTrue(documentXml.contains("GB/T 35273-2020"));
    }

    @Test
    void xlsxHasOneRowPerClausePlusHeader() {
        byte[] xlsx = renderer.clausesXlsx(clauses());
        String sheet = unzip(xlsx, "xl/worksheets/sheet1.xml");
        assertEquals(4, occurrences(sheet, "<row "));
        assertTrue(sheet.contains("dimension ref=\"A1:E4\""));
    }

    @Test
    void summaryPdfRendersWithoutMissingElementErrors() {
        byte[] pdf = renderer.summaryPdf(summary());
        assertTrue(new String(pdf, StandardCharsets.ISO_8859_1).startsWith("%PDF-1.4"));
    }

    private static ExportDataClient.ClauseData clauses() {
        return new ExportDataClient.ClauseData("doc-1", 1, 3, List.of(
                new ExportDataClient.ClauseItem("4.1.1", "总则", "本文件规定了基本原则。", "第4章", 1, 2, "c1"),
                new ExportDataClient.ClauseItem("4.1.2", "同意", "应取得同意。", "第4章", 1, 2, "c2"),
                new ExportDataClient.ClauseItem("4.1.3", "最小必要", "仅收集必要信息。", "第4章", 2, 2, "c3")));
    }

    private static ExportDataClient.SummaryData summary() {
        return new ExportDataClient.SummaryData("doc-1", "GB/T 35273-2020", "信息安全技术 个人信息安全规范",
                "本文件规定了个人信息处理活动中的安全要求。", "本文件共 6 条，主要技术内容包括：总则、同意。",
                null, List.of(
                new ExportDataClient.SummaryElement("standard_no", "标准编号", "GB/T 35273-2020", null, null),
                new ExportDataClient.SummaryElement("standard_name", "标准名称",
                        "信息安全技术 个人信息安全规范", null, null),
                new ExportDataClient.SummaryElement("scope", "适用范围",
                        "本文件规定了个人信息处理活动中的安全要求。", "4.1.1", "本文件规定了……"),
                new ExportDataClient.SummaryElement("main_content", "主要技术内容概述",
                        "本文件共 6 条，主要技术内容包括：总则、同意。", "4.1.1", "总则；同意"),
                new ExportDataClient.SummaryElement("effective_date", "实施日期", "未识别", null, null)),
                "2026-11-18T00:00:00Z", "extractive-v1", "summary-extract-v1", 0.8, 1);
    }

    private static String unzip(byte[] zip, String name) {
        try (ZipInputStream input = new ZipInputStream(new ByteArrayInputStream(zip))) {
            java.util.zip.ZipEntry entry;
            while ((entry = input.getNextEntry()) != null) {
                if (entry.getName().equals(name)) {
                    return new String(input.readAllBytes(), StandardCharsets.UTF_8);
                }
            }
            fail("zip 缺少 " + name);
            return "";
        } catch (Exception ex) {
            throw new AssertionError(ex);
        }
    }

    private static int occurrences(String text, String needle) {
        int count = 0;
        int index = 0;
        while ((index = text.indexOf(needle, index)) >= 0) {
            count++;
            index += needle.length();
        }
        return count;
    }
}
