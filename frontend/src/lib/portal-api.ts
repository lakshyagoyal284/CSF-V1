/**
 * Typed client for the AgriTrust dual-portal API.
 *
 * Every call goes through `request()`, which attaches the bearer token and
 * normalises FastAPI's error shapes into one readable message plus optional
 * field errors. Multipart submissions deliberately do not set Content-Type:
 * the browser must add the multipart boundary itself.
 */

import { API_BASE } from "./api";
import type {
  ApplicationPayload,
  ApplicationListResponse,
  AuditEntry,
  AuthResponse,
  BankerApplicationDetail,
  BorrowerApplication,
  PortalUser,
  ScoreResult,
  UploadFileEntry,
  VerifyAndScorePayload,
} from "./portal-types";

export { API_BASE };

export class PortalApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly fields: string[] = [],
  ) {
    super(message);
    this.name = "PortalApiError";
  }
}

interface RequestOptions {
  token?: string | null;
  method?: "GET" | "POST";
  json?: unknown;
  form?: FormData;
}

function describeError(detail: unknown): { message: string; fields: string[] } {
  if (typeof detail === "string") return { message: detail, fields: [] };

  if (Array.isArray(detail)) {
    const fields = detail.map((item) => {
      const entry = item as { loc?: unknown[]; msg?: string };
      const location = Array.isArray(entry.loc)
        ? entry.loc.filter((part) => part !== "body").join(".")
        : "request";
      return `${location}: ${entry.msg ?? "invalid value"}`;
    });
    return { message: "Please check the highlighted fields.", fields };
  }

  if (detail && typeof detail === "object") {
    const payload = detail as {
      message?: string;
      fields?: Array<{ field?: string; message?: string }>;
    };
    const fields = (payload.fields ?? []).map((item) =>
      item.field ? `${item.field}: ${item.message ?? "invalid value"}` : String(item.message),
    );
    return {
      message: payload.message ?? "The request could not be completed.",
      fields,
    };
  }

  return { message: "Unexpected error from the AgriTrust service.", fields: [] };
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  if (options.json !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.form ?? (options.json !== undefined ? JSON.stringify(options.json) : undefined),
      cache: "no-store",
    });
  } catch {
    throw new PortalApiError(
      `Cannot reach the AgriTrust API at ${API_BASE}. Start it with "uvicorn main:app --port 8000".`,
      0,
    );
  }

  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      /* non-JSON error body */
    }
    const detail = (payload as { detail?: unknown } | null)?.detail;
    const { message, fields } = describeError(detail);
    const friendly =
      response.status === 401
        ? "Your session is invalid or has expired. Please sign in again."
        : response.status === 503
          ? "The scoring model is not loaded. Run `python train_model.py` and restart the API."
          : message;
    throw new PortalApiError(friendly, response.status, fields);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function authHeaders(token: string | null): RequestOptions {
  return { token };
}

export const portalApi = {
  // -- auth ---------------------------------------------------------------
  login: (email: string, password: string) =>
    request<AuthResponse>("/api/auth/login", {
      method: "POST",
      json: { email, password },
    }),

  register: (input: { email: string; password: string; full_name: string; phone?: string }) =>
    request<AuthResponse>("/api/auth/register", { method: "POST", json: input }),

  me: (token: string) => request<PortalUser>("/api/auth/me", authHeaders(token)),

  // -- borrower -----------------------------------------------------------
  submitApplication: (
    token: string,
    payload: ApplicationPayload,
    uploads: UploadFileEntry[],
  ) => {
    const form = new FormData();
    form.append("payload", JSON.stringify(payload));
    uploads.forEach((entry) => {
      form.append("documents", entry.file, entry.file.name);
      form.append("doc_types", entry.docType);
    });
    return request<BorrowerApplication>("/api/borrower/application", {
      token,
      method: "POST",
      form,
    });
  },

  myApplications: (token: string) =>
    request<BorrowerApplication[]>("/api/borrower/applications", authHeaders(token)),

  myApplication: (token: string, id: string) =>
    request<BorrowerApplication>(`/api/borrower/applications/${id}`, authHeaders(token)),

  // -- banker -------------------------------------------------------------
  bankerApplications: (token: string, status = "pending", limit = 100) =>
    request<ApplicationListResponse>(
      `/api/banker/applications?status=${encodeURIComponent(status)}&limit=${limit}`,
      authHeaders(token),
    ),

  bankerApplication: (token: string, id: string) =>
    request<BankerApplicationDetail>(`/api/banker/applications/${id}`, authHeaders(token)),

  verifyAndScore: (token: string, id: string, payload: VerifyAndScorePayload) =>
    request<ScoreResult>(`/api/banker/verify-and-score/${id}`, {
      token,
      method: "POST",
      json: payload,
    }),

  latestScore: (token: string, id: string) =>
    request<ScoreResult>(`/api/banker/applications/${id}/score`, authHeaders(token)),

  scoreHistory: (token: string, id: string) =>
    request<ScoreResult[]>(`/api/banker/applications/${id}/history`, authHeaders(token)),

  rejectDocument: (token: string, id: string, documentId: string, reason: string) =>
    request<BankerApplicationDetail>(
      `/api/banker/applications/${id}/documents/${documentId}/reject`,
      { token, method: "POST", json: { reason } },
    ),

  reject: (token: string, id: string, reason: string) =>
    request<BankerApplicationDetail>(`/api/banker/applications/${id}/reject`, {
      token,
      method: "POST",
      json: { reason },
    }),

  auditTrail: (token: string, id: string) =>
    request<{ items: AuditEntry[] }>(`/api/banker/applications/${id}/audit`, authHeaders(token)),

  createDemoApplication: (token: string) =>
    request<BankerApplicationDetail>("/api/banker/demo-applications", {
      token,
      method: "POST",
    }),

  /**
   * Fetch the decision letter as a blob URL. The caller triggers the download
   * and revokes the URL afterwards (a plain link cannot send the bearer token).
   */
  decisionLetter: async (token: string, id: string): Promise<string> => {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}/api/banker/applications/${id}/decision-letter`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
    } catch {
      throw new PortalApiError(`Cannot reach the document store at ${API_BASE}.`, 0);
    }
    if (!response.ok) {
      throw new PortalApiError("The decision letter could not be generated.", response.status);
    }
    return URL.createObjectURL(await response.blob());
  },

  /**
   * Fetch document bytes with the bearer token.
   *
   * A plain `<img src>`/`<iframe src>` cannot send an Authorization header, so
   * previews are loaded as blobs and rendered from an object URL. The caller
   * must revoke that URL when the preview closes.
   */
  documentBlob: async (token: string, fileUrl: string): Promise<string> => {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}${fileUrl}`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
    } catch {
      throw new PortalApiError(`Cannot reach the document store at ${API_BASE}.`, 0);
    }
    if (!response.ok) {
      throw new PortalApiError("This document could not be loaded.", response.status);
    }
    return URL.createObjectURL(await response.blob());
  },
};
