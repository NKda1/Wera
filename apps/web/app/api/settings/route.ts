import { forward } from "@/lib/server/agent";

/** Non-secret settings plus key status (set / missing). Key values never reach this app (SET-01, SET-07). */
export async function GET(req: Request) {
  return forward(req, "/settings");
}

export async function PUT(req: Request) {
  return forward(req, "/settings");
}
