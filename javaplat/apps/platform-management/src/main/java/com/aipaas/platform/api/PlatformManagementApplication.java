package com.aipaas.platform.api;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@EnableScheduling
@SpringBootApplication(scanBasePackages = {
        "com.aipaas.platform.common",
        "com.aipaas.platform.admin", "com.aipaas.platform.api", "com.aipaas.platform.audit",
        "com.aipaas.platform.auth", "com.aipaas.platform.config", "com.aipaas.platform.content",
        "com.aipaas.platform.conversation", "com.aipaas.platform.crypto", "com.aipaas.platform.document",
        "com.aipaas.platform.export",
        "com.aipaas.platform.index", "com.aipaas.platform.ingest", "com.aipaas.platform.knowledgebase",
        "com.aipaas.platform.observability", "com.aipaas.platform.rbac", "com.aipaas.platform.tenant"
})
public class PlatformManagementApplication {

    public static void main(String[] args) {
        SpringApplication.run(PlatformManagementApplication.class, args);
    }
}
