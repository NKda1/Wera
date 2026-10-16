import "server-only";

// Route-handler helpers. Errors always leave as `{ error: { code, message } }` with a
// stable code (SPEC §9.4); unexpected errors never echo internals to the browser.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields?: Record<string, string>,
  ) {
    super(message);
  }
}

export async function handle<T>(fn: () => T | Promise<T>, init?: ResponseInit): Promise<Response> {
  try {
    const result = await fn();
    return Response.json(result, init);
  } catch (err) {
    if (err instanceof ApiError) {
      return Response.json({ error: { code: err.code, message: err.message, fields: err.fields } }, { status: err.status });
    }
    console.error("[wera] unhandled route error", err instanceof Error ? err.message : "unknown");
    return Response.json({ error: { code: "INTERNAL", message: "Something went wrong on the server." } }, { status: 500 });
  }
}

export async function readJson<T = unknown>(req: Request): Promise<T> {
  try {
    return (await req.json()) as T;
  } catch {
    throw new ApiError(400, "BAD_JSON", "The request body isn't valid JSON.");
  }
}

export type IdContext = { params: Promise<{ id: string }> };
