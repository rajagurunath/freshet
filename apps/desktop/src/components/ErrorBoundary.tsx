import React from "react";

/**
 * Top-level error boundary. Without this a render crash white-screens the whole
 * app (no sidebar, nothing) with the real error hidden in the webview console.
 * Now it shows the error inline and offers a recovery action — clearing local
 * state, which fixes crashes caused by stale/corrupt persisted store data.
 */
export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Surface it where it's findable even without devtools.
    console.error("[context-hub] render crash:", error, info?.componentStack);
    try {
      localStorage.setItem(
        "ctxhub.lastError",
        `${error?.message}\n${error?.stack}\n${info?.componentStack ?? ""}`,
      );
    } catch {
      /* ignore */
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div
        style={{
          height: "100%",
          overflow: "auto",
          padding: 24,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          color: "#3a3a3a",
          background: "#faf9f6",
        }}
      >
        <h2 style={{ color: "#b13d12", margin: "0 0 8px" }}>Something broke</h2>
        <div style={{ fontSize: 13, marginBottom: 12 }}>{error.message}</div>
        <pre style={{ fontSize: 11, color: "#6b6657", whiteSpace: "pre-wrap" }}>
          {error.stack}
        </pre>
        <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
          <button
            onClick={() => location.reload()}
            style={{ padding: "6px 14px", cursor: "pointer" }}
          >
            Reload
          </button>
          <button
            onClick={() => {
              try {
                localStorage.clear();
              } catch {
                /* ignore */
              }
              location.reload();
            }}
            style={{ padding: "6px 14px", cursor: "pointer", color: "#b13d12" }}
          >
            Clear local data &amp; reload
          </button>
        </div>
      </div>
    );
  }
}
