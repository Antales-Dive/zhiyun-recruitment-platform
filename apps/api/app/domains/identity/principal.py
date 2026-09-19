"""身份域：服务端验证产生的操作主体 Principal。"""
from dataclasses import dataclass

ROLES_PRIORITY = ("ADMIN", "HR", "INTERVIEWER", "AUDITOR")


@dataclass(frozen=True)
class Principal:
    """Principal 只能由服务端认证结果构造；请求头模拟模式仅限测试环境。"""

    user_id: str | None
    org_id: str
    roles: frozenset[str]
    session_id: str | None = None
    source: str = "session"

    @property
    def role(self) -> str:
        """主角色：按固定优先级取第一个存在的角色，用于兼容旧读取路径。"""
        for candidate in ROLES_PRIORITY:
            if candidate in self.roles:
                return candidate
        return ""

    def has_role(self, required: str) -> bool:
        """ADMIN 拥有全部角色能力；其余按成员判断。"""
        return "ADMIN" in self.roles or required in self.roles
