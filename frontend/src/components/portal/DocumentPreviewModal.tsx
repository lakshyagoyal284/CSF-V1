"use client";

/**
 * Authenticated proof preview.
 *
 * The document endpoint requires a bearer token, which an `<iframe src>` cannot
 * send. The modal fetches the bytes with fetch(), renders them from an object
 * URL, and revokes that URL on close/unmount.
 */

import { useEffect, useState } from "react";

import { Spinner } from "@/components/portal/PortalShell";
import { portalApi, PortalApiError } from "@/lib/portal-api";
import type { DocumentProof } from "@/lib/portal-types";

export function DocumentPreviewModal({
  token,
  document,
  onClose,
}: {
  token: string;
  document: DocumentProof;
  onClose: () => void;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;

    (async () => {
      try {
        created = await portalApi.documentBlob(token, document.file_url);
        if (cancelled) {
          URL.revokeObjectURL(created);
          return;
        }
        setObjectUrl(created);
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof PortalApiError ? caught.message : "Could not load the document.",
          );
        }
      }
    })();

    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [token, document.file_url]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isPdf = document.content_type === "application/pdf";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-3 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={`Preview ${document.original_filename}`}
      onClick={onClose}
    >
      <div
        className="flex h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-slate-900">
              {document.original_filename}
            </p>
            <p className="text-[11px] text-slate-500">
              {document.doc_type.replaceAll("_", " ")} · {(document.size_bytes / 1024).toFixed(0)} KB ·{" "}
              {document.verified_status}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {objectUrl && (
              <a
                href={objectUrl}
                download={document.original_filename}
                className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] font-medium text-slate-600 transition hover:bg-slate-50"
              >
                Download
              </a>
            )}
            <button
              type="button"
              onClick={onClose}
              data-testid="preview-close"
              className="rounded-lg bg-slate-900 px-2.5 py-1.5 text-[11px] font-medium text-white transition hover:bg-slate-700"
            >
              Close
            </button>
          </div>
        </header>

        <div className="flex flex-1 items-center justify-center overflow-auto bg-slate-100 p-2">
          {error ? (
            <p className="text-sm text-rose-700">{error}</p>
          ) : !objectUrl ? (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <Spinner /> Loading document…
            </div>
          ) : isPdf ? (
            <iframe
              src={objectUrl}
              title={document.original_filename}
              className="h-full w-full rounded-lg border border-slate-200 bg-white"
            />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element -- blob URL, not an optimizable asset
            <img
              src={objectUrl}
              alt={document.original_filename}
              className="max-h-full max-w-full rounded-lg object-contain shadow"
            />
          )}
        </div>
      </div>
    </div>
  );
}
