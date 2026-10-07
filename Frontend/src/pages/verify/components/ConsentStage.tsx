import { useState } from "react";
import type { Client } from "@/api/client";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import { documentLabel } from "@/lib/catalog";
import type { DocumentTypeCode, SessionStatus } from "@/api/types";

interface ConsentStageProps {
  credential: Client;
  sessionId: string;
  documentType: DocumentTypeCode;
  onConsented: () => void;
  onSessionChanged: (status: SessionStatus) => void;
}

/** Document-processing consent, recorded from the person's own device before any upload (Phase 17). */
export default function ConsentStage({ credential, sessionId, documentType, onConsented, onSessionChanged }: ConsentStageProps) {
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await kycApi.consent(credential, sessionId);
      onConsented();
    } catch (caught) {
      const failure = caught as ApiError;
      if (failure.status === 409) return onSessionChanged("EXPIRED");
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-heading text-lg font-semibold text-foreground-950">Before you start</h2>
        <p className="mt-0.5 font-label text-sm text-foreground-600">You will photograph your {documentLabel(documentType)}, then take a selfie if asked.</p>
      </div>
      <ul className="flex flex-col gap-2.5 rounded-lg border border-background-200 bg-background-100/60 p-4 font-label text-sm text-foreground-700">
        <li className="flex gap-2"><i className="ri-sun-line mt-0.5 leading-none text-primary-600"></i>Find good, even light and a plain dark surface.</li>
        <li className="flex gap-2"><i className="ri-focus-3-line mt-0.5 leading-none text-primary-600"></i>Keep the whole document in the frame, without glare.</li>
        <li className="flex gap-2"><i className="ri-lock-2-line mt-0.5 leading-none text-primary-600"></i>Photos are stored encrypted and deleted after the retention period.</li>
      </ul>
      <label className="flex cursor-pointer items-start gap-3 rounded-md border border-background-200 p-3.5">
        <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)}
               className="mt-0.5 h-4 w-4 shrink-0 accent-[oklch(var(--primary-500))]" />
        <span className="font-label text-sm leading-relaxed text-foreground-700">
          I agree that my identity document is photographed and its details are read and checked to verify my identity.
        </span>
      </label>
      {error && <p role="alert" className="rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{error}</p>}
      <button type="button" disabled={!agreed || busy} onClick={submit}
              className="inline-flex items-center justify-center gap-2 self-start rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50">
        <i className={`${busy ? "ri-loader-4-line animate-spin" : "ri-arrow-right-line"} text-base leading-none`}></i>
        Agree and continue
      </button>
    </div>
  );
}
