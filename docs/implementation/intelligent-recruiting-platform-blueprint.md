# 智聘云智能招聘平台开发蓝图

> 文档状态：`DECIDED`  
> 修订日期：2026-09-13  
> 项目模式：Hybrid  
> 最终判定：`IMPLEMENTABLE`

本文件是稳定入口。完整蓝图已拆分到 `docs/implementation/zhiyun/`，开发、评审和验收均以拆分文档为准：

1. [总览与实施导航](zhiyun/00-index.md)
2. [需求与验收标准](zhiyun/01-requirements.md)
3. [当前代码与部署现状](zhiyun/02-current-state.md)
4. [目标架构与模块边界](zhiyun/03-architecture.md)
5. [API、事件与数据契约](zhiyun/04-contracts-and-data.md)
6. [有序实施计划](zhiyun/05-implementation-plan.md)
7. [验证、发布与回滚](zhiyun/06-verification-release.md)

核心发布决策保持不变：本地完成开发和验证，本地构建 `linux/amd64` Docker 镜像，使用 `docker save` 导出镜像归档，通过 SSH/SCP/SFTP 把镜像和部署文件传到 Linux 服务器；服务器只执行校验、`docker load`、迁移和 Docker Compose 启动，不接收源代码，也不构建镜像。

生产启用前必须关闭文档中列出的阻塞项，包括真实身份认证、数据库迁移、可靠消息链路、敏感信息保护、真实 AI Provider、备份回滚演练及邮件/日历等业务政策确认。
