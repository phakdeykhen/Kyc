import { useNavigate } from "react-router-dom";
import StatusBadge from "@/components/base/StatusBadge";
import { decisionMeta, getDocumentType, statusMeta, buildResult } from "@/lib/kycSimulation";
import { recentSessions } from "@/mocks/kyc";

export default function SessionsTable() {
  const navigate = useNavigate();

  const openSession = (id: string, reference: string, applicant: string, country: string, documentType: string) => {
    const result = findResult(id, reference, applicant, country, documentType);
    navigate(`/verify/${encodeURIComponent(id)}/done`, { state: { result } });
  };

  return (
    <div className="rounded-lg border border-background-200 bg-background-50">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-background-200 px-4 py-3.5 md:px-5">
        <div className="flex items-center gap-2">
          <h2 className="font-heading text-base font-semibold text-foreground-950">Recent sessions</h2>
          <span className="rounded-full bg-secondary-100 px-2 py-0.5 font-label text-xs text-secondary-700">
            last 24h
          </span>
        </div>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 px-3 py-1.5 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100"
        >
          <i className="ri-filter-3-line text-sm leading-none"></i>
          Filter
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-left">
          <thead>
            <tr className="border-b border-background-200">
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500 md:px-5">Applicant</th>
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Document</th>
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Level</th>
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Status</th>
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Decision</th>
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Time</th>
              <th className="px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody>
            {recentSessions.map((s) => {
              const dt = getDocumentType(s.documentType);
              return (
                <tr
                  key={s.id}
                  onClick={() =>
                    s.status === "MANUAL_REVIEW"
                      ? navigate(`/review/${s.id}`)
                      : openSession(s.id, s.reference, s.applicant, s.country, s.documentType)
                  }
                  className="cursor-pointer border-b border-background-100 transition-colors last:border-0 hover:bg-background-100/70"
                >
                  <td className="px-4 py-3 md:px-5">
                    <div className="font-label text-sm font-medium text-foreground-950">{s.applicant}</div>
                    <div className="font-mono text-[11px] text-foreground-500">{s.reference}</div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="font-label text-sm text-foreground-800">{dt.label}</div>
                    <div className="font-mono text-[11px] text-foreground-500">{s.id}</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded bg-secondary-100 px-2 py-0.5 font-label text-[11px] font-medium text-secondary-700">
                      {s.level}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge meta={statusMeta(s.status)} size="sm" />
                  </td>
                  <td className="px-4 py-3">
                    {s.decision ? <StatusBadge meta={decisionMeta(s.decision)} size="sm" /> : <span className="text-foreground-400">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    <div className="font-label text-xs text-foreground-700">{s.createdAt.slice(11)}</div>
                    <div className="font-label text-[11px] text-foreground-500">{s.duration}</div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <i className="ri-arrow-right-s-line text-lg leading-none text-foreground-400"></i>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function findResult(
  id: string,
  reference: string,
  applicant: string,
  country: string,
  documentType: string,
) {
  return buildResult({
    sessionId: id,
    reference,
    applicant,
    country,
    documentTypeId: documentType,
    level: "STANDARD",
  });
}