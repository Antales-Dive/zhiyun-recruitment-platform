# 智聘云开发蓝图：当前代码与部署现状

## 1. 证据范围

本文件只描述 2026-09-13 工作区中的可见代码和脚本。项目目录当前不是 Git 仓库，因此无法验证提交历史、分支策略或变更归属；这不影响代码现状判断，但发布版本不能暂时依赖 Git Commit SHA。

## 2. 当前目录

```text
apps/
  api/
    app/
      api/auth.py
      api/v1/routes.py
      api/v1/knowledge_routes.py
      domains/knowledge/
      domains/matching/
      infrastructure/db.py
      infrastructure/models.py
      infrastructure/model_gateway.py
      workers/main.py
      workers/resume_worker.py
      workers/knowledge_worker.py
    tests/
    Dockerfile
    Dockerfile.worker
    requirements.txt
  web/
    src/main.jsx
    src/styles.css
    nginx.conf
    Dockerfile
    package.json
deploy/compose/compose.lite.yml
scripts/build-release.ps1
scripts/deploy.sh
docs/implementation/
.env.example
README.md
```

当前代码已包含 Alembic 迁移、身份与审计、问卷、排期、通知、面试和 Worker 模块；前端仍以现有页面结构承载这些能力，尚未按 feature 目录拆分。

## 3. 运行入口与依赖

| 区域 | 当前入口/依赖 | 状态 |
| --- | --- | --- |
| API | `uvicorn app.main:app --app-dir apps/api` | `VERIFIED` |
| Worker | `python -m app.workers.main` | `VERIFIED` |
| Web | Vite 开发服务器；生产由 Nginx 服务静态文件并反代 API | `VERIFIED` |
| Python | Python 3.12 镜像；FastAPI、SQLAlchemy、PyMySQL、pypdf、python-docx、httpx | `VERIFIED` |
| Web | React 18、Vite 5、JavaScript JSX | `VERIFIED` |
| 数据库 | 本地默认 SQLite；Compose 使用 MySQL 8.4 | `VERIFIED` |
| 基础设施 | Worker 使用 RabbitMQ 的 Outbox/Consumer 链路；Redis 已声明并探活，但当前业务不依赖 Redis | `VERIFIED` |

## 4. 当前 HTTP 能力

### 4.1 招聘 API

`apps/api/app/api/v1/routes.py` 已提供：

- `POST /api/v1/jobs`：创建基础岗位；
- `GET /api/v1/jobs`：岗位列表；
- `POST /api/v1/candidates/import`：上传单份简历，写本地文件，创建候选人和任务；
- `GET /api/v1/tasks/{task_id}`：任务快照；
- `GET /api/v1/tasks/{task_id}/events`：只发送一次事件的 SSE 响应；
- `GET /api/v1/candidates`：候选人列表；
- `GET /api/v1/dashboard`：基础计数。

当前限制：

- `VERIFIED` 岗位与评分规则已经通过版本表保存；基础岗位表仍保留当前活动版本指针。
- `VERIFIED` 简历 SHA-256 唯一约束按组织和候选人文件范围处理，不向其他组织暴露重复存在性。
- `VERIFIED` 简历和知识文档上传会检查大小、扩展名、MIME 与文件签名，并使用安全存储键。
- `VERIFIED` SSE 不保持连接、不读取 `Last-Event-ID`、不补发事件。
- `VERIFIED` 请求 envelope 的 `request_id` 和 `trace_id` 固定为 `"local"`。
- `VERIFIED` 招聘数据带 `org_id`，并在路由和领域服务中执行组织过滤；面试官详情访问还需要有效分配记录。

### 4.2 知识与助手 API

`apps/api/app/api/v1/knowledge_routes.py` 已提供：

- `POST /api/v1/knowledge/documents`：管理员上传文档并创建初始版本和摄取任务；
- `GET /api/v1/knowledge/tasks/{task_id}`：查询摄取状态；
- `POST /api/v1/knowledge/documents/{document_id}/publish`：发布最新已索引版本；
- `POST /api/v1/assistant/query`：检索、拼接原文并返回引用。

可复用能力：

- `VERIFIED` 支持 TXT、Markdown、DOCX、带文本层 PDF 的解析；
- `VERIFIED` 图片和无文本 PDF 进入 `NEEDS_OCR`；
- `VERIFIED` 文档分块保存章节、页码、顺序和内容哈希；
- `VERIFIED` 发布前不可检索；发布时旧 `PUBLISHED` 版本改为 `SUPERSEDED`；
- `VERIFIED` 检索按 `org_id`、发布状态和角色过滤；
- `VERIFIED` 回答记录查询、引用、排名和得分；
- `VERIFIED` 无命中时返回 `NO_RELIABLE_EVIDENCE`。

当前限制：

- `VERIFIED` 上传 API 每次新建 `KnowledgeDocument`，没有给现有文档新增版本的接口。
- `VERIFIED` 分块按字符上限切分，不是 Parent/Child Token 分块。
- `VERIFIED` 检索是中英文关键词重合评分，不包含 Embedding、向量库、BM25、RRF 或 Rerank。
- `VERIFIED` “回答”是命中 Chunk 原文拼接，不调用生成模型。
- `VERIFIED` 角色 ACL 兼容 JSON 存储，并通过知识版本、索引清单和审计记录约束发布。
- `VERIFIED` 已有索引提交、版本发布、撤回和重建流程；当前 Dense Provider 未配置时明确降级为空 Dense 结果，不伪造向量。
- `VERIFIED` OCR 仅标记状态，没有真实 OCR Provider。

## 5. 当前 Worker 与一致性

`VERIFIED` Worker 是 RabbitMQ Consumer，并配合事务 Outbox、发布确认、重试队列、Consumer Ledger 和 DLQ 工作。

`VERIFIED` 任务通过条件更新从 `PENDING/RETRY_WAIT` 改为 `PROCESSING`，具备抢占、尝试记录、重试和永久失败状态；仍需持续完善跨服务告警和运营看板。

## 6. 当前简历解析与匹配

`apps/api/app/workers/resume_worker.py`：

- 将文件字节以 UTF-8 `errors="ignore"` 解码；
- 使用第一行作为候选人姓名；
- 按岗位技能字符串是否出现计算技能分；
- 通过“年经验/年工作/年开发”判断经历分；
- 按文本长度计算完整度；
- 按 40%/40%/20% 确定性加权；
- 按 80/60/40 分流。

`VERIFIED` 该逻辑可作为规则边界测试的起点，但不是完整简历解析、LangGraph 三 Agent、可信证据抽取或 AI 匹配实现。PDF、DOCX 和图片即使能上传，也不会在简历 Worker 中正确解析。

## 7. 当前身份、审计与 AI Provider

### 7.1 身份

`apps/api/app/api/auth.py` 信任 `X-User-Role` 和 `X-Org-Id`。开发环境缺省为 `ADMIN/default`。

`VERIFIED` 这只适用于本地开发：

- 已有用户、密码、会话和会话撤销；SSO 尚未接入；
- 已有组织级 RBAC、候选人分配检查和面试官资源级授权；
- 招聘 API 和事实表带有组织字段；
- 前端请求未显式发送真实认证凭据。

### 7.2 审计与 PII

`VERIFIED` 已有审计表、人工决定审计和日志 PII 脱敏；文件通过存储键解析，任务错误保留截断后的摘要。下载审计与统一外部身份集成仍需按部署环境补齐。

### 7.3 Model Gateway

`apps/api/app/infrastructure/model_gateway.py` 有一个 OpenAI-compatible `chat` 适配器雏形。

`VERIFIED` Chat Provider 缺失时返回 `MODEL_NOT_CONFIGURED`，匹配任务进入人工复核；助手只返回授权证据摘要，不生成演示答案。Embedding/Rerank Provider 尚未配置时保留明确降级语义。

## 8. 当前数据结构

`VERIFIED` SQLAlchemy 模型包含：

```text
jobs
candidates
candidate_files
tasks
match_results
knowledge_documents
knowledge_versions
knowledge_chunks
knowledge_ingestion_tasks
assistant_query_logs
assistant_citations
```

`VERIFIED` 生产启动不自动建表，数据库由 Alembic 迁移管理；当前 head 为 `0012_model_run_org`。迁移前备份和发布脚本健康门已接入，数据库回滚仍需遵循兼容版本策略。

## 9. 当前前端

`apps/web/src/main.jsx` 是单文件管理台，包含：

- 仪表盘计数；
- 岗位创建表单；
- 简历上传表单；
- 最近候选人列表；
- 静态侧栏导航。

`VERIFIED` 多数导航项没有路由和业务页面；没有登录、权限态、知识管理、助手、任务持续更新、问卷、排期、面试或审计 UI。当前为 JavaScript，不是蓝图目标中的 TypeScript。

## 10. 当前测试证据

`VERIFIED` 当前共有 11 个 Python `unittest` 用例，覆盖：

- 健康检查、岗位创建、简历导入和 SSE 格式；
- 知识发布控制、引用回答和角色过滤；
- 匹配加权与 80/60/40 边界；
- 单个简历任务处理。

此前运行证据：

```powershell
$env:PYTHONPATH = "apps/api"
python -m unittest discover -s apps/api/tests -p "test_*.py" -v
python -m compileall apps/api/app
cd apps/web
npm run build
docker compose -f deploy/compose/compose.lite.yml config
```

`VERIFIED` 当时结果为后端 11/11 通过、Python 编译通过、前端构建通过、Compose 配置校验通过。该结果只证明当前基线，不证明目标功能已完成。

## 11. 当前部署现状

### 11.1 已有内容

- `VERIFIED` API、Worker、Web Dockerfile；
- `VERIFIED` `compose.lite.yml` 运行 MySQL、Redis、RabbitMQ、API、Worker、Web；
- `VERIFIED` `build-release.ps1` 通过 Buildx 构建 `linux/amd64`，`docker save` 后 gzip；
- `VERIFIED` 发布包包含镜像、Compose、`.env.example`、`deploy.sh`、单页蓝图和 `SHA256SUMS`；
- `VERIFIED` `deploy.sh` 校验摘要、`docker load`、Compose 启动、查看容器和调用健康接口。

### 11.2 缺口

- `VERIFIED` Compose 已配置 API/Worker 的 RabbitMQ 连接变量，Redis 当前仅作基础设施探活。
- `VERIFIED` MySQL、Redis、RabbitMQ、API 均有健康检查，服务间依赖健康状态启动。
- `VERIFIED` 各服务已配置 CPU/内存上限和日志轮转；只读文件系统、TLS 终止和安全上下文仍依赖生产编排平台。
- `VERIFIED` 发布脚本通过 API 镜像执行 Alembic 迁移。
- `VERIFIED` `deploy.sh` 使用 `docker load < images/*.tar.gz`，需在目标 Linux Docker 版本上验证是否接受 gzip 流。
- `VERIFIED` `curl ... || true` 会吞掉健康失败，当前部署不会因 Smoke Test 失败而停止或回滚。
- `VERIFIED` 发布脚本包含数据库/上传文件备份、版本激活指针、摘要校验和健康门失败后的应用版本恢复；数据库降级不自动执行，需按迁移兼容策略处理。
- `VERIFIED` 构建脚本只复制旧的单页蓝图，不包含新的拆分文档。
- `VERIFIED` `.env.example` 包含数据库、RabbitMQ、模型和会话配置模板；密码含特殊字符时应使用 URL 编码的显式连接串，邮件和日历 Provider 仍需按部署环境配置。

## 12. 可复用与必须替换

可复用：

- FastAPI、SQLAlchemy、React/Vite、Nginx 和 Docker 基线；
- 统一 envelope 方向；
- 文件 SHA-256、任务状态、匹配纯函数；
- 知识文档/版本/Chunk/引用基础实体；
- 显式发布、组织与角色过滤、拒答行为；
- `linux/amd64` 本地构建和发布包方向。

必须先替换或强化：

- 自动建表、请求头身份、数据库轮询 Worker；
- 演示模型返回、UTF-8 简历解析、单次 SSE；
- 全局文件去重、缺少组织隔离和审计；
- 关键词检索作为最终 RAG；
- 无健康门、无备份、无回滚的发布脚本。

完整差距按依赖顺序映射到 [05-implementation-plan.md](05-implementation-plan.md)。
