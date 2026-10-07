import type { KeyboardEvent, ReactNode } from "react";
import StatusBadge from "@/components/base/StatusBadge";
import type { QueueItem, SessionListItem } from "@/api/types";
import { humanize, sessionStatusBadge, shortId, since } from "@/pages/review/format";

const th = "px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500";

function Empty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-background-300 bg-background-50 py-16">
      <span className="flex h-14 w-14 items-center justify-center rounded-full bg-secondary-100 text-secondary-600">
        <i className="ri-inbox-archive-line text-2xl leading-none"></i>
      </span>
      <div className="text-center">
        <p className="font-heading text-base font-semibold text-foreground-950">{title}</p>
        <p className="mt-1 font-label text-sm text-foreground-600">{detail}</p>
      </div>
    </div>
  );
}

function Frame({ children, minWidth }: { children: ReactNode; minWidth: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-background-200 bg-background-50">
      <div className="overflow-x-auto">
        <table className={`w-full ${minWidth} border-collapse text-left`}>{children}</table>
      </div>
    </div>
  );
}

const rowClass = "cursor-pointer border-b border-background-100 transition-colors last:border-0 hover:bg-background-100/70 focus-visible:bg-background-100 focus-visible:outline-none";

function rowProps(id: string, onOpen: (id: string) => void) {
  return {
    tabIndex: 0,
    onClick: () => onOpen(id),
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onOpen(id);
      }
    },
  };
}

export function QueueTable({ items, onOpen }: { items: QueueItem[]; onOpen: (id: string) => void }) {
  if (items.length === 0) {
    return <Empty title="No cases are waiting" detail="New cases appear here when the risk engine asks for a human check." />;
  }
  return (
    <Frame minWidth="min-w-[860px]">
      <thead>
        <tr className="border-b border-background-200 bg-background-100/60">
          <th className={`${th} md:px-5`}>Case</th>
          <th className={th}>Document</th>
          <th className={th}>Why it needs review</th>
          <th className={th}>Fraud signals</th>
          <th className={th}>Waiting</th>
          <th className={th}></th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.session_id} className={rowClass} {...rowProps(item.session_id, onOpen)}>
            <td className="px-4 py-3 md:px-5">
              <div className="font-mono text-sm font-medium text-foreground-950">{shortId(item.session_id)}</div>
              <div className="font-label text-[11px] text-foreground-500">{humanize(item.verification_level)}</div>
            </td>
            <td className="px-4 py-3">
              <div className="font-label text-sm text-foreground-800">{humanize(item.expected_document_type)}</div>
              <div className="font-label text-[11px] text-foreground-500">{item.country}</div>
            </td>
            <td className="px-4 py-3">
              <div className="flex max-w-xs flex-wrap gap-1">
                {item.reason_codes.slice(0, 3).map((code) => (
                  <span key={code} className="rounded bg-background-200 px-1.5 py-0.5 font-mono text-[10px] text-foreground-700">{code}</span>
                ))}
                {item.reason_codes.length > 3 && (
                  <span className="font-label text-[11px] text-foreground-500">+{item.reason_codes.length - 3}</span>
                )}
              </div>
            </td>
            <td className="px-4 py-3 font-label text-xs">
              {item.high_signals.length === 0 && item.medium_signals.length === 0 ? (
                <span className="inline-flex items-center gap-1 text-primary-700">
                  <i className="ri-shield-check-line text-sm leading-none"></i>None
                </span>
              ) : (
                <span className="flex flex-col gap-0.5">
                  {item.high_signals.length > 0 && <span className="text-accent-700">{item.high_signals.length} high</span>}
                  {item.medium_signals.length > 0 && <span className="text-foreground-600">{item.medium_signals.length} medium</span>}
                </span>
              )}
            </td>
            <td className="px-4 py-3 font-label text-xs text-foreground-700">{since(item.in_review_since)}</td>
            <td className="px-4 py-3 text-right">
              <i className="ri-arrow-right-s-line text-lg leading-none text-foreground-400"></i>
            </td>
          </tr>
        ))}
      </tbody>
    </Frame>
  );
}

export function AllSessionsTable({ items, onOpen }: { items: SessionListItem[]; onOpen: (id: string) => void }) {
  if (items.length === 0) {
    return <Empty title="No sessions match" detail="Try another status, or check the user ID (it must match exactly)." />;
  }
  return (
    <Frame minWidth="min-w-[860px]">
      <thead>
        <tr className="border-b border-background-200 bg-background-100/60">
          <th className={`${th} md:px-5`}>Session</th>
          <th className={th}>Customer user ID</th>
          <th className={th}>Document</th>
          <th className={th}>Status</th>
          <th className={th}>Last change</th>
          <th className={th}></th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.session_id} className={rowClass} {...rowProps(item.session_id, onOpen)}>
            <td className="px-4 py-3 md:px-5">
              <div className="font-mono text-sm font-medium text-foreground-950">{shortId(item.session_id)}</div>
              <div className="font-label text-[11px] text-foreground-500">{humanize(item.verification_level)}</div>
            </td>
            <td className="max-w-[220px] px-4 py-3">
              <div className="truncate font-label text-sm text-foreground-900" title={item.user_id}>{item.user_id}</div>
            </td>
            <td className="px-4 py-3">
              <div className="font-label text-sm text-foreground-800">{humanize(item.expected_document_type)}</div>
              <div className="font-label text-[11px] text-foreground-500">{item.country}</div>
            </td>
            <td className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <StatusBadge meta={sessionStatusBadge(item.status)} size="sm" />
                {item.erased && <span className="font-label text-[11px] text-foreground-500">data erased</span>}
                {!item.erased && item.expired && <span className="font-label text-[11px] text-accent-700">expired</span>}
              </div>
            </td>
            <td className="px-4 py-3 font-label text-xs text-foreground-700">{since(item.updated_at)} ago</td>
            <td className="px-4 py-3 text-right">
              <i className="ri-arrow-right-s-line text-lg leading-none text-foreground-400"></i>
            </td>
          </tr>
        ))}
      </tbody>
    </Frame>
  );
}
