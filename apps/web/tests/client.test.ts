/** API 客户端测试：envelope 解析、错误分类与令牌管理。 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, clearToken, loadToken, saveToken } from "../src/api/client";

describe("api client", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns data from envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ request_id: "r1", trace_id: "t1", data: { ok: true }, error: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const data = await api<{ ok: boolean }>("/api/v1/jobs");

    expect(data.ok).toBe(true);
  });

  it("throws ApiError with code from error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            request_id: "r1",
            trace_id: "t1",
            data: null,
            error: { code: "FORBIDDEN", message: "无权限", details: [], retryable: false },
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await api("/api/v1/jobs").catch((err: unknown) => err);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("FORBIDDEN");
    expect((error as ApiError).status).toBe(403);
  });

  it("clears token on 401", async () => {
    saveToken("stale-token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            request_id: "r1",
            trace_id: "t1",
            data: null,
            error: { code: "TOKEN_EXPIRED", message: "过期", details: [], retryable: false },
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await api("/api/v1/auth/me").catch(() => undefined);

    expect(loadToken()).toBeNull();
  });

  it("persists and loads token", () => {
    saveToken("token-abc");
    expect(loadToken()).toBe("token-abc");
    clearToken();
    expect(loadToken()).toBeNull();
  });
});
