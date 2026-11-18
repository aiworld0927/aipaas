package com.aipaas.platform.document;

import io.minio.BucketExistsArgs;
import io.minio.MakeBucketArgs;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import io.minio.RemoveObjectArgs;
import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;

@Service
@ConditionalOnProperty(name = "aipaas.storage.type", havingValue = "minio", matchIfMissing = true)
public class MinioEncryptedObjectStore implements EncryptedObjectStore {

    private final MinioClient client;
    private final String bucket;

    public MinioEncryptedObjectStore(@Value("${aipaas.storage.minio.endpoint:http://minio:9000}") String endpoint,
                                     @Value("${aipaas.storage.minio.access-key}") String accessKey,
                                     @Value("${aipaas.storage.minio.secret-key}") String secretKey,
                                     @Value("${aipaas.storage.minio.bucket:aipaas-p0}") String bucket) {
        this.client = MinioClient.builder().endpoint(endpoint).credentials(accessKey, secretKey).build();
        this.bucket = bucket;
    }

    @PostConstruct
    void initialize() throws Exception {
        if (!client.bucketExists(BucketExistsArgs.builder().bucket(bucket).build())) {
            client.makeBucket(MakeBucketArgs.builder().bucket(bucket).build());
        }
    }

    @Override
    public void put(String objectKey, Path ciphertext) {
        try (InputStream input = Files.newInputStream(ciphertext)) {
            client.putObject(PutObjectArgs.builder().bucket(bucket).object(objectKey)
                    .stream(input, Files.size(ciphertext), -1)
                    .contentType("application/octet-stream").build());
        } catch (Exception ex) {
            throw new IllegalStateException("Encrypted object storage failed", ex);
        }
    }

    @Override
    public InputStream open(String objectKey) {
        try {
            return client.getObject(io.minio.GetObjectArgs.builder().bucket(bucket).object(objectKey).build());
        } catch (Exception ex) {
            throw new IllegalStateException("Encrypted object read failed", ex);
        }
    }

    @Override
    public void delete(String objectKey) {
        try {
            client.removeObject(RemoveObjectArgs.builder().bucket(bucket).object(objectKey).build());
        } catch (Exception ex) {
            throw new IllegalStateException("Encrypted object removal failed", ex);
        }
    }
}
