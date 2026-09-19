# Agent 业务架构与技术设计面试指南

> 项目：智聘云智能招聘平台  
> 文档用途：面试复习  
> 更新时间：2026-09-18  
> 重点：掌握 Agent 部分的业务目标、核心链路、技术实现和企业级设计

## 1. 一句话介绍

项目中的 Agent 不是一个简单的聊天机器人，而是招聘流程中的智能分析组件。

核心设计是：

```text
大模型负责理解自然语言、提取结构化证据
程序负责校验、计算分数和执行业务规则
系统负责异步调度、持久化、审计和人工复核
```

以简历匹配为例，系统不会让大模型直接决定录用或淘汰，而是让多个专业 Agent 分析简历，再由确定性评分器完成匹配计算和流程分流。

## 2. Agent 解决的业务问题

HR 上传候选人简历后，系统需要判断：

1. 候选人的技能是否满足岗位要求；
2. 候选人的工作和项目经历是否符合岗位职责；
3. 候选人应该进入面试、问卷、人才库，还是人工复核；
4. 这个判断是否有足够证据，能否被 HR 和审计人员追溯。

如果直接让模型输出“匹配度 85 分，建议面试”，会有几个问题：

- 同一份简历重复分析时结果可能波动；
- 很难解释分数来源；
- 模型可能输出非法格式或编造证据；
- 岗位规则修改后，历史结果可能发生漂移；
- 模型可能使用年龄、性别、婚育等敏感属性；
- Provider 超时或未配置时，容易被错误地填充成一个假分数。

因此系统采用“AI 提取证据，规则决定结果”的设计。

## 3. 总体业务架构

```text
HR 上传简历
    |
    v
简历解析，生成 ResumeVersion 和 ResumeBlock
    |
    v
HR 触发匹配分析
    |
    v
创建 AnalysisRun、Task 和 OutboxEvent
    |
    v
RabbitMQ 投递 analysis.requested
    |
    v
Matching Worker 消费事件
    |
    v
basic / skills / experience 三个 Agent 并行分析
    |
    v
Schema 校验和 evidence_refs 校验
    |
    v
Supervisor 检查结果完整性
    |
    v
DeterministicScorer 按 RubricVersion 计算分数
    |
    v
置信度门控
    |
    +--> INTERVIEW
    +--> QUESTIONNAIRE
    +--> TALENT_POOL
    +--> NEEDS_REVIEW
    +--> CLOSED
```

当前实现中，简历解析完成后不会自动触发匹配。简历解析和匹配分析是两个独立阶段，匹配由 HR 通过分析接口显式触发。

## 4. Matching Agent 架构

### 4.1 三个专业 Agent

系统当前包含三个匹配 Agent：

| Agent | 职责 |
| --- | --- |
| `basic_agent` | 分析基础信息和岗位基础要求 |
| `skills_agent` | 分析技能与岗位技能要求的重合度和深度 |
| `experience_agent` | 分析工作经历、项目经历和岗位职责的契合度 |

三个 Agent 的分析维度相互独立，因此通过 LangGraph 并行执行。

这么拆分的原因：

1. 单个 Agent 的职责更清晰，Prompt 更容易约束；
2. 三个维度可以并行，降低整体响应时间；
3. 某个维度失败时，可以准确定位故障；
4. 后续可以独立替换某个 Agent 的 Prompt、模型或评估集。

### 4.2 LangGraph 流程

```text
START
  |
  +--> basic_agent
  |
  +--> skills_agent
  |
  +--> experience_agent
          |
          v
  supervisor_validate
          |
          v
  deterministic_score
          |
          v
         END
```

LangGraph 在这里主要解决流程编排问题：

- 将 Agent 封装成图节点；
- 使用共享 State 保存中间结果；
- 合并三个并行 Agent 的输出；
- 在评分之前执行统一校验；
- 为后续增加重试、分支或人工复核节点预留空间。

LangGraph 不负责业务数据持久化，也不负责消息可靠投递。任务可靠性由 MySQL、Outbox、RabbitMQ、Task 和 Consumer Ledger 共同保证。

## 5. Agent 输入设计

Agent 的输入不是“随时查询最新数据库数据”，而是一次分析对应的一组版本快照：

```text
job_version
rubric_version
resume_version
```

代码中使用 `AnalysisSnapshot` 表示最小必要输入：

```python
AnalysisSnapshot(
    job_version_id,
    rubric_version_id,
    resume_version_id,
    job_title,
    job_description,
    requirements,
    resume_blocks,
)
```

简历会被解析成多个带 ID 的文本块：

```text
block-1：技能
block-2：工作经历
block-3：项目经历
```

这样设计有三个作用：

1. 控制进入模型的上下文范围；
2. 让 Agent 能够返回可定位的证据；
3. 让历史分析可以基于原始版本重放。

## 6. Agent 输出设计

每个 Agent 必须返回结构化 JSON，而不是自然语言段落：

```json
{
  "dimension": "skills",
  "raw_score": 85,
  "confidence": 0.9,
  "evidence_refs": ["resume-block-id"],
  "missing_fields": [],
  "reason_codes": []
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `dimension` | 当前 Agent 分析的维度 |
| `raw_score` | 该维度的原始分数，范围 0 到 100 |
| `confidence` | 模型对该判断的置信度，范围 0 到 1 |
| `evidence_refs` | 判断所依据的简历块 ID |
| `missing_fields` | 缺少的关键信息 |
| `reason_codes` | 结果原因编码，便于审计和统计 |

系统会依次校验：

1. 返回内容是不是合法 JSON；
2. 是否符合 Pydantic Schema；
3. `dimension` 是否和当前 Agent 一致；
4. 分数和置信度是否在合法范围内；
5. `evidence_refs` 是否引用了真实存在的简历块。

任何一项校验失败，结果都不会直接进入普通分流，而是进入 `NEEDS_REVIEW`。

## 7. 为什么必须保存证据引用

模型输出“技能匹配度 90 分”本身不具备足够的业务价值。

系统还必须回答：

```text
这个 90 分来自简历中的哪段内容？
```

因此 `evidence_refs` 必须引用真实的 `ResumeBlock.id`。

如果模型返回不存在的引用：

```json
{
  "evidence_refs": ["not-exist-block"]
}
```

系统会判定为非法结果，进入人工复核，而不是继续计算分数。

这可以防止模型编造来源，也方便 HR 查看分析依据。

## 8. Supervisor 的职责

Supervisor 不是另一个负责重新打分的模型。

当前 Supervisor 主要检查：

- 三个 Agent 是否都成功返回结果；
- 是否缺少某个分析维度；
- 是否存在 Agent 错误；
- 是否提供了证据引用；
- 是否可以安全进入确定性评分阶段。

Supervisor 不负责修改：

- Agent 的分数；
- 评分权重；
- 分流阈值。

面试时可以这样说：

> Supervisor 更像一个质量门和流程控制器，而不是第二个评分器。它负责判断 Agent 结果是否完整、是否有证据、是否满足进入评分阶段的条件。

## 9. 确定性评分与分流

默认 Rubric 配置如下：

```text
basic      20%
skills     40%
experience 40%
```

计算公式：

```text
total_score =
    basic_score      * 0.2
  + skills_score     * 0.4
  + experience_score * 0.4
```

默认分流阈值：

| 总分 | 分流结果 |
| --- | --- |
| `>= 80` | `INTERVIEW` |
| `>= 60` | `QUESTIONNAIRE` |
| `>= 40` | `TALENT_POOL` |
| `< 40` | `CLOSED` |

实际权重和阈值来自岗位绑定的 `RubricVersion`，不是写死在 Prompt 中。

这意味着：

```text
LLM 负责自然语言理解
Rubric 负责业务配置
DeterministicScorer 负责计算和路由
```

这样能够保证：

- 分数稳定；
- 权重可测试；
- 阈值可解释；
- 模型升级不会直接改变业务规则；
- 不同岗位可以使用不同的评分规则。

## 10. 置信度门控

每个 Agent 都会返回 `confidence`，系统基于多个 Agent 的置信度计算整体置信度。

如果整体置信度低于 `RubricConfig.confidence_threshold`，就强制进入人工复核：

```text
score 很高，但 confidence 很低
    |
    v
NEEDS_REVIEW
```

需要区分两个概念：

```text
score      = 候选人和岗位的匹配程度
confidence = 模型对当前判断的可靠程度
```

高分不等于高可靠。如果简历信息不完整，模型即使给出了高分，也可能必须人工复核。

## 11. 异步任务和消息架构

匹配包含多个模型调用，不适合一直占用 HTTP 请求。

API 只负责创建任务并快速返回：

```text
创建 AnalysisRun
创建 Task
写入 OutboxEvent
返回 202 Accepted
```

Worker 负责真正执行：

```text
RabbitMQ
    |
    v
Matching Consumer
    |
    v
加载岗位、规则和简历版本
    |
    v
运行 LangGraph
    |
    v
保存 AgentRun、AnalysisRun、RoutingDecision
```

涉及的核心对象：

| 对象 | 作用 |
| --- | --- |
| `AnalysisRun` | 表示一次完整的匹配分析 |
| `AgentRun` | 保存每个 Agent 的分数、置信度和证据 |
| `Task` | 记录异步任务状态、进度和错误 |
| `OutboxEvent` | 保证业务事务和待投递事件一致提交 |
| `RoutingDecision` | 记录候选人状态变化和分流原因 |
| `ModelRun` | 保存 Provider、模型、Prompt 版本和错误元数据 |
| `PromptVersion` | 管理 Prompt 版本和模板哈希 |

## 12. 失败处理

可能出现的失败包括：

- Provider 未配置；
- 模型请求超时；
- Provider 不可用；
- 认证失败；
- 模型返回非法 JSON；
- 返回的维度错误；
- 返回的分数越界；
- 证据引用不存在；
- Agent 结果不完整；
- 置信度过低。

系统不会用默认分数或随机分数掩盖失败，而是将其归一化为明确错误码，例如：

```text
MODEL_NOT_CONFIGURED
TIMEOUT
UNAVAILABLE
AUTH_FAILED
INVALID_RESPONSE
MISSING_EVIDENCE
LOW_CONFIDENCE
```

业务上通常进入：

```text
NEEDS_REVIEW
```

这符合招聘系统的高风险业务特征：宁可让 HR 复核，也不能生成一个看起来正常但实际上没有依据的结果。

## 13. 企业级设计重点

### 13.1 版本化

一次分析绑定：

```text
job_version_id
rubric_version_id
resume_version_id
prompt_version
```

岗位、规则或 Prompt 后续发生变化，不会影响已经完成的历史分析。

### 13.2 幂等

分析接口支持 `Idempotency-Key`，避免用户重复点击创建多次相同分析。

消息消费者还需要基于事件 ID 做幂等，因为 RabbitMQ 通常是至少一次投递。

### 13.3 Outbox

业务数据和 Outbox 事件在同一个数据库事务中提交：

```text
业务状态写入成功
    +
事件记录写入成功
```

这样可以避免“任务已经创建，但消息没有成功投递”的不一致问题。

### 13.4 人工复核

以下情况进入人工复核：

- 模型没有配置；
- Agent 调用失败；
- 输出格式错误；
- 证据无效；
- 证据不足；
- 置信度过低。

### 13.5 敏感属性保护

Prompt 明确禁止使用以下信息作为匹配依据：

```text
年龄、性别、婚育、民族、宗教、政治面貌等
```

同时，评分规则不应将这些字段设计为业务维度。高影响招聘决定也不应由 Agent 直接完成。

### 13.6 审计

系统保存：

- Agent 各维度结果；
- 简历证据引用；
- 总分和分流结果；
- 规则版本；
- Prompt 版本；
- Provider 和模型元数据；
- 候选人状态变化；
- 人工复核结果。

## 14. AI 文字面试 Agent

项目中还有一类在线面试 Agent，它和简历匹配 Agent 的关注点不同。

### 14.1 面试链路

```text
候选人发送消息
    |
    v
先持久化候选人消息
    |
    v
检查消息幂等和会话状态
    |
    v
Interview Graph 读取岗位、题目计划和对话历史
    |
    v
模型生成下一步动作
    |
    +--> question
    +--> follow_up
    +--> wrap_up
    |
    v
校验输出和敏感问题
    |
    v
保存 AI 消息并通过 SSE 返回
```

面试 Agent 的结构化输出示例：

```json
{
  "action": "follow_up",
  "content": "你刚才提到负责过接口性能优化，可以具体介绍优化前后的指标吗？",
  "done": false
}
```

### 14.2 面试 Agent 的企业级控制

- 候选人消息通过 `client_message_id` 幂等；
- 面试会话使用状态机；
- 候选人通过短期令牌访问；
- 面试官可以人工接管；
- 接管通过 `state_version` 乐观锁处理并发；
- 人工接管后，未提交的 AI 回复必须失效；
- AI 只生成报告草稿；
- 报告需要面试官审核后才能进入招聘决策材料。

面试匹配 Agent 的区别：

| 类型 | 核心问题 |
| --- | --- |
| 简历匹配 Agent | 如何从简历中提取证据并进行可解释评分 |
| 面试 Agent | 如何基于上下文生成下一轮问题并支持人工接管 |

## 15. RAG Assistant 与 Agent 的关系

项目中的 RAG 更准确地说是“受权限约束的 AI Assistant”，不是自主规划型 Agent。

主要链路：

```text
用户身份和角色
    |
    v
组织过滤、版本过滤、ACL 过滤
    |
    v
关键词召回 + 向量召回
    |
    v
RRF 融合
    |
    v
Rerank
    |
    v
证据门槛检查
    |
    v
模型生成带引用回答
    |
    v
引用 ID 校验
```

最重要的安全原则是：

```text
先授权过滤，再把内容送给模型
```

不能先把全库文本发送给模型，最后再尝试过滤答案。未授权内容必须在检索阶段就被排除。

## 16. 关键代码位置

### Matching

- Agent Prompt 和输出解析：`apps/api/app/domains/matching/agents.py`
- LangGraph 编排：`apps/api/app/domains/matching/graph.py`
- 确定性评分：`apps/api/app/domains/matching/scoring.py`
- Agent 输入输出契约：`apps/api/app/contracts/matching.py`
- 匹配异步消费者：`apps/api/app/workers/matching_consumer.py`
- 分析接口：`apps/api/app/api/v1/analysis_routes.py`

### Model Provider

- Chat Provider 适配：`apps/api/app/infrastructure/model_gateway.py`
- Embedding/Rerank Port：`apps/api/app/infrastructure/ai/ports.py`
- OpenAI-compatible Embedding：`apps/api/app/infrastructure/ai/openai_compatible.py`

### AI Interview

- 面试图：`apps/api/app/orchestration/interview_graph.py`
- 面试会话状态机：`apps/api/app/domains/interviews/service.py`
- 面试接口：`apps/api/app/api/v1/interview_routes.py`

### RAG

- RAG 检索链：`apps/api/app/domains/assistant/service.py`
- Assistant 接口：`apps/api/app/api/v1/assistant_routes.py`

## 17. 面试 30 秒回答

> 我们的 Agent 主要服务于简历匹配和 AI 文字面试。以简历匹配为例，系统不会让大模型直接决定录用或淘汰，而是拆成基础信息、技能和经历三个专业 Agent，分别基于岗位版本、规则版本和简历版本提取结构化证据，每个结果都包含分数、置信度和简历证据引用。三个 Agent 通过 LangGraph 并行编排，之后由 Supervisor 做完整性和证据校验，最后由确定性评分器按照岗位绑定的 Rubric 计算总分并进行流程分流。模型异常、证据无效或置信度不足时进入人工复核。整个过程通过异步 Worker、Outbox、RabbitMQ 和版本化数据保证可靠性、幂等性和可追溯性。

## 18. 高频追问回答

### 问：为什么不用一个大模型直接输出最终结果？

> 因为招聘属于高影响业务。直接让模型输出最终结论会带来不稳定、不可解释和难以审计的问题。我们让模型负责自然语言理解和证据提取，把权重、阈值和流程路由交给确定性代码，同时保留人工复核入口。

### 问：为什么拆成三个 Agent？

> 三个维度职责不同且相互独立，可以并行执行，降低延迟；同时每个 Agent 都有独立的结构化输出和失败边界，方便定位和评估。

### 问：LangGraph 解决什么问题？

> LangGraph 负责有状态的 Agent 流程编排，包括并行节点、状态合并、校验节点和后续分支。它不替代数据库事务、消息队列和业务规则。

### 问：模型返回非法 JSON 怎么处理？

> 先做 JSON 解析，再做 Pydantic Schema 校验，并检查维度、分数范围和证据引用。任意一层失败都会进入人工复核，不会生成普通分流结果。

### 问：如何保证历史结果不受岗位修改影响？

> 分析运行绑定具体的岗位版本、规则版本、简历版本和 Prompt 版本，历史分析读取的是不可变快照，而不是当前最新数据。

### 问：如何防止模型使用性别、年龄等敏感信息？

> Prompt 明确禁止使用敏感属性，评分规则也不把这些字段设计成评分维度。对于生产环境，还需要通过公平性评估集、人工抽样和审计指标持续验证。

### 问：为什么要使用异步 Worker？

> 匹配包含多个模型调用，耗时和失败概率都高于普通请求。API 快速创建任务并返回 202，Worker 异步执行，便于重试、监控、进度查询和故障恢复。

### 问：如何保证消息不会重复造成副作用？

> 使用事务 Outbox 保证业务状态和事件记录一致提交，消费者基于事件 ID 做幂等。因为消息系统通常是至少一次投递，所以不能依赖恰好一次语义。

### 问：RAG 和 Agent 有什么区别？

> RAG 主要解决企业知识检索和带引用生成，重点是 ACL、版本、证据和拒答；Matching Agent 重点是多步骤分析和确定性业务路由；Interview Agent 重点是上下文对话和人工接管。

## 19. 当前实现与后续增强的边界

### 当前代码已经体现的能力

- 三个 Matching Agent 的 LangGraph 编排；
- 结构化 JSON 输出；
- Pydantic Schema 校验；
- 简历块证据引用校验；
- 确定性评分和分流；
- 置信度门控；
- 异步 Task 和 RabbitMQ 事件；
- `AnalysisRun`、`AgentRun`、`RoutingDecision` 持久化；
- Provider 未配置时进入人工复核；
- AI 面试状态机、消息幂等和人工接管；
- RAG 的组织隔离、角色 ACL 和引用记录。

### 面试时不要说成已经完全完成的内容

- 真实生产 Provider 的模型质量和容量；
- PromptVersion 表与所有 Prompt 的完整动态管理；
- 完整的跨 Agent 冲突检测；
- 生产级公平性指标和大规模评估基线；
- 多 Worker 高可用和大规模向量基础设施；
- 所有外部 Provider 的预算、限流和合规策略。

更稳妥的表达是：

> 当前代码已经完成了核心 Agent 编排、结构化校验、确定性评分和失败转人工的闭环；真实生产上线还需要结合 Provider、容量、合规和公平性评估继续验证。

## 20. 最终记忆点

只记住下面这条主线：

```text
版本化输入
    -> 多 Agent 提取证据
    -> Schema 和证据校验
    -> Supervisor 质量门
    -> 确定性评分
    -> 置信度门控
    -> 业务分流或人工复核
```

最核心的一句话：

> Agent 提高了招聘系统处理非结构化信息的效率，但最终业务结果必须依赖版本化规则、可验证证据、明确失败状态和人工审核边界。
