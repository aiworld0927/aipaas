package com.aipaas.platform.export;

import java.nio.file.Path;

/** 导出文件存储抽象；生产实现必须落对象存储并加密。 */
public interface ExportFileStore {

    void put(String objectKey, Path plaintext);

    byte[] read(String objectKey);

    void delete(String objectKey);
}
