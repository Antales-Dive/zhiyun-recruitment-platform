/** SSE 客户端：Last-Event-ID 重连补发；重连失败回退快照轮询由调用方决定。 */

export interface SseEvent {
  id: number;
  event: string;
  data: Record<string, unknown>;
}

export function parseSseChunk(chunk: string): SseEvent[] {
  const events: SseEvent[] = [];
  let id = 0;
  let eventName = "message";
  const dataLines: string[] = [];
  for (const rawLine of chunk.split("\n")) {
    const line = rawLine.trimEnd();
    if (line === "") {
      if (dataLines.length > 0) {
        try {
          events.push({
            id,
            event: eventName,
            data: JSON.parse(dataLines.join("\n")) as Record<string, unknown>,
          });
        } catch {
          // 忽略无法解析的负载（心跳注释等）
        }
      }
      dataLines.length = 0;
      eventName = "message";
      continue;
    }
    if (line.startsWith(":") || line.startsWith("id:") === false && line.startsWith("event:") === false && line.startsWith("data:") === false) {
      continue; // 注释心跳
    }
    if (line.startsWith("id:")) {
      const value = line.slice(3).trim();
      const parsed = Number.parseInt(value, 10);
      if (!Number.isNaN(parsed)) id = parsed;
    } else if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  return events;
}

export interface SseConnection {
  close: () => void;
}

/**
 * 建立 SSE 连接；断线自动重连并携带 Last-Event-ID 只补发后续事件（AC-009）。
 */
export function connectSse(
  url: string,
  onEvent: (event: SseEvent) => void,
  onState: (state: "connecting" | "open" | "reconnecting" | "closed") => void,
  options: { maxRetries?: number; retryDelayMs?: number; getLastEventId?: () => number | null } = {},
): SseConnection {
  let closed = false;
  let retries = 0;
  const maxRetries = options.maxRetries ?? 5;
  const retryDelayMs = options.retryDelayMs ?? 2000;
  let source: EventSource | null = null;
  let retryTimer: number | null = null;

  const connect = () => {
    if (closed) return;
    const lastEventId = options.getLastEventId?.();
    const separator = url.includes("?") ? "&" : "?";
    const fullUrl = lastEventId ? `${url}${separator}last_event_id=${lastEventId}` : url;
    onState(retries > 0 ? "reconnecting" : "connecting");
    source = new EventSource(fullUrl);
    source.onopen = () => {
      retries = 0;
      onState("open");
    };
    source.onmessage = (message) => {
      for (const event of parseSseChunk(message.data as string)) {
        onEvent(event);
      }
    };
    source.onerror = () => {
      source?.close();
      retries += 1;
      if (retries > maxRetries) {
        closed = true;
        onState("closed");
        return;
      }
      retryTimer = window.setTimeout(connect, retryDelayMs);
    };
  };

  connect();
  return {
    close: () => {
      closed = true;
      source?.close();
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
        retryTimer = null;
      }
    },
  };
}
