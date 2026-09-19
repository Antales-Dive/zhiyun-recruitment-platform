/** 招聘管道：按候选人状态分组的工作台视图。 */
import { useEffect, useState } from "react";
import { api, isAbortError } from "../../api/client";
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge } from "../../components/states";
import type { CandidateInfo } from "../candidates/CandidatesPage";

const COLUMNS = ["IMPORTED", "PARSED", "INTERVIEW", "QUESTIONNAIRE", "TALENT_POOL", "NEEDS_REVIEW"];

export function PipelinePage() {
  const [candidates, setCandidates] = useState<CandidateInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api<CandidateInfo[]>("/api/v1/candidates", { signal: controller.signal })
      .then(setCandidates)
      .catch((err) => {
        if (!isAbortError(err)) setError(err instanceof Error ? err.message : "加载失败");
      });
    return () => controller.abort();
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!candidates) return <LoadingState />;

  const grouped = COLUMNS.map((status) => ({
    status,
    items: candidates.filter((candidate) => candidate.status === status),
  }));
  const others = candidates.filter((candidate) => !COLUMNS.includes(candidate.status));

  return (
    <section>
      <PageHeader title="招聘管道" />
      {candidates.length === 0 ? <EmptyState title="管道为空" hint="导入候选人后按阶段流转。" /> : null}
      <div className="pipeline">
        {grouped.map((column) => (
          <div className="pipeline-column card" key={column.status}>
            <h2>
              <StatusBadge status={column.status} /> <span className="muted">{column.items.length}</span>
            </h2>
            <ul className="pipeline-list">
              {column.items.map((candidate) => (
                <li key={candidate.id} className="pipeline-item">
                  {candidate.name}
                  {candidate.match_score !== null ? <span className="muted"> {candidate.match_score}分</span> : null}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {others.length > 0 ? (
        <details className="card">
          <summary>其他状态（{others.length}）</summary>
          <ul className="list">
            {others.map((candidate) => (
              <li key={candidate.id} className="list-item">
                {candidate.name} <StatusBadge status={candidate.status} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
