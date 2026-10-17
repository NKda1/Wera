import "server-only";

// The only module that reads secrets (SEC-04). Since Phase 2 the web app holds no provider
// keys at all: they live with the agent service. This app only needs to reach the agent,
// on loopback, with the shared token both processes get at start-up.

export function agentConfig(): { url: string; token: string | null } {
  return {
    url: (process.env.AGENT_URL?.trim() || "http://127.0.0.1:8787").replace(/\/+$/, ""),
    token: process.env.AGENT_TOKEN?.trim() || null,
  };
}
