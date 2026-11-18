package com.aipaas.platform.document;

import java.io.InputStream;
import java.nio.file.Path;

/** 仅接收应用层已加密文件，避免把明文对象误写入最终存储。 */
public interface EncryptedObjectStore {

    void put(String objectKey, Path ciphertext);

    /** 读取已加密对象；调用方必须关闭返回的流。 */
    InputStream open(String objectKey);

    void delete(String objectKey);
}
