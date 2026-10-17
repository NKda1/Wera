import { forward, seg } from "@/lib/server/agent";

/** SET-02: checks a provider connection from the agent service and returns a sanitised result. */
export async function POST(req: Request, { params }: { params: Promise<{ provider: string }> }) {
  const { provider } = await params;
  return forward(req, `/settings/test/${seg(provider)}`);
}
