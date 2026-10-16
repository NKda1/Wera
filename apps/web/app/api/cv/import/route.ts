import { ApiError, handle, readJson } from "@/lib/server/http";
import { importCvYaml } from "@/lib/server/store";

/** Returns a proposed CV; nothing is saved until PUT /api/cv (CV-03). */
export async function POST(req: Request) {
  return handle(async () => {
    const body = await readJson<{ yaml?: string }>(req);
    if (typeof body.yaml !== "string" || !body.yaml.trim()) throw new ApiError(422, "VALIDATION", "Paste or choose a YAML file.");
    return importCvYaml(body.yaml);
  });
}
