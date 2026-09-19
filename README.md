# 智聘云智能招聘平台

面向企业 HR 的 AI 招聘流程平台。当前已实现岗位管理、简历上传、异步解析、候选人匹配，以及企业知识库 RAG 的第一阶段闭环。

## 当前 RAG 能力

- 管理员上传 TXT、Markdown、DOCX、PDF 或图片制度文档；
- 后台 Worker 异步解析文档并生成带章节、页码和内容摘要的分块；
- 文档完成索引后由管理员显式发布，未发布版本不会参与检索；
- 查询时按组织和角色过滤，管理员可以审计查询记录与引用；
- 回答返回文档标题、版本、章节、页码、摘录和检索分数；
- 没有可靠证据时返回 `NO_RELIABLE_EVIDENCE`，不根据常识补写制度；
- 图片和无文本层 PDF 当前进入 `NEEDS_OCR`，等待后续 PaddleOCR 适配器处理。

当前检索实现是可解释的关键词基线，尚未接入 Embedding、Milvus、Rerank 和生成模型。对应接口已经保持独立边界，后续替换检索实现不会改变上传、发布和问答 API。

核心接口：

```text
POST /api/v1/knowledge/documents
GET  /api/v1/knowledge/tasks/{task_id}
POST /api/v1/knowledge/documents/{document_id}/publish
POST /api/v1/assistant/query
```

## 本地运行

```powershell
$env:PYTHONPATH = "apps/api"
python -m unittest discover -s apps/api/tests -p "test_*.py" -v
uvicorn app.main:app --app-dir apps/api --reload --port 8000
```

前端开发环境：

```powershell
cd apps/web
npm install
npm run dev
```

访问 `http://localhost:5173`。开发环境默认允许请求，不代表生产认证配置。

## 本地构建并传输到 Linux

本项目采用本地构建镜像、直接传输到 Linux 的发布方式：

```powershell
.\scripts\build-release.ps1 -Version 0.1.0
scp -r .\release\0.1.0 user@server:/opt/zhiyun/releases/
ssh user@server "bash /opt/zhiyun/releases/0.1.0/deploy.sh /opt/zhiyun/releases/0.1.0"
```

构建脚本显式生成 `linux/amd64` 镜像，服务器不会接收源代码，也不会在服务器上构建镜像。服务器上的 `.env` 必须手动填写真实密码，不能从 `.env.example` 直接用于生产。

详细架构、数据模型、接口、验收和部署约束见 [开发蓝图](docs/implementation/intelligent-recruiting-platform-blueprint.md)。
