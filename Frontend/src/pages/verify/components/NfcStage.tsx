import { useState } from "react";
import type { Client } from "@/api/client";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import type { SessionStatus } from "@/api/types";

interface NfcStageProps {
  credential: Client;
  sessionId: string;
  onSessionChanged: (status: SessionStatus) => void;
  onRefresh: () => void;
}

/**
 * The ePassport chip step. A browser cannot open a passport chip (Web NFC only reads NDEF tags), so the
 * mobile app performs PACE/BAC and posts the chip files. From here the person can either finish in the
 * app, or report that their device cannot read the chip; the risk engine then decides without chip
 * evidence (which keeps the case out of automatic approval).
 */
export default function NfcStage({ credential, sessionId, onSessionChanged, onRefresh }: NfcStageProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const noReader = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await kycApi.reportNfcUnavailable(credential, sessionId, "NOT_SUPPORTED");
      onSessionChanged(result.status);
    } catch (caught) {
      const failure = caught as ApiError;
      if (failure.status === 409) return onRefresh();
      setError(failure.status === 429 ? "No chip attempts left for this session." : failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-heading text-lg font-semibold text-foreground-950">Read your passport chip</h2>
        <p className="mt-0.5 font-label text-sm text-foreground-600">
          Your passport has a chip that proves it was issued by your country and not copied or changed.
        </p>
      </div>
      <div className="flex flex-col items-center gap-4 rounded-xl border border-background-200 bg-background-100/60 px-6 py-10 text-center">
        <span className="flex h-16 w-16 items-center justify-center rounded-full bg-primary-100 text-primary-700">
          <i className="ri-smartphone-line text-3xl leading-none"></i>
        </span>
        <p className="max-w-md font-label text-sm text-foreground-700">
          Open the mobile app from the service that sent you, and hold your passport against the back of your phone when it asks.
          Then come back here and check progress.
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <button type="button" onClick={onRefresh}
                  className="inline-flex items-center justify-center gap-2 rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 hover:bg-primary-600">
            <i className="ri-refresh-line text-base leading-none"></i>Check progress
          </button>
          <button type="button" onClick={noReader} disabled={busy}
                  className="inline-flex items-center justify-center gap-2 rounded-md border border-background-300 bg-background-50 px-5 py-2.5 font-label text-sm font-medium text-foreground-800 hover:bg-background-100 disabled:opacity-50">
            <i className={`${busy ? "ri-loader-4-line animate-spin" : "ri-forbid-line"} text-base leading-none`}></i>
            My phone cannot read the chip
          </button>
        </div>
        <p className="max-w-md font-label text-xs text-foreground-500">
          Without the chip, your verification continues but a member of staff may need to check it.
        </p>
      </div>
      {error && <p role="alert" className="rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{error}</p>}
    </div>
  );
}
