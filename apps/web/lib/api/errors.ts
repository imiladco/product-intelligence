import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fieldErrors: Record<string, string[]>;

  constructor(status: number, body: ApiErrorBody | null) {
    super(body?.error?.message ?? "Something went wrong.");
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code ?? "server_error";
    this.fieldErrors = body?.error?.detail ?? {};
  }

  /** First message for a field, for rendering inline under an input. */
  fieldError(field: string): string | undefined {
    return this.fieldErrors[field]?.[0];
  }

  get isUnauthenticated(): boolean {
    return this.code === "not_authenticated" || this.status === 401;
  }
}
