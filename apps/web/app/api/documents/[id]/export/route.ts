import { agent } from "@/lib/server/agent";
import { parseDocId, parseFormat, renderExport } from "@/lib/server/export";
import { ApiError, type IdContext } from "@/lib/server/http";

/**
 * EXP-01/02: rendering happens here, on the server, and contact details are inserted at
 * render time (ARCHITECTURE §4 keeps document rendering in TypeScript). The agent holds the
 * pack, the master CV and the contact details; it never renders.
 */
export async function GET(req: Request, { params }: IdContext) {
  const { id } = await params;
  try {
    const { appId, kind } = parseDocId(id);
    const format = parseFormat(new URL(req.url).searchParams.get("format"));
    const [app, cv, contact, settings] = await Promise.all([agent.getApplication(appId), agent.getCv(), agent.getContact(), agent.getSettings()]);
    const out = await renderExport(kind, format, app, cv, contact, settings.cvPages);

    const failed = out.checks.filter((c) => !c.pass);
    if (failed.length) {
      // The person still gets their file; the format lint is advice, not a gate.
      console.warn(`[export] ${out.fileName}: ${failed.map((c) => `${c.id} — ${c.message}`).join("; ")}`);
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
