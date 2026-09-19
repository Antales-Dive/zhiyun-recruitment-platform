/** 问卷管理：创建问卷并发送候选人邀请。 */
import { useEffect, useState, type FormEvent } from "react";
import { api, isAbortError } from "../../api/client";
import { ErrorState, PageHeader } from "../../components/states";
import type { CandidateInfo } from "../candidates/CandidatesPage";

export function QuestionnairesPage() {
  const [candidates, setCandidates] = useState<CandidateInfo[]>([]);
  const [title, setTitle] = useState("");
  const [questionsText, setQuestionsText] = useState("");
  const [selectedCandidate, setSelectedCandidate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [inviteLink, setInviteLink] = useState<string | null>(null);

  async function refresh(signal?: AbortSignal) {
    try {
      const nextCandidates = await api<CandidateInfo[]>("/api/v1/candidates", { signal });
      setCandidates(nextCandidates);
      if (!selectedCandidate && nextCandidates[0]) setSelectedCandidate(nextCandidates[0].id);
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

  async function createQuestionnaire(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    setInviteLink(null);
    const questions = questionsText
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => ({ id: `q${index + 1}`, type: "text", title: line, options: [] }));
    if (questions.length === 0) {
      setMessage("至少填写一道题目");
      return;
    }
    try {
      const created = await api<{ questionnaire_id: string }>("/api/v1/questionnaires", {
        method: "POST",
        body: JSON.stringify({ title, questions, scoring: {} }),
      });
      const invitation = await api<{ token: string; expires_at: string }>(
        `/api/v1/questionnaires/${created.questionnaire_id}/send`,
        {
          method: "POST",
          body: JSON.stringify({ candidate_id: selectedCandidate }),
        },
      );
      setInviteLink(`${window.location.origin}/public/questionnaires/${invitation.token}`);
      setTitle("");
      setQuestionsText("");
      setMessage("问卷已创建并发送邀请");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "创建失败");
    }
  }

  return (
    <section>
      <PageHeader title="问卷管理" />
      <form className="card form-grid" onSubmit={createQuestionnaire} aria-label="创建问卷">
        <label>
          问卷标题
          <input required value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          题目（每行一题）
          <textarea required value={questionsText} onChange={(event) => setQuestionsText(event.target.value)} />
        </label>
        <label>
          发送给候选人
          <select value={selectedCandidate} onChange={(event) => setSelectedCandidate(event.target.value)}>
            {candidates.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
              </option>
            ))}
          </select>
        </label>
        {message ? <p className="form-note">{message}</p> : null}
        {inviteLink ? (
          <p className="form-note">
            候选人链接：<code>{inviteLink}</code>
          </p>
        ) : null}
        <button type="submit" className="button button-primary">
          创建并发送
        </button>
      </form>
      {error ? <ErrorState message={error} /> : null}
    </section>
  );
}
