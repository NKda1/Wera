import { agent } from "@/lib/server/agent";
import { lintExport, parseDocId } from "@/lib/server/export";
import { ApiError, type IdContext } from "@/lib/server/http";

/**
 * ATSC-05/06 for the review rail: the format lint, run on the PDF as it would render now — for the
 * CV, the agent's RenderCV render of its file. The file itself is never returned — that stays
 * behind the approval gate in `../export`.
 */
export async function GET(_req: Request, { params }: IdContext) {
  const { id } = await params;
  try {
    const { appId, kind } = parseDocId(id);
    const [app, cv, contact, settings, file] = await Promise.all([
      agent.getApplication(appId),
      agent.getCv(),
      agent.getContact(),
      agent.getSettings(),
      kind === "cv" ? agent.cvFile(appId) : undefined,
    ]);
    const source = file ? { file, pdf: () => agent.cvPdf(appId) } : undefined;
    return Response.json({ checks: await lintExport(kind, app, cv, contact, settings.cvPages, source) }, { headers: { "cache-control": "no-store" } });
  } catch (err) {
    if (err instanceof ApiError) return Response.json({ error: { code: err.code, message: err.message } }, { status: err.status });
    throw err;
  }
}
