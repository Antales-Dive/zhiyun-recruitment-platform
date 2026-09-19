/** 认证：登录页与令牌守卫。 */
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, clearToken, loadToken, saveToken } from "../../api/client";

interface LoginResponse {
  token: string;
  user_id: string;
  org_id: string;
  roles: string[];
  expires_at: string;
}

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const data = await api<LoginResponse>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      saveToken(data.token);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-page">
      <form className="card login-card" onSubmit={submit} aria-label="登录">
        <h1>智聘云</h1>
        <p className="muted">企业招聘管理平台</p>
        <label>
          邮箱
          <input
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label>
          密码
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <button type="submit" className="button button-primary" disabled={loading}>
          {loading ? "登录中…" : "登录"}
        </button>
      </form>
    </main>
  );
}

export function RequireAuth({ children }: { children: JSX.Element }) {
  if (!loadToken()) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

export function LogoutButton() {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      className="button button-ghost"
      onClick={async () => {
        const token = loadToken();
        if (token) {
          try {
            await api("/api/v1/auth/logout", { method: "POST" });
          } catch {
            // 本地登出不因网络失败而阻塞
          }
        }
        clearToken();
        navigate("/login", { replace: true });
      }}
    >
      退出登录
    </button>
  );
}
