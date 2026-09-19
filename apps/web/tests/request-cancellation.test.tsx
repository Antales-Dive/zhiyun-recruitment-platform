import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "../src/features/dashboard/DashboardPage";

describe("page request cancellation", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("aborts an in-flight page request when the page unmounts", () => {
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, options?: RequestInit) => {
        requestSignal = options?.signal ?? undefined;
        return new Promise<Response>(() => undefined);
      }),
    );

    const { unmount } = render(<DashboardPage />);

    expect(requestSignal).toBeDefined();
    expect(requestSignal?.aborted).toBe(false);

    unmount();

    expect(requestSignal?.aborted).toBe(true);
  });
});
