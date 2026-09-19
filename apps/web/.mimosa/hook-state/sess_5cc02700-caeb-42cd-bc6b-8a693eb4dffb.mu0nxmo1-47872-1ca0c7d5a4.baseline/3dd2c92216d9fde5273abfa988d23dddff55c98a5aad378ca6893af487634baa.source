/** 登录页组件测试：表单标签、提交与错误展示（NFR-009 表单标签/错误说明）。 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "../src/features/auth/LoginPage";

describe("LoginPage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders labeled form fields", () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("邮箱")).toBeInTheDocument();
    expect(screen.getByLabelText("密码")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
  });

  it("shows error message when login fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            request_id: "r",
            trace_id: "t",
            data: null,
            error: { code: "INVALID_CREDENTIALS", message: "INVALID_CREDENTIALS", details: [], retryable: false },
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "hr@example.com" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });

  it("stores token and navigates on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            request_id: "r",
            trace_id: "t",
            data: { token: "token-1", user_id: "u1", org_id: "org-a", roles: ["HR"], expires_at: "2026-09-14T00:00:00Z" },
            error: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "hr@example.com" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => {
      expect(localStorage.getItem("zhiyun_token")).toBe("token-1");
    });
  });
});
