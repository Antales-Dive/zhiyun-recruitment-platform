/** 集中式 API 客户端：统一 envelope 解析、认证令牌与错误分类。 */

export class ApiError extends Error {
  code: string;
  retryable: boolean;
  status: number;

  constructor(status: number, code: string, message: string, retryable = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

export interface Envelope<T> {
  request_id: string;
  trace_id: string;
  data: T | null;
  error: { code: string; message: string; details: unknown[]; retryable: boolean } | null;
}

const TOKEN_KEY = "zhiyun_token";

export function saveToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function loadToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string> | undefined) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const token = loadToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const response = await fetch(path, { ...options, headers });
  let body: Envelope<T> | null = null;
  try {
    body = (await response.json()) as Envelope<T>;
  } catch {
    body = null;
  }
  if (!response.ok || !body || body.error) {
    const error = body?.error;
    if (response.status === 401) {
      clearToken();
      window.location.hash = "#/login";
    }
    throw new ApiError(
      response.status,
      error?.code ?? "HTTP_ERROR",
      error?.message ?? `请求失败（${response.status}）`,
      error?.retryable ?? response.status >= 500,
    );
  }
  return body.data as T;
}

export function formData(body: Record<string, unknown>): FormData {
  const form = new FormData();
  for (const [key, value] of Object.entries(body)) {
    if (value !== undefined && value !== null) {
      form.append(key, String(value));
    }
  }
  return form;
}
