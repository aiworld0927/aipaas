package com.aipaas.platform.common.api;

import java.util.List;

/** 统一分页结构：{@code {items,total,page,pageSize}}。 */
public record PageResult<T>(List<T> items, long total, int page, int pageSize) {

    public static <T> PageResult<T> of(List<T> items, long total, int page, int pageSize) {
        return new PageResult<>(items, total, page, pageSize);
    }
}
