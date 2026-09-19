/** 应用外壳：侧栏导航 + 路由 + 错误边界。 */
import { Component, type ReactNode } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { LoginPage, LogoutButton, RequireAuth } from "../features/auth/LoginPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { JobsPage } from "../features/jobs/JobsPage";
import { CandidatesPage } from "../features/candidates/CandidatesPage";
import { CandidateDetailPage } from "../features/candidates/CandidateDetailPage";
import { PipelinePage } from "../features/pipeline/PipelinePage";
import { QuestionnairesPage } from "../features/questionnaires/QuestionnairesPage";
import { SchedulingPage } from "../features/scheduling/SchedulingPage";
import { InterviewsPage } from "../features/interviews/InterviewsPage";
import { KnowledgePage } from "../features/knowledge/KnowledgePage";
import { AssistantPage } from "../features/assistant/AssistantPage";
import { AuditPage } from "../features/audit/AuditPage";

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="state state-error" role="alert">
          <strong>页面出错</strong>
          <p>{this.state.error.message}</p>
        </div>
      );
    }
    return this.props.children;
  }
}

const NAV_ITEMS = [
  { to: "/", label: "仪表盘", end: true },
  { to: "/jobs", label: "岗位" },
  { to: "/candidates", label: "候选人" },
  { to: "/pipeline", label: "招聘管道" },
  { to: "/questionnaires", label: "问卷" },
  { to: "/scheduling", label: "排期" },
  { to: "/interviews", label: "AI 面试" },
  { to: "/knowledge", label: "知识库" },
  { to: "/assistant", label: "制度助手" },
  { to: "/audit", label: "审计" },
];

function Layout() {
  return (
    <div className="app-shell">
      <nav className="sidebar" aria-label="主导航">
        <div className="brand">智聘云</div>
        <ul>
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="sidebar-footer">
          <LogoutButton />
        </div>
      </nav>
      <main className="content">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/candidates" element={<CandidatesPage />} />
            <Route path="/candidates/:candidateId" element={<CandidateDetailPage />} />
            <Route path="/pipeline" element={<PipelinePage />} />
            <Route path="/questionnaires" element={<QuestionnairesPage />} />
            <Route path="/scheduling" element={<SchedulingPage />} />
            <Route path="/interviews" element={<InterviewsPage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/audit" element={<AuditPage />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      />
    </Routes>
  );
}
