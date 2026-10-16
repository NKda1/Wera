import { handle } from "@/lib/server/http";
import { testProvider } from "@/lib/server/store";

export async function POST(_req: Request, { params }: { params: Promise<{ provider: string }> }) {
  const { provider } = await params;
  return handle(() => testProvider(provider));
}
