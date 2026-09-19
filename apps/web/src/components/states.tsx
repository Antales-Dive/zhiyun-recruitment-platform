/** 通用 UI 原语：加载/空/错误/无权限/状态徽章（01-requirements.md UI 要求）。 */
import type { ReactNode } from "react";

export function LoadingState({ label = "加载中…" }: { label?: string }) {
  return (
    <div className="state state-loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="state state-empty">
      <strong>{title}</strong>
      {hint ? <p>{hint}</p> : null}
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <strong>操作失败</strong>
      <p>{message}</p>
      {retry ? (
        <button type="button" className="button" onClick={retry}>
          重试
        </button>
      ) : null}
    </div>
  );
}

export function ForbiddenState() {
  return (
    <div className="state state-empty">
      <strong>无权限</strong>
      <p>当前角色无权访问该资源。</p>
    </div>
  );
}

const STATUS_TONES: Record<string, string> = {
  SUCCEEDED: "success",
  DONE: "success",
  SENT: "success",
  PUBLISHED: "success",
  PARSED: "success",
  REVIEWED: "success",
  HIRED: "success",
  ACTIVE: "success",
  INTERVIEW: "info",
  QUESTIONNAIRE: "info",
  IN_PROGRESS: "info",
  TALENT_POOL: "info",
  PENDING: "info",
  PROCESSING: "info",
  RETRY_WAIT: "warn",
  NEEDS_REVIEW: "warn",
  NEEDS_OCR: "warn",
  CANCELLED: "warn",
  FAILED: "danger",
  REJECTED: "danger",
  CLOSED: "muted",
  SUPERSEDED: "muted",
  WITHDRAWN: "muted",
};

export function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONES[status] ?? "muted";
  return (
    <span className={`badge badge-${tone}`} title={status}>
      {status}
    </span>
  );
}

export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <h1>{title}</h1>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}
