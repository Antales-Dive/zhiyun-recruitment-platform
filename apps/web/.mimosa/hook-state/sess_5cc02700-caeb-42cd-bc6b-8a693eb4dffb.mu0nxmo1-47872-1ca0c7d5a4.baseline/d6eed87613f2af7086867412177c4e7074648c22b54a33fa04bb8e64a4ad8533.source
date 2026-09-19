/** 制度助手：RAG 问答、引用展示与拒答状态。 */
import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { PageHeader } from "../../components/states";

interface Citation {
  citation_id: string;
  document_title: string;
  version: number;
  section: string | null;
  excerpt: string;
  score: number;
}

interface QueryResponse {
  query_id: string | null;
  answer: string;
  reliable: boolean;
  citations: Citation[];
  error_code: string | null;
}

export function AssistantPage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function ask(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const data = await api<QueryResponse>("/api/v1/assistant/query", {
        method: "POST",
        body: JSON.stringify({ question }),
      });
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "查询失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <PageHeader title="制度助手" />
      <form className="card form-grid" onSubmit={ask} aria-label="制度问答">
        <label>
          问题
          <textarea
            required
            minLength={2}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
        </label>
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <button type="submit" className="button button-primary" disabled={loading}>
          {loading ? "查询中…" : "查询"}
        </button>
      </form>
      {result ? (
        <article className="card answer-card">
          <p className="muted">
            {result.reliable ? "基于已发布制度回答" : `未生成可靠回答（${result.error_code ?? "未知"}）`}
          </p>
          <p className="answer-text">{result.answer}</p>
          {result.citations.length > 0 ? (
            <details open>
              <summary>引用（{result.citations.length}）</summary>
              <ul className="list">
                {result.citations.map((citation) => (
                  <li className="card list-item" key={citation.citation_id}>
                    <strong>
                      {citation.document_title} 第{citation.version}版
                      {citation.section ? ` · ${citation.section}` : ""}
                    </strong>
                    <p className="muted">{citation.excerpt.slice(0, 160)}…</p>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </article>
      ) : null}
    </section>
  );
}
