import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles/index.css";

// Catch errors thrown before/while React mounts (e.g. corrupt persisted store
// state) — these bypass the React ErrorBoundary and would otherwise white-screen.
// Beacon the error to the API so it lands in a log we can read (debugging aid).
function reportError(message: string) {
  try {
    fetch("http://localhost:8787/healthz?clienterror=" + encodeURIComponent(message.slice(0, 1500)));
  } catch {
    /* ignore */
  }
}

function showFatal(message: string) {
  reportError(message);
  const root = document.getElementById("root");
  if (!root || root.childElementCount > 0) return; // React already rendered
  root.innerHTML = `
    <div style="height:100%;overflow:auto;padding:24px;font-family:ui-monospace,Menlo,monospace;background:#faf9f6;color:#3a3a3a">
      <h2 style="color:#b13d12;margin:0 0 8px">Startup error</h2>
      <pre style="font-size:11px;color:#6b6657;white-space:pre-wrap">${message.replace(/</g, "&lt;")}</pre>
      <button onclick="try{localStorage.clear()}catch(e){};location.reload()" style="margin-top:16px;padding:6px 14px;cursor:pointer;color:#b13d12">Clear local data &amp; reload</button>
    </div>`;
}
window.addEventListener("error", (e) => showFatal(`${e.message}\n${e.error?.stack ?? ""}`));
window.addEventListener("unhandledrejection", (e) =>
  showFatal(`Unhandled promise rejection:\n${String(e.reason?.stack ?? e.reason)}`),
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
