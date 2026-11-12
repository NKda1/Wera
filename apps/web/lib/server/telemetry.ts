// Logfire for the web server: traces of its own requests and of its calls to the agent, and every
// server error Next.js captures. Loaded by `instrumentation.ts`, on the Node.js runtime only.
//
// The agent was traced and this side wasn't, so an export that failed or a route that threw was
// visible only in this terminal. Now both report to the same Logfire project, and a request's
// trace carries on into the agent: the call to it propagates W3C trace context, which the
// agent's FastAPI instrumentation picks up, so one click is one trace across both services.
//
// Not under `server-only`: that package throws outside React Server Components, and
// `instrumentation.ts` isn't one. Nothing here is imported by client code.

import { readFileSync } from "node:fs";
import path from "node:path";
import { OTLPHttpProtoTraceExporter, registerOTel } from "@vercel/otel";
import * as logfire from "logfire";

const SERVICE_NAME = "wera-web";

/** The agent's address — the same default as `env.agentConfig`, without its token, which this
 *  module has no use for (and `env.ts` is `server-only`, so it can't be imported here). */
function agentUrl(): string {
  return (process.env.AGENT_URL?.trim() || "http://127.0.0.1:8787").replace(/\/+$/, "");
}

/** The repo root: `pnpm dev` starts this server in apps/web. `WERA_ROOT` wins, as for the agent. */
function root(): string {
  return process.env.WERA_ROOT?.trim() || path.resolve(process.cwd(), "../..");
}

/** One non-secret flag from the env files the agent reads, which Next.js doesn't load. */
function rootFlag(name: string): string | undefined {
  if (process.env[name] !== undefined) return process.env[name];
  for (const file of [".env.local", ".env"]) {
    try {
      const line = readFileSync(path.join(root(), file), "utf8")
        .split(/\r?\n/)
        .find((l) => l.trim().startsWith(`${name}=`));
      if (line) return line.slice(line.indexOf("=") + 1).trim();
    } catch {
      // absent is fine: the agent's own default applies
    }
  }
  return undefined;
}

/**
 * Where to send, and with what. `LOGFIRE_TOKEN` (a deployment's secret) wins; otherwise the
 * project credential `logfire init` wrote for the agent, the same file its SDK reads. Nothing is
 * sent without a token, so WERA runs exactly as before for anyone who hasn't connected Logfire.
 * The token is passed straight to the exporter — never logged, never put in the environment.
 */
function destination(): { url: string; token: string } | null {
  if (rootFlag("LOGFIRE_ENABLED")?.toLowerCase() === "false") return null;
  let base = process.env.LOGFIRE_BASE_URL?.trim() || "";
  let token = process.env.LOGFIRE_TOKEN?.trim() || "";
  if (!token) {
    try {
      const file = JSON.parse(readFileSync(path.join(root(), ".logfire", "logfire_credentials.json"), "utf8"));
      token = typeof file.token === "string" ? file.token : "";
      base = base || (typeof file.logfire_api_url === "string" ? file.logfire_api_url : "");
    } catch {
      return null;
    }
  }
  if (!token || !base) return null;
  return { url: `${base.replace(/\/+$/, "")}/v1/traces`, token };
}

let registered = false;

export function registerTelemetry(): void {
  const to = destination();
  if (!to) return;
  const demo = (rootFlag("WERA_DEMO_MODE") ?? "true").toLowerCase() !== "false";
  registerOTel({
    serviceName: SERVICE_NAME,
    // The agent's own split (`observability.configure`), so a Logfire query for real data
    // filters both services the same way.
    attributes: { "deployment.environment.name": demo ? "demo" : "real" },
    traceExporter: new OTLPHttpProtoTraceExporter({ url: to.url, headers: { Authorization: to.token } }),
    instrumentationConfig: {
      // Only the agent gets trace context: it is WERA's own service, and nothing else this
      // server calls should learn WERA's trace ids.
      fetch: { propagateContextUrls: [agentUrl()] },
    },
  });
  registered = true;
}

type RequestInfo = { path: string; method: string };
type ErrorContext = { routerKind: string; routePath: string; routeType: string };

/**
 * A server error Next.js caught: a route handler or a server component that threw. Path and
 * route only — never headers, which carry the agent token's sibling cookies and the person's
 * session — so this stays inside SEC-05's "metadata, not content".
 */
export function reportRequestError(error: unknown, request: RequestInfo, context: ErrorContext): void {
  if (!registered) return;
  const digest = typeof error === "object" && error !== null && "digest" in error ? String(error.digest) : undefined;
  logfire.reportError(`${request.method} ${context.routePath} failed`, error instanceof Error ? error : new Error(String(error)), {
    "http.method": request.method,
    "url.path": request.path.split("?")[0],
    "next.route": context.routePath,
    "next.route_type": context.routeType,
    "next.router": context.routerKind,
    ...(digest ? { "next.error_digest": digest } : {}),
  });
}

/** For route code that handles a failure itself but wants it seen — the format lint, say. */
export function warn(message: string, attributes: Record<string, string | number | boolean>): void {
  if (registered) logfire.warning(message, attributes);
}
