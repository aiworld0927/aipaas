package com.aipaas.platform.ingest;

/** 解析服务调用失败（网络、超时、协议错误），由任务执行器按上限自动重试。 */
public class ParseClientException extends RuntimeException {

    public ParseClientException(String message, Throwable cause) {
        super(message, cause);
    }

    public ParseClientException(String message) {
        super(message);
    }
}
