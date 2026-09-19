/** E2E：登录 → 岗位列表 → 候选人导入 的完整管理台流程（jsdom 模拟）。 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../src/app/App";

function jsonResponse(data: unknown, status = 200) {
  const body =
    status >= 400
      ? { request_id: "r", trace_id: "t", data: null, error: { code: "X", message: "X", details: [], retryable: false } }
      : { request_id: "r", trace_id: "t", data, error: null };
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("e2e admin console flow", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("login then load jobs and candidates", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/login")) {
        return Promise.resolve(
          jsonResponse({ token: "t1", user_id: "u1", org_id: "org-a", roles: ["HR"], expires_at: "2026-09-15T00:00:00Z" }),
        );
      }
      if (url.includes("/api/v1/jobs")) {
        return Promise.resolve(jsonResponse([{ id: "j1", title: "后端工程师", status: "OPEN", active_version_id: "v1", versions: [{ job_version_id: "v1", version_no: 1, description: "", requirements: ["Python"] }] }]));
      }
      if (url.includes("/api/v1/candidates")) {
        return Promise.resolve(jsonResponse([{ id: "c1", job_id: "j1", name: "张三", status: "PARSED", task_id: "task-1", match_score: 82, route: "INTERVIEW" }]));
      }
      if (url.includes("/api/v1/dashboard")) {
        return Promise.resolve(jsonResponse({ total: 1, today_new: 0, parsed: 1, pending: 0, failed: 0 }));
      }
      if (url.includes("/api/v1/tasks/task-1/events")) {
        return Promise.resolve(new Response(": keep-alive\n\n", { status: 200, headers: { "Content-Type": "text/event-stream" } }));
      }
      return Promise.resolve(jsonResponse(null, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/login"]}>
        <App />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "hr@example.com" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    // 登录后进入仪表盘
    await waitFor(() => {
      expect(screen.getByText("仪表盘")).toBeInTheDocument();
    });

    // 进入岗位页
    fireEvent.click(screen.getByText("岗位"));
    await waitFor(() => {
      expect(screen.getByText("岗位管理")).toBeInTheDocument();
    });
    expect(screen.getByText("后端工程师")).toBeInTheDocument();

    // 进入候选人页并看到候选人
    fireEvent.click(screen.getByText("候选人"));
    await waitFor(() => {
      expect(screen.getByText("张三")).toBeInTheDocument();
    });
    expect(screen.getByText("PARSED")).toBeInTheDocument();
  });

  it("redirects unauthenticated users to login", () => {
    render(
      <MemoryRouter initialEntries={["/jobs"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
  });
});
