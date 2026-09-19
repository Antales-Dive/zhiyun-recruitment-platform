/** 岗位：版本化岗位列表、创建与新增版本（FR-001/FR-002）。 */
import { useEffect, useState, type FormEvent } from "react";
import { api, isAbortError } from "../../api/client";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "../../components/states";

export interface JobVersionInfo {
  job_version_id: string;
  version_no: number;
  description: string;
  requirements: string[];
}

export interface JobInfo {
  id: string;
  title: string;
  status: string;
  active_version_id: string | null;
  versions: JobVersionInfo[];
}

export function JobsPage() {
  const [jobs, setJobs] = useState<JobInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [requirements, setRequirements] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function refresh(signal?: AbortSignal) {
    try {
      setJobs(await api<JobInfo[]>("/api/v1/jobs", { signal }));
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, []);

  async function createJob(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    try {
      await api("/api/v1/jobs", {
        method: "POST",
        body: JSON.stringify({
          title,
          description,
          requirements: requirements.split(",").map((item) => item.trim()).filter(Boolean),
        }),
      });
      setTitle("");
      setDescription("");
      setRequirements("");
      setMessage("岗位已创建");
      await refresh();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "创建失败");
    }
  }

  return (
    <section>
      <PageHeader title="岗位管理" />
      <form className="card form-grid" onSubmit={createJob} aria-label="创建岗位">
        <label>
          岗位名称
          <input required value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          描述
          <textarea value={description} onChange={(event) => setDescription(event.target.value)} />
        </label>
        <label>
          要求（逗号分隔）
          <input value={requirements} onChange={(event) => setRequirements(event.target.value)} />
        </label>
        {message ? <p className="form-note">{message}</p> : null}
        <button type="submit" className="button button-primary">
          创建岗位
        </button>
      </form>

      {error ? <ErrorState message={error} retry={() => void refresh()} /> : null}
      {!error && jobs === null ? <LoadingState /> : null}
      {jobs && jobs.length === 0 ? <EmptyState title="暂无岗位" hint="创建第一个岗位开始招聘流程。" /> : null}
      {jobs && jobs.length > 0 ? (
        <ul className="list">
          {jobs.map((job) => (
            <li className="card list-item" key={job.id}>
              <div>
                <strong>{job.title}</strong>
                <span className="muted">版本 {job.versions.length}</span>
              </div>
              <code className="muted">{job.versions.at(-1)?.requirements.join("、") ?? "无要求"}</code>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
