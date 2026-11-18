---
name: 缺陷报告
about: 报告可复现的功能、性能、安全控制或部署问题
title: "fix: <简要描述问题>"
labels: bug
assignees: ""
---

> 安全漏洞请勿公开提交，改用 SECURITY.md 中的私密报告渠道。提交前请删除口令、Token、Cookie、私钥和敏感业务数据。

## 问题描述

<!-- 实际行为是什么？影响哪些用户或链路？ -->

## 影响模块

<!-- 例如：contracts / platform-api / ingest-worker / query-worker / pipeline / retrieval / generation / web / deploy / docs -->

- 模块：
- 文件、接口或页面：
- 提交 SHA / 版本：

## 复现步骤

1.
2.
3.

## 期望行为

## 实际行为

## 运行环境

- OS：
- 部署方式：Mock / P0 Compose / 开发全量 Compose / 本地进程 / 离线验收
- Java / Python / Node / Docker 版本（按涉及范围填写）：
- CPU 或 GPU：

## 日志与证据

<!-- 粘贴脱敏后的最小日志、TraceID、测试输出或截图。不要上传真实密钥和业务文档。 -->

```text

```

## 初步定位与可能原因

<!-- 可选。已排除哪些原因？ -->

## 验收建议

<!-- 修复后如何证明问题消失且没有回归？ -->

- [ ] 已提供最小可复现步骤
- [ ] 已确认没有包含敏感信息
- [ ] 已检索现有 Issue，未发现相同问题
