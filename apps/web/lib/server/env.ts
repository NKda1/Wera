import "server-only";

// The only module that reads secrets (SEC-04). It reports whether each key is set —
// never its value — so nothing secret can reach a response, the client bundle or a log.

import type { SecretStatus } from "../types";

const status = (value: string | undefined): SecretStatus => (value && value.trim() ? "set" : "missing");

export function secretStatus(): { llm: SecretStatus; firecrawl: SecretStatus; inbox: SecretStatus } {
  return {
    llm: status(process.env.LLM_API_KEY),
    firecrawl: status(process.env.FIRECRAWL_API_KEY),
    inbox: status(process.env.ALERTS_IMAP_APP_PASSWORD),
  };
}

export const llmBaseUrl = (): string => process.env.LLM_BASE_URL?.trim() || "https://openrouter.ai/api/v1";

export function llmProviderName(baseUrl: string): string {
  try {
    const host = new URL(baseUrl).hostname;
    if (host.endsWith("openrouter.ai")) return "OpenRouter";
    if (host.includes("dashscope") || host.includes("aliyuncs")) return "Alibaba Model Studio";
    return host;
  } catch {
    return "Custom endpoint";
  }
}

/** Mailbox label for the inbox card (SET-04). The address is shown; the password never is. */
export const inboxLabel = (): string | null => process.env.ALERTS_IMAP_USER?.trim() || null;
export const inboxFolder = (): string => process.env.ALERTS_FOLDER?.trim() || "WERA-alerts";
