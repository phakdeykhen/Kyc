import { useNavigate } from "react-router-dom";
import StatusBadge from "@/components/base/StatusBadge";
import type { SessionListItem } from "@/api/types";
import { statusMeta } from "@/lib/badges";
import { countryFlag, documentLabel } from "@/lib/catalog";
import { dateTime, humanize, shortId, since } from "@/pages/review/format";

interface SessionsTableProps {
  items: SessionListItem[] | null;
  total: number | null;
}

const th = "px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500";

export default function SessionsTable({ items, total }: SessionsTableProps) {
  const navigate = useNavigate();
  const open = (item: SessionListItem) =>
    navigate(item.status === "MANUAL_REVIEW" ? `/review/${item.session_id}` : `/sessions/${item.session_id}`);

  return (
    <div className="rounded-lg border border-background-200 bg-background-50">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-background-200 px-4 py-3.5 md:px-5">
        <div className="flex items-center gap-2">
          <h2 className="font-heading text-base font-semibold text-foreground-950">Recent sessions</h2>
          <span className="rounded-full bg-secondary-100 px-2 py-0.5 font-label text-xs text-secondary-700">latest change first</span>
        </div>
        <button type="button" onClick={() => navigate("/review?view=all")}
                className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 px-3 py-1.5 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100">
          <i className="ri-filter-3-line text-sm leading-none"></i>
          {total !== null ? `All ${total.toLocaleString()} sessions` : "All sessions"}
        </button>
      </div>

      {!items ? (
        <div className="flex items-center justify-center gap-2 py-12 font-label text-sm text-foreground-500">
          <i className="ri-loader-4-line animate-spin text-base leading-none"></i>Loading…
        </div>
      ) : items.length === 0 ? (
        <div className="py-12 text-center font-label text-sm text-foreground-500">No sessions yet. Start a verification to create one.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-left">
            <thead>
              <tr className="border-b border-background-200">
                <th className={`${th} md:px-5`}>User ID</th>
                <th className={th}>Document</th>
                <th className={th}>Level</th>
                <th className={th}>Status</th>
                <th className={th}>Updated</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.session_id} onClick={() => open(item)}
                    className="cursor-pointer border-b border-background-100 transition-colors last:border-0 hover:bg-background-100/70">
                  <td className="px-4 py-3 md:px-5">
                    <div className="font-mono text-sm font-medium text-foreground-950">{item.user_id}</div>
                    <div className="font-mono text-[11px] text-foreground-500">{shortId(item.session_id)}</div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="font-label text-sm text-foreground-800">{countryFlag(item.country)} {documentLabel(item.expected_document_type)}</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded bg-secondary-100 px-2 py-0.5 font-label text-[11px] font-medium text-secondary-700">
                      {humanize(item.verification_level)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <StatusBadge meta={statusMeta(item.status)} size="sm" />
                      {item.erased && <span className="rounded-full bg-background-200 px-2 py-0.5 font-label text-[10px] text-foreground-600">erased</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3" title={dateTime(item.updated_at)}>
                    <div className="font-label text-xs text-foreground-700">{since(item.updated_at)} ago</div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <i className="ri-arrow-right-s-line text-lg leading-none text-foreground-400"></i>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
