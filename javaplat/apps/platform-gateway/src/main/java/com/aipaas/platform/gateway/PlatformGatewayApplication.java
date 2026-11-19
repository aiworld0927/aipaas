package com.aipaas.platform.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = {
        "com.aipaas.platform.common", "com.aipaas.platform.auth", "com.aipaas.platform.chat",
        "com.aipaas.platform.content", "com.aipaas.platform.gateway", "com.aipaas.platform.observability"
})
public class PlatformGatewayApplication {

    public static void main(String[] args) {
        SpringApplication.run(PlatformGatewayApplication.class, args);
    }
}
