"""身份模块：Principal 主角色、角色成员与 ADMIN 豁免逻辑测试。"""
from app.domains.identity.principal import ROLES_PRIORITY, Principal


def test_priority_ordering_covers_all_roles():
    assert list(ROLES_PRIORITY) == ["ADMIN", "HR", "INTERVIEWER", "AUDITOR"]


def test_primary_role_follows_priority():
    principal = Principal(user_id="u1", org_id="org-a", roles=frozenset({"HR", "AUDITOR"}))
    assert principal.role == "HR"


def test_admin_wins_over_other_roles():
    principal = Principal(user_id="u1", org_id="org-a", roles=frozenset({"ADMIN", "HR"}))
    assert principal.role == "ADMIN"


def test_has_role_checks_membership():
    principal = Principal(user_id="u1", org_id="org-a", roles=frozenset({"INTERVIEWER"}))
    assert principal.has_role("INTERVIEWER")
    assert not principal.has_role("AUDITOR")


def test_admin_bypasses_any_role_check():
    principal = Principal(user_id="u1", org_id="org-a", roles=frozenset({"ADMIN"}))
    assert principal.has_role("AUDITOR")
    assert principal.has_role("HR")


def test_header_principal_has_no_user_identity():
    principal = Principal(user_id=None, org_id="default", roles=frozenset({"HR"}), source="header")
    assert principal.user_id is None
    assert principal.source == "header"


def test_session_principal_keeps_identity():
    principal = Principal(
        user_id="u1", org_id="org-a", roles=frozenset({"HR"}), session_id="s1", source="session"
    )
    assert principal.user_id == "u1"
    assert principal.session_id == "s1"
