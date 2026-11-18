package com.aipaas.platform.export;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.document.DocumentRepository;
import com.aipaas.platform.rbac.RbacService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicReference;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** #406 导出任务：权限、条目数、格式、重试与下载前二次鉴权。 */
class ExportServiceTest {

    private ExportRepository repository;
    private DocumentRepository documents;
    private RbacService rbac;
    private AuditService audit;
    private ExportDataClient data;
    private ExportService service;
    private SecurityPrincipal principal;
    private final AtomicReference<ExportJob> stored = new AtomicReference<>();
    private final Map<String, byte[]> files = new java.util.concurrent.ConcurrentHashMap<>();

    @BeforeEach
    void setUp() {
        repository = mock(ExportRepository.class);
        documents = mock(DocumentRepository.class);
        rbac = mock(RbacService.class);
        audit = mock(AuditService.class);
        data = mock(ExportDataClient.class);
        ExportFileStore store = new ExportFileStore() {
            @Override
            public void put(String objectKey, Path plaintext) {
                try {
                    files.put(objectKey, java.nio.file.Files.readAllBytes(plaintext));
                } catch (java.io.IOException ex) {
                    throw new IllegalStateException(ex);
                }
            }

            @Override
            public byte[] read(String objectKey) {
                return files.get(objectKey);
            }

            @Override
            public void delete(String objectKey) {
                files.remove(objectKey);
            }
        };
        Executor direct = Runnable::run;
        service = new ExportService(repository, documents, rbac, audit, data, new ExportRenderer(), store, direct,
                Duration.ofHours(24), 0, "/tmp/aipaas-export-test");
        principal = new SecurityPrincipal("usr_1", "tenant-a", "analyst", "Analyst", "session",
                Set.of("ANALYST"), Set.of("export:create", "export:read", "export:download"));
        when(rbac.allowed(any(), anyString(), anyString())).thenReturn(true);
        when(documents.findActive("tenant-a", "doc-1")).thenReturn(Optional.of(document()));
        doAnswer(invocation -> {
            stored.set(invocation.getArgument(0));
            return null;
        }).when(repository).insert(any());
        when(repository.findById(anyString())).thenAnswer(invocation -> stored.get());
        when(repository.claim(anyString(), any(OffsetDateTime.class))).thenReturn(true);
        when(data.clauses("doc-1", "tenant-a")).thenReturn(clauses(3));
    }

    @Test
    void clauseExportRendersEveryClauseAndRecordsFingerprint() {
        ExportService.ExportView view = service.create("kb-1",
                new ExportService.CreateExportRequest(null, "doc-1", "CLAUSES", "XLSX"), principal, "idem-1");

        assertNotNull(view.id());
        assertEquals("QUEUED", view.status());
        assertTrue(files.keySet().stream().anyMatch(key -> key.contains(view.id())));
        verify(repository).succeed(eq(view.id()), eq(3), contains(view.id()), contains(".xlsx"),
                anyLong(), matches("[0-9a-f]{64}"), any(OffsetDateTime.class), any(OffsetDateTime.class));
        byte[] xlsx = files.values().iterator().next();
        String sheet = unzip(xlsx, "xl/worksheets/sheet1.xml");
        assertEquals(4, count(sheet, "<row "));
        assertTrue(sheet.contains("4.1.1"));
        assertTrue(sheet.contains("4.1.2"));
        assertTrue(sheet.contains("4.1.3"));
    }

    @Test
    void downloadRechecksKbPermissionAndAuditsDenial() {
        ExportJob succeeded = succeededJob();
        when(repository.find("tenant-a", "exp_1")).thenReturn(Optional.of(succeeded));
        files.put(succeeded.objectKey(), "hello".getBytes(StandardCharsets.UTF_8));
        when(rbac.allowed(principal, "export:download", "kb-1")).thenReturn(false);

        ApiException denied = assertThrows(ApiException.class, () -> service.download("exp_1", principal));
        assertEquals(ErrorCodes.PERM_EXPORT_FORBIDDEN, denied.getErrorCode());
        verify(audit).record(eq(principal), eq("export.download"), eq("export"), eq("exp_1"),
                eq("DENIED"), anyMap());
    }

    @Test
    void expiredExportCannotBeDownloaded() {
        ExportJob expired = new ExportJob("exp_1", "tenant-a", "kb-1", "doc-1", "CLAUSES", "PDF", "SUCCEEDED",
                100, "KB:kb-1|DOC:doc-1|CLAUSES:ALL", 3, 0, 2, "usr_1", null, "key", "a.pdf", 10, "hash",
                null, null, OffsetDateTime.now(ZoneOffset.UTC).minusMinutes(1), null, null,
                OffsetDateTime.now(ZoneOffset.UTC), OffsetDateTime.now(ZoneOffset.UTC));
        ExportJob refreshed = new ExportJob(expired.id(), expired.tenantId(), expired.kbId(), expired.documentId(),
                expired.exportType(), expired.format(), "EXPIRED", 100, expired.scope(), 3, 0, 2, "usr_1", null,
                "key", "a.pdf", 10, "hash", null, null, expired.expiresAt(), null, null,
                expired.createdAt(), expired.updatedAt());
        when(repository.find("tenant-a", "exp_1")).thenReturn(Optional.of(expired), Optional.of(refreshed));

        ApiException conflict = assertThrows(ApiException.class, () -> service.download("exp_1", principal));
        assertEquals(ErrorCodes.BIZ_CONFLICT, conflict.getErrorCode());
        verify(repository).markExpired(eq("exp_1"), any(OffsetDateTime.class));
        verify(audit).record(eq(principal), eq("export.download"), eq("export"), eq("exp_1"),
                eq("DENIED"), anyMap());
    }

    @Test
    void clauseCountMismatchFailsWithoutExporting() {
        when(data.clauses("doc-1", "tenant-a")).thenReturn(
                new ExportDataClient.ClauseData("doc-1", 1, 3, clauses(2).items()));

        service.create("kb-1", new ExportService.CreateExportRequest(null, "doc-1", "CLAUSES", "PDF"),
                principal, null);

        verify(repository).fail(eq(stored.get().id()), eq(ErrorCodes.BIZ_EXPORT_NOT_READY),
                contains("条款数量不一致"), any(OffsetDateTime.class));
        assertTrue(files.isEmpty());
    }

    @Test
    void unauthorizedCreateIsDeniedAndAudited() {
        when(rbac.allowed(principal, "export:create", "kb-1")).thenReturn(false);

        ApiException denied = assertThrows(ApiException.class, () -> service.create("kb-1",
                new ExportService.CreateExportRequest(null, "doc-1", "CLAUSES", "PDF"), principal, null));

        assertEquals(ErrorCodes.PERM_EXPORT_FORBIDDEN, denied.getErrorCode());
        verify(repository, never()).insert(any());
        verify(audit).record(eq(principal), eq("export.create"), eq("knowledgeBase"), eq("kb-1"),
                eq("DENIED"), anyMap());
    }

    @Test
    void summaryExportNeedsFiveElements() {
        when(data.summary("doc-1", "tenant-a")).thenReturn(new ExportDataClient.SummaryData("doc-1",
                "GB/T 35273-2020", "个人信息安全规范", "范围", "内容", null,
                List.of(new ExportDataClient.SummaryElement("standard_no", "标准编号", "GB/T 35273-2020", null, null)),
                "now", "extractive-v1", "summary-extract-v1", 0.6, 1));

        service.create("kb-1", new ExportService.CreateExportRequest(null, "doc-1", "STANDARD_SUMMARY", "DOCX"),
                principal, null);

        verify(repository).fail(eq(stored.get().id()), eq(ErrorCodes.BIZ_EXPORT_NOT_READY),
                contains("五要素不完整"), any(OffsetDateTime.class));
    }

    @Test
    void idempotencyKeyReturnsExistingJob() {
        ExportJob existing = succeededJob();
        when(repository.findByIdempotency("tenant-a", "usr_1", "same-key")).thenReturn(Optional.of(existing));

        ExportService.ExportView view = service.create("kb-1",
                new ExportService.CreateExportRequest(null, "doc-1", "CLAUSES", "PDF"), principal, "same-key");

        assertEquals(existing.id(), view.id());
        verify(repository, never()).insert(any());
    }

    private ExportJob succeededJob() {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        return new ExportJob("exp_1", "tenant-a", "kb-1", "doc-1", "CLAUSES", "PDF", "SUCCEEDED", 100,
                "KB:kb-1|DOC:doc-1|CLAUSES:ALL", 3, 0, 2, "usr_1", null, "tenant-a/exports/exp_1.pdf.a1",
                "a.pdf", 10, "hash", null, null, now.plusHours(1), now, now, now, now);
    }

    private static DocumentRepository.DocumentRow document() {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        return new DocumentRepository.DocumentRow("doc-1", "tenant-a", "kb-1", "cipher", null, "key", "PDF",
                100, "READY", "hash", "READY", 3, null, null, "usr_1", now, now);
    }

    private static ExportDataClient.ClauseData clauses(int count) {
        List<ExportDataClient.ClauseItem> items = new java.util.ArrayList<>();
        String[] numbers = {"4.1.1", "4.1.2", "4.1.3"};
        for (int i = 0; i < count; i++) {
            items.add(new ExportDataClient.ClauseItem(numbers[i], "条款" + i, "正文" + i, "第4章 总则", i + 1, 2,
                    "chunk-" + i));
        }
        return new ExportDataClient.ClauseData("doc-1", 1, count, List.copyOf(items));
    }

    private static String unzip(byte[] zip, String name) {
        try (ZipInputStream input = new ZipInputStream(new ByteArrayInputStream(zip))) {
            ZipEntry entry;
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

    private static int count(String text, String needle) {
        int count = 0;
        int index = 0;
        while ((index = text.indexOf(needle, index)) >= 0) {
            count++;
            index += needle.length();
        }
        return count;
    }
}
