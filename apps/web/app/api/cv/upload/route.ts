import { forward } from "@/lib/server/agent";
import { extractCvText } from "@/lib/server/cv-upload";
import { ApiError } from "@/lib/server/http";

/**
 * CV-03: upload a CV and get a proposed master CV back. Nothing is saved.
 *
 * The file is read here, never by the agent: PDF and DOCX extraction is a TypeScript concern
 * (ARCHITECTURE §4), and doing it here means the agent's API stays JSON-only and a 10 MB PDF
 * never crosses the loopback boundary.
 *
 * A YAML file still goes to `/cv/import`, which parses it directly — no model, no cost. Only a
 * PDF, DOCX or pasted text needs `/cv/parse`.
 */
export async function POST(req: Request) {
  let file: File | null = null;
  try {
    const form = await req.formData();
    const value = form.get("file");
    file = value instanceof File ? value : null;
  } catch {
    return Response.json({ error: { code: "BAD_REQUEST", message: "That upload couldn't be read. Try choosing the file again." } }, { status: 400 });
  }

  if (!file || !file.name) {
    return Response.json({ error: { code: "VALIDATION", message: "Choose a file to upload." } }, { status: 422 });
  }

  let kind: string;
  let text: string;
  try {
    ({ kind, text } = await extractCvText(file));
  } catch (err) {
    if (err instanceof ApiError) return Response.json({ error: { code: err.code, message: err.message } }, { status: err.status });
    // Anything else is a library failing on a file we can't characterise. Say which file, and
    // don't echo the internals (SPEC §9.4).
    return Response.json(
      { error: { code: "BAD_FILE", message: `WERA couldn't read "${file.name}". If it opens in a word processor, try exporting it as a PDF.` } },
      { status: 422 },
    );
  }

  const [path, body] = kind === "yaml" ? ["/cv/import", { yaml: text }] : ["/cv/parse", { text }];
  return forward(new Request(req.url, { method: "POST", body: JSON.stringify(body) }), path as string);
}
