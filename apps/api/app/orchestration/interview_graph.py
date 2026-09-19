"""面试图：根据会话历史与题目计划生成下一动作（问下一个问题/追问/收尾）。

Provider 未配置或输出不符合 Schema → 明确错误；接管后不生成任何回复。
禁问规则：计划中配置的敏感属性（年龄/婚育/宗教等）不得出现在问题中。
"""
import json
import re

from app.infrastructure.model_gateway import (
    ModelGateway,
    ProviderError,
    ProviderInvalidResponseError,
)

PROMPT_VERSION = "interview-graph-v1"

SENSITIVE_ATTRIBUTES = ("年龄", "性别", "婚育", "婚姻", "生育", "民族", "宗教", "信仰", "政治面貌", "户籍")


class InterviewGraphError(Exception):
    pass


def build_next_action(
    gateway: ModelGateway,
    *,
    plan: dict,
    questions_asked: list[str],
    messages: list[dict],
    round_number: int,
    max_rounds: int,
) -> dict:
    """返回 {action: question|follow_up|wrap_up, content, done}。"""
    if round_number >= max_rounds:
        return {"action": "wrap_up", "content": "本轮面试问题已问完，感谢参与。", "done": True}

    system = (
        "你是招聘文字面试官。系统政策：只能基于岗位要求与候选人回答继续提问；"
        "禁止询问以下敏感属性：" + "、".join(SENSITIVE_ATTRIBUTES) + "。"
        '只输出 JSON：{"action": "question|follow_up|wrap_up", "content": "提问内容", "done": false}'
    )
    transcript = "\n".join(f"{item['actor_type']}: {item['content']}" for item in messages[-12:])
    asked = "\n".join(f"- {q}" for q in questions_asked[-5:]) or "（尚未提问）"
    user = (
        f"题目计划：{json.dumps(plan, ensure_ascii=False)}\n"
        f"已问问题：\n{asked}\n\n对话记录：\n{transcript}\n\n"
        "输出下一个动作。"
    )
    try:
        chat = gateway.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    except ProviderError:
        raise
    text = chat.content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderInvalidResponseError("面试图输出不是合法 JSON") from exc
    action = data.get("action")
    content = str(data.get("content", "")).strip()
    done = bool(data.get("done", False))
    if action not in {"question", "follow_up", "wrap_up"} or not content:
        raise ProviderInvalidResponseError("面试图输出缺少合法 action/content")
    if any(attr in content for attr in SENSITIVE_ATTRIBUTES):
        raise ProviderInvalidResponseError(f"面试问题包含敏感属性：{content}")
    if action == "wrap_up":
        done = True
    return {"action": action, "content": content, "done": done}
