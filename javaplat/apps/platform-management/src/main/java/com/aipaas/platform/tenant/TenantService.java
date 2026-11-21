package com.aipaas.platform.tenant;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Service
public class TenantService {

    private static final Set<String> STATUSES = Set.of("ACTIVE", "DISABLED");

    private final TenantRepository tenants;

    public TenantService(TenantRepository tenants) {
        this.tenants = tenants;
    }

    public Map<String, Object> list() {
        List<Map<String, Object>> items = tenants.list().stream().map(this::view).toList();
        return Map.of("items", items, "total", items.size());
    }

    @Transactional
    public Map<String, Object> create(String id, String name, String description) {
        if (id == null || !id.matches("[a-z][a-z0-9-]{1,31}")) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST,
                    "租户标识需为 2..32 位小写字母、数字或连字符");
        }
        if (name == null || name.isBlank() || name.trim().length() > 128) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "租户名称长度需为 1..128");
        }
        if (tenants.find(id).isPresent()) {
            throw new ApiException(ErrorCodes.BIZ_DUPLICATE, HttpStatus.CONFLICT, "租户标识已存在");
        }
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        tenants.create(id, name.trim(), description, now);
        tenants.provision(id, now);
        return view(tenants.find(id).orElseThrow());
    }

    @Transactional
    public Map<String, Object> update(String id, String name, String status, String description) {
        TenantRepository.TenantRow current = tenants.find(id)
                .orElseThrow(() -> ApiException.notFound("租户不存在"));
        String finalStatus = status == null ? current.status() : status;
        if (!STATUSES.contains(finalStatus)) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "租户状态取值不合法");
        }
        if ("default".equals(id) && !"ACTIVE".equals(finalStatus)) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "默认租户不可停用");
        }
        String finalName = name == null ? current.name() : name.trim();
        if (finalName.isEmpty() || finalName.length() > 128) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "租户名称长度需为 1..128");
        }
        String finalDescription = description == null ? current.description() : description;
        tenants.update(id, finalName, finalStatus, finalDescription, OffsetDateTime.now(ZoneOffset.UTC));
        return view(tenants.find(id).orElseThrow());
    }

    private Map<String, Object> view(TenantRepository.TenantRow row) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("id", row.id());
        data.put("name", row.name());
        data.put("status", row.status());
        data.put("description", row.description());
        data.put("userCount", tenants.countUsers(row.id()));
        data.put("createdAt", row.createdAt());
        data.put("updatedAt", row.updatedAt());
        return data;
    }
}
