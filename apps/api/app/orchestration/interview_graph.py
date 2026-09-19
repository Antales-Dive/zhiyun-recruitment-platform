"""面试图：根据会话历史与题目计划生成下一动作（问下一个问题/追问/收尾）。

【职责边界】
本模块是"一次决策"，不是"一整场面试的状态机"。会话状态、题目轮次、消息序号、
接管互斥全部由 domains/interviews/service.py 负责；这里只做一件事：
输入（题目计划 + 已问问题 + 最近对话）→ 输出下一个动作。
调用方是 api/v1/interview_routes._generate_ai_reply()，它把动作再翻译成
"落库一条 AI 消息"或"结束会话并出报告"。

【为什么用"动作"而不是自由文本】
把模型输出限定为 question / follow_up / wrap_up 三选一的封闭集合，路由决策
才能写在代码里（wrap_up 或 done=true → complete_session）。若模型直接输出
一段话，"这场面试该不该结束"这种有后果的判断就交给了模型。

Provider 未配置或输出不符合 Schema → 明确错误；接管后不生成任何回复。
禁问规则：内置敏感属性（年龄/婚育/宗教等）+ 会话计划里 HR 配置的
plan["forbidden"] 合并成一份清单，同一份清单既进提示词（事前告知）
也用于生成后拦截（事后校验并抛错）。不能依赖模型自律满足合规要求。
"""
import json
import re

from app.infrastructure.model_gateway import (
    ModelGateway,
    ProviderInvalidResponseError,
)

# 提示词改版必须同步 bump：api/v1/interview_routes.py 在每次模型调用后把它写进
# ModelRun.prompt_version，用于失败归因与 Prompt A/B 对比（与 matching 侧同一机制）。
# v2：禁问清单从"仅内置敏感属性"扩展为"内置 + plan.forbidden"，提示词文案随之改变。
PROMPT_VERSION = "interview-graph-v2"

# 招聘合规禁问底线（中国就业促进法/个人信息保护相关的常见红线属性），所有会话无条件生效。
# 同时用于两处：① 拼进 system 提示词做"事前告知"；② 对生成结果做"事后拦截"。
# 用子串匹配而非分词，宁可误拦（抛错→前端提示重试）不可漏放（问出敏感问题）。
SENSITIVE_ATTRIBUTES = ("年龄", "性别", "婚育", "婚姻", "生育", "民族", "宗教", "信仰", "政治面貌", "户籍")


def _forbidden_terms(plan: dict) -> tuple[str, ...]:
    """本次会话生效的禁问清单 = 内置敏感属性 + 计划里 HR 配置的 forbidden 项。

    plan_json 可能来自历史数据或直连写库，因此这里做宽松归一而非信任上游模型：
    非列表一律视为"未配置"（否则字符串会被逐字符展开成一堆单字禁词）；
    空串/纯空白必须丢弃——空串是任意文本的子串，留下会让所有问题都被判违规。
    dict.fromkeys 去重且保持顺序，避免同一词在提示词里重复出现。
    """
    configured = plan.get("forbidden")
    extra = [str(item).strip() for item in configured] if isinstance(configured, list) else []
    return tuple(dict.fromkeys([*SENSITIVE_ATTRIBUTES, *(term for term in extra if term)]))


def build_next_action(
    gateway: ModelGateway,
    *,
    plan: dict,
    questions_asked: list[str],
    messages: list[dict],
    round_number: int,
    max_rounds: int,
) -> dict:
    """返回 {action: question|follow_up|wrap_up, content, done}。

    Args:
        gateway: Model Gateway 实例；未配置时其 chat() 抛 ModelNotConfiguredError。
        plan: 会话创建时固化的题目计划（JSON 反序列化结果），含 max_rounds 与 forbidden；
            forbidden 是 HR 为本岗位额外配置的禁问项，与内置敏感属性一并生效。
        questions_asked: 本场 AI 已提出的问题文本，用于避免重复提问。
        messages: 全量会话消息（{actor_type, content}），只有最近 12 条进入提示词。
        round_number: 调用方按"已问数 + 1"算出的当前轮次（1 起）。
        max_rounds: 计划总轮数，达到即本地收尾，不再消耗模型调用。

    Returns:
        dict：action 为三种封闭取值之一；content 为要发给候选人的话术；
        done=True 表示调用方应结束会话（wrap_up 已被强制置真）。

    Raises:
        ProviderError 族：模型未配置/超时/不可用/认证失败/输出不合 Schema/命中禁问清单。
        调用方必须捕获并回显错误码，不得降级成模板问题继续面试。
    """
    # 轮次上限优先于模型：即使 Provider 已配置也不再调用，省钱且保证面试可预期结束。
    # 这条分支保证"面试一定有终点"，不依赖模型自己判断该不该收尾。
    if round_number >= max_rounds:
        return {"action": "wrap_up", "content": "本轮面试问题已问完，感谢参与。", "done": True}

    # 一次计算、两处使用：同一份清单既写进提示词（事前告知）也用于校验（事后拦截），
    # 保证"告诉模型的边界"与"实际判定失败的边界"永远一致。
    forbidden = _forbidden_terms(plan)
    system = (
        "你是招聘文字面试官。系统政策：只能基于岗位要求与候选人回答继续提问；"
        "禁止询问以下事项：" + "、".join(forbidden) + "。"
        '只输出 JSON：{"action": "question|follow_up|wrap_up", "content": "提问内容", "done": false}'
    )
    # 只取最近 12 条：面试多轮后全量历史会撑大上下文并抬高成本；
    # 早期信息已由 questions_asked（最近 5 问）承担去重职责。
    transcript = "\n".join(f"{item['actor_type']}: {item['content']}" for item in messages[-12:])
    asked = "\n".join(f"- {q}" for q in questions_asked[-5:]) or "（尚未提问）"
    user = (
        f"题目计划：{json.dumps(plan, ensure_ascii=False)}\n"
        f"已问问题：\n{asked}\n\n对话记录：\n{transcript}\n\n"
        "输出下一个动作。"
    )
    # Provider 失败（未配置/超时/不可用/认证失败）原样上抛，此处刻意不兜底：
    # 面试是候选人实时交互的场景，宁可让调用方回一个错误码，也不能用模板问题假装在面试。
    chat = gateway.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    text = chat.content.strip()
    # 与 matching 侧同一套容错：只剥 markdown 围栏，不修补 JSON 结构。
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderInvalidResponseError("面试图输出不是合法 JSON") from exc
    action = data.get("action")
    content = str(data.get("content", "")).strip()
    done = bool(data.get("done", False))
    # action 必须落在封闭集合且话术非空，否则这次生成不可用（空内容会让候选人看到空气泡）。
    if action not in {"question", "follow_up", "wrap_up"} or not content:
        raise ProviderInvalidResponseError("面试图输出缺少合法 action/content")
    # 合规硬闸：内置敏感属性与 HR 自定禁问项一并拦截，命中即判定整次调用失败；
    # 错误信息带上命中的词，HR 能直接看出是模型越界还是自己的禁问配置过宽。
    hits = [term for term in forbidden if term in content]
    if hits:
        raise ProviderInvalidResponseError(f"面试问题命中禁问项：{hits} → {content}")
    # 归一化：wrap_up 必然意味着结束，避免调用方还要同时判断两个字段而漏结束会话。
    if action == "wrap_up":
        done = True
    return {"action": action, "content": content, "done": done}

