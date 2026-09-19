/** SSE 解析测试：事件、序号与心跳注释。 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { connectSse, parseSseChunk } from "../src/api/sse";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("parseSseChunk", () => {
  it("parses event with id and data", () => {
    const chunk = 'id: 42\nevent: task.progress\ndata: {"task_id":"t1","progress":40}\n\n';

    const events = parseSseChunk(chunk);

    expect(events).toHaveLength(1);
    expect(events[0].id).toBe(42);
    expect(events[0].event).toBe("task.progress");
    expect(events[0].data).toEqual({ task_id: "t1", progress: 40 });
  });

  it("ignores heartbeat comments", () => {
    const events = parseSseChunk(": keep-alive\n\n");

    expect(events).toHaveLength(0);
  });

  it("parses multiple events in one chunk", () => {
    const chunk =
      'id: 1\nevent: task.progress\ndata: {"progress":10}\n\nid: 2\nevent: task.completed\ndata: {"progress":100}\n\n';

    const events = parseSseChunk(chunk);

    expect(events).toHaveLength(2);
    expect(events[1].event).toBe("task.completed");
    expect(events[1].data.progress).toBe(100);
  });

  it("joins multi-line data payloads per SSE spec", () => {
    const chunk = 'id: 7\nevent: message\ndata: {"lines":\ndata: [1,2]}\n\n';

    const events = parseSseChunk(chunk);

    expect(events[0].data).toEqual({ lines: [1, 2] });
  });

  it("does not reconnect after the connection is closed during the retry delay", () => {
    vi.useFakeTimers();
    class FakeEventSource {
      static instances: FakeEventSource[] = [];
      onerror: (() => void) | null = null;
      onopen: (() => void) | null = null;
      onmessage: ((message: MessageEvent) => void) | null = null;
      close = vi.fn();

      constructor() {
        FakeEventSource.instances.push(this);
      }
    }
    vi.stubGlobal("EventSource", FakeEventSource);

    const connection = connectSse("/api/v1/tasks/t1/events", vi.fn(), vi.fn(), {
      maxRetries: 2,
      retryDelayMs: 100,
    });
    FakeEventSource.instances[0].onerror?.();

    connection.close();
    vi.advanceTimersByTime(100);

    expect(FakeEventSource.instances).toHaveLength(1);
  });
});
