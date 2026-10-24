/**
 * Thin typed client for the scoring API.
 *
 * All calls funnel through `request()` so FastAPI validation errors (which come
 * back as a `detail` string or a list of per-field errors) are turned into a
 * single readable message the loan officer can act on.
 */

import type {
  HealthResponse,
  ModelInfoResponse,
  SampleApplicant,
  ScoreRequestBody,
  ScoreResponse,
  SimulateRequestBody,
  SimulateResponse,
} from "./types";

// When NEXT_PUBLIC_API_BASE is unset we proxy /api/* through the Next.js
// dev server (next.config.ts rewrite), so the browser call is same-origin.
// The default must stay empty: every request path already starts with "/api",
// so defaulting to "/api" would produce /api/api/... and a 404.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly fields?: string[],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function describeDetail(detail: unknown): { message: string; fields?: string[] } {
  if (typeof detail === "string") return { message: detail };

  if (Array.isArray(detail)) {
    const fields = detail.map((item) => {
      const loc = Array.isArray((item as { loc?: unknown[] }).loc)
        ? (item as { loc: unknown[] }).loc.filter((p) => p !== "body").join(".")
        : "request";
      const msg = (item as { msg?: string }).msg ?? "invalid value";
      return `${loc}: ${msg}`;
    });
    return { message: "The application data failed validation.", fields };
  }

  if (detail && typeof detail === "object" && "message" in detail) {
    return { message: String((detail as { message: unknown }).message) };
  }
  return { message: "Unexpected error from the scoring service." };
}

function authHeaders(): Record<string, string> {
  // The legacy console is banker-only. Reuse the portal session token rather
  // than duplicating a second auth mechanism; on the server there is no token.
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem("agritrust.session");
    if (!raw) return {};
    const token = (JSON.parse(raw) as { token?: string }).token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      `Cannot reach the scoring API at ${API_BASE}. Start it with "uvicorn backend.main:app --port 8000".`,
      0,
    );
  }

  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      /* non-JSON error body (proxy, gateway) - fall through to the status text */
    }
    const detail = (payload as { detail?: unknown } | null)?.detail;
    const { message, fields } = describeDetail(detail);
    throw new ApiError(
      response.status === 503
        ? "The scoring model is not loaded yet. Run `python train_model.py` and reload the API."
        : message,
      response.status,
      fields,
    );
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  modelInfo: () => request<ModelInfoResponse>("/api/model/info"),
  samples: () => request<SampleApplicant[]>("/api/sample-applicants"),
  score: (body: ScoreRequestBody) =>
    request<ScoreResponse>("/api/score", { method: "POST", body: JSON.stringify(body) }),
  simulate: (body: SimulateRequestBody) =>
    request<SimulateResponse>("/api/simulate", { method: "POST", body: JSON.stringify(body) }),
};
