import { Component, Suspense, type ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ErrorState, Skeleton } from "./components/States";
import { PortfolioProvider } from "./lib/portfolio";
import { queryClient } from "./lib/query";
import { NotFoundPage, ROUTES } from "./lib/routes";
import { ThemeProvider } from "./lib/theme";
import { AppShell } from "./shell/AppShell";

/** Catches render errors in a page so the chrome survives. Resets on navigation. */
class PageBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: unknown }> {
  override state = { error: null as unknown };
  static getDerivedStateFromError(error: unknown) {
    return { error };
  }
  override componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }
  override render() {
    if (this.state.error) return <div className="oc-page"><ErrorState error={this.state.error} onRetry={() => this.setState({ error: null })} /></div>;
    return this.props.children;
  }
}

function PageFallback() {
  return (
    <div className="oc-page">
      <Skeleton height={44} width={320} />
      <Skeleton height={16} width={520} style={{ marginTop: 14 }} />
      <div className="grid-3" style={{ marginTop: 32 }}>
        <Skeleton height={220} />
        <Skeleton height={220} />
        <Skeleton height={220} />
      </div>
    </div>
  );
}

const CompanyPage = ROUTES.find((r) => r.path === "/company/:ticker")?.component;

function RoutedPages() {
  const loc = useLocation();
  return (
    <PageBoundary resetKey={loc.pathname}>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          {ROUTES.map((r) => (
            <Route key={r.path} path={r.path} element={<r.component />} />
          ))}
          {/* /company with no ticker renders the Company landing (search + suggestions). */}
          {CompanyPage && <Route path="/company" element={<CompanyPage />} />}
          <Route path="/ticker" element={<Navigate to="/ticker/SPY" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </PageBoundary>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <PortfolioProvider>
          <BrowserRouter>
            <AppShell>
              <RoutedPages />
            </AppShell>
          </BrowserRouter>
        </PortfolioProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
