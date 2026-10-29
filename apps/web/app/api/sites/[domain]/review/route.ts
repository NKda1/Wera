import { forward, seg } from "@/lib/server/agent";

/**
 * SRCH-04/12: record that a person decided about a board WERA proposed.
 *
 * Approving is an assertion that they read the site's terms and robots.txt, which is why it is a
 * deliberate POST rather than a config edit.
 */
export async function POST(req: Request, { params }: { params: Promise<{ domain: string }> }) {
  const { domain } = await params;
  return forward(req, `/sites/${seg(domain)}/review`);
}
