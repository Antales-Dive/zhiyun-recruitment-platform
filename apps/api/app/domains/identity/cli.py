"""身份域运维 CLI：创建组织/用户/角色绑定（生产引导用）。

用法（在 apps/api 目录或 PYTHONPATH 指向 apps/api 时）：
    python -m app.domains.identity.cli create-user --email admin@example.com --password '...' --role ADMIN --org default
    python -m app.domains.identity.cli assign-role --email hr@example.com --role HR --org default
"""
import argparse
import getpass
import sys

from app.domains.identity.service import (
    EmailTakenError,
    IdentityError,
    assign_role,
    create_user,
    ensure_roles,
    get_or_create_default_org,
    login,
)
from app.infrastructure.db import SessionLocal


def _commit_and_report(db, message: str) -> None:
    db.commit()
    print(message)


def cmd_create_user(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        ensure_roles(db)
        get_or_create_default_org(db)
        password = args.password or getpass.getpass("密码（不回显）: ")
        if not password:
            raise SystemExit("密码不能为空")
        user = create_user(db, org_id=args.org, email=args.email, password=password)
        binding = assign_role(db, user_id=user.id, org_id=args.org, role_code=args.role, trace_id="cli")
        db.commit()
        print(f"已创建用户 {user.email}（org={args.org}，role={binding.role_id and args.role}）")
    except EmailTakenError:
        print(f"用户已存在：{args.email}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def cmd_assign_role(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        ensure_roles(db)
        from sqlalchemy import select

        from app.infrastructure.models import User

        user = db.scalar(select(User).where(User.email == args.email, User.org_id == args.org))
        if user is None:
            print(f"用户不存在：{args.email}（org={args.org}）", file=sys.stderr)
            sys.exit(1)
        assign_role(db, user_id=user.id, org_id=args.org, role_code=args.role, trace_id="cli")
        db.commit()
        print(f"已为 {user.email} 绑定角色 {args.role}")
    finally:
        db.close()


def cmd_verify(args: argparse.Namespace) -> None:
    """验证登录链路（输出不含密码与令牌）。"""
    db = SessionLocal()
    try:
        password = args.password or getpass.getpass("密码（不回显）: ")
        result = login(db, email=args.email, password=password, org_id=args.org, trace_id="cli")
        db.commit()
        print(f"登录成功：org={result.org_id} roles={result.roles}")
    except IdentityError:
        print("登录失败：凭据无效", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="identity-cli", description="智聘云身份域运维命令")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-user", help="创建用户并绑定角色")
    create.add_argument("--email", required=True)
    create.add_argument("--role", default="HR")
    create.add_argument("--org", default="default")
    create.add_argument("--password", default=None, help="留空则交互式输入")
    create.set_defaults(func=cmd_create_user)

    assign = subparsers.add_parser("assign-role", help="为既有用户绑定角色")
    assign.add_argument("--email", required=True)
    assign.add_argument("--role", required=True)
    assign.add_argument("--org", default="default")
    assign.set_defaults(func=cmd_assign_role)

    verify = subparsers.add_parser("verify-login", help="验证登录链路")
    verify.add_argument("--email", required=True)
    verify.add_argument("--org", default="default")
    verify.add_argument("--password", default=None)
    verify.set_defaults(func=cmd_verify)

    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
