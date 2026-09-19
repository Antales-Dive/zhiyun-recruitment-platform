/** 排期管理：创建可用时段与取消预约。 */
import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { PageHeader } from "../../components/states";

export function SchedulingPage() {
  const [resourceId, setResourceId] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function createSlots(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    try {
      const data = await api<{ slots: unknown[] }>("/api/v1/schedule-slots", {
        method: "POST",
        body: JSON.stringify({
          resource_type: "interviewer",
          resource_id: resourceId,
          intervals: [{ starts_at: startsAt, ends_at: endsAt }],
        }),
      });
      setMessage(`已创建 ${data.slots.length} 个时段`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "创建失败");
    }
  }

  return (
    <section>
      <PageHeader title="排期管理" />
      <form className="card form-grid" onSubmit={createSlots} aria-label="创建时段">
        <label>
          面试官/资源 ID
          <input required value={resourceId} onChange={(event) => setResourceId(event.target.value)} />
        </label>
        <label>
          开始时间
          <input
            type="datetime-local"
            required
            value={startsAt}
            onChange={(event) => setStartsAt(event.target.value)}
          />
        </label>
        <label>
          结束时间
          <input type="datetime-local" required value={endsAt} onChange={(event) => setEndsAt(event.target.value)} />
        </label>
        {message ? <p className="form-note">{message}</p> : null}
        <button type="submit" className="button button-primary">
          创建时段
        </button>
      </form>
    </section>
  );
}
