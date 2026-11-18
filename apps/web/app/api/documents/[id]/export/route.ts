import { agent } from "@/lib/server/agent";
import { parseDocId, parseFormat, renderExport } from "@/lib/server/export";
import { ApiError, type IdContext } from "@/lib/server/http";
import { warn } from "@/lib/server/telemetry";

/**
 * EXP-01/02: every download passes through here, behind the approval gate, and contact details
 * are inserted at render time. The letter and both Word files are rendered here; the CV PDF is
 * rendered by the agent with RenderCV from the pack's CV file (18 Nov), and linted here like
 * the rest.
 */
export async function GET(req: Request, { params }: IdContext) {
  const { id } = await params;
  try {
    const { appId, kind } = parseDocId(id);
    const format = parseFormat(new URL(req.url).searchParams.get("format"));
    const [app, cv, contact, settings, file] = await Promise.all([
      agent.getApplication(appId),
      agent.getCv(),
      agent.getContact(),
      agent.getSettings(),
      kind === "cv" ? agent.cvFile(appId) : undefined,
    ]);
    const source = file ? { file, pdf: () => agent.cvPdf(appId) } : undefined;
    const out = await renderExport(kind, format, app, cv, contact, settings.cvPages, source);

    const failed = out.checks.filter((c) => !c.pass);
    if (failed.length) {
      // The person still gets their file; the format lint is advice, not a gate. In Logfire too,
      // so a template change that breaks extraction shows up before someone sends a bad PDF.
      console.warn(`[export] ${out.fileName}: ${failed.map((c) => `${c.id} — ${c.message}`).join("; ")}`);
      warn("Exported {kind} failed the format lint: {checks}", { kind, format, checks: failed.map((c) => c.id).join(",") });
    }

    return new Response(out.bytes as unknown as BodyInit, {
      headers: {
        "content-type": out.contentType,
        "content-length": String(out.bytes.byteLength),
        "content-disposition": `attachment; filename="${out.fileName}"`,
        "cache-control": "no-store",
        "x-wera-format-lint": failed.length ? failed.map((c) => c.id).join(",") : "pass",
      },
    });
  } catch (err) {
    if (err instanceof ApiError) return Response.json({ error: { code: err.code, message: err.message } }, { status: err.status });
    throw err;
  }
}
