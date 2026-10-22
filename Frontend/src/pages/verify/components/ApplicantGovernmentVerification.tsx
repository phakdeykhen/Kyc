import { useEffect, useState } from "react";
import { kycApi, type Client } from "@/api/client";
import type { GovernmentVerification } from "@/api/types";
import GovernmentVerificationPanel from "@/components/feature/GovernmentVerificationPanel";

export default function ApplicantGovernmentVerification({ credential, sessionId, version }:
  { credential: Client; sessionId: string; version: number }) {
  const [data, setData] = useState<GovernmentVerification | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setLoading(true);
    setError(false);
    kycApi.governmentVerification(credential, sessionId, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setData(result); })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [credential, sessionId, version, attempt]);

  return <GovernmentVerificationPanel data={data} loading={loading} error={error} onRetry={() => setAttempt((value) => value + 1)} />;
}
