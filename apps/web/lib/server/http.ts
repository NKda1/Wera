import "server-only";

// Errors leave as `{ error: { code, message } }` with a stable code (SPEC §9.4).

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

export type IdContext = { params: Promise<{ id: string }> };
