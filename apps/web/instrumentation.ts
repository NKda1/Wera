// Next.js loads this once when the server starts (see node_modules/next/dist/docs, "instrumentation").
// Telemetry is Node.js-only: it reads the local Logfire credential from disk, which the edge
// runtime can't, and nothing in WERA runs on the edge.

import type { Instrumentation } from "next";

export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { registerTelemetry } = await import("./lib/server/telemetry");
  registerTelemetry();
}

export const onRequestError: Instrumentation.onRequestError = async (error, request, context) => {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { reportRequestError } = await import("./lib/server/telemetry");
  reportRequestError(error, request, context);
};
