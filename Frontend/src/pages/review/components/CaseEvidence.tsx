import { useEffect, useState } from "react";
import StatusBadge from "@/components/base/StatusBadge";
import type { Credential } from "@/api/http";
import { reviewApi } from "@/api/review";
import type { ReviewCase } from "@/api/types";
import { decisionMeta } from "@/lib/badges";
import { CHECK_LABELS, checkBadge, dateTime, humanize } from "@/pages/review/format";

const card = "rounded-lg border border-background-200 bg-background-50 p-4 md:p-5";
const title = "font-heading text-sm font-semibold text-foreground-950";
type Reviewer = Extract<Credential, { kind: "reviewer" }>;

/** Document photos and selfie, fetched with the reviewer token (each view is audited; never cached). */
export function CaseImages({ credential, data }: { credential: Reviewer; data: ReviewCase }) {
  const [urls, setUrls] = useState<Record<string, string | null>>({});
  const canView = data.permissions.includes("VIEW_IMAGES");
  const sessionId = data.session.session_id;
  const imageKey = data.images.map((image) => image.image_id).join(",");

  useEffect(() => {
    if (!canView) return;
    let live = true;
    const created: string[] = [];
    setUrls({});
    data.images.forEach((image) => {
      reviewApi.image(credential, sessionId, image.image_id)
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          created.push(url);
          if (live) setUrls((current) => ({ ...current, [image.image_id]: url }));
        })
        .catch(() => live && setUrls((current) => ({ ...current, [image.image_id]: null })));
    });
    return () => {
      live = false;
      created.forEach((url) => URL.revokeObjectURL(url));
    };
    // imageKey stands for data.images
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [credential, sessionId, imageKey, canView]);

  return (
    <div className={card}>
      <div className="flex items-center justify-between">
        <h3 className={title}>Photos</h3>
        <span className="font-label text-[11px] text-foreground-500">Viewing a photo is recorded in the audit log</span>
      </div>
      {!canView ? (
        <p className="mt-3 font-label text-sm text-foreground-500">Your role cannot view photos.</p>
      ) : data.images.length === 0 ? (
        <p className="mt-3 font-label text-sm text-foreground-500">No photos are kept for this case (removed by retention, or never stored).</p>
      ) : (
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {data.images.map((image) => {
            const url = urls[image.image_id];
            const label = humanize(image.kind.replace("DOCUMENT_", "document "));
            return (
              <figure key={image.image_id} className="overflow-hidden rounded-lg border border-background-200">
                <div className="flex aspect-square w-full items-center justify-center bg-foreground-950/90">
                  {url ? (
                    <img src={url} alt={label} className={`h-full w-full ${image.kind === "SELFIE" ? "object-cover" : "object-contain"}`} />
                  ) : url === null ? (
                    <span className="font-label text-xs text-background-50/70">Not available</span>
                  ) : (
                    <i className="ri-loader-4-line animate-spin text-xl leading-none text-background-50/70"></i>
                  )}
                </div>
                <figcaption className="border-t border-background-200 bg-background-50 px-3 py-2 font-label text-[11px] text-foreground-600">
                  {label} · kept until {dateTime(image.available_until)}
                </figcaption>
              </figure>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function CaseFields({ data }: { data: ReviewCase }) {
  const canSee = data.permissions.includes("VIEW_IDENTITY");
  const doc = data.document;
  return (
    <div className="rounded-lg border border-background-200 bg-background-50">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-background-200 px-4 py-3.5 md:px-5">
        <h3 className={title}>Document and identity</h3>
        {!canSee && <span className="font-label text-[11px] text-foreground-500">Identity values hidden for your role</span>}
      </div>
      {doc && (
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 border-b border-background-100 px-4 py-3 sm:grid-cols-3 md:px-5">
          <Meta label="Document type" value={humanize(doc.type)} />
          <Meta label="Issuing country" value={doc.issuing_country ?? "—"} />
          <Meta label="Document number" value={doc.document_number ?? "—"} mono />
        </dl>
      )}
      {data.fields.length === 0 ? (
        <p className="px-4 py-4 font-label text-sm text-foreground-500 md:px-5">No extracted document data.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-left">
            <thead>
              <tr className="border-b border-background-200">
                {["Field", "Value", "Source", "Confidence"].map((head) => (
                  <th key={head} className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500 first:md:px-5">{head}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.fields.map((f) => (
                <tr key={`${f.name}-${f.side}`} className="border-b border-background-100 last:border-0 align-top">
                  <td className="px-4 py-3 md:px-5">
                    <div className="font-label text-sm text-foreground-900">{humanize(f.name)}</div>
                    {f.flags.length > 0 && <div className="mt-0.5 font-mono text-[10px] text-accent-700">{f.flags.join(", ")}</div>}
                  </td>
                  <td className={`px-4 py-3 text-sm ${f.name === "mrz" ? "font-mono text-xs" : "font-label"} ${canSee ? "text-foreground-950" : "italic text-foreground-400"}`}>
                    <span className="whitespace-pre-wrap break-all">{canSee ? (f.value ?? "—") : "hidden"}</span>
                  </td>
                  <td className="px-4 py-3 font-label text-xs text-foreground-600">{f.source}{f.side ? ` · ${humanize(f.side)}` : ""}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-background-200">
                        <div className={`h-full rounded-full ${f.confidence >= 0.9 ? "bg-primary-500" : "bg-accent-500"}`}
                             style={{ width: `${Math.round(f.confidence * 100)}%` }}></div>
                      </div>
                      <span className="font-mono text-xs text-foreground-700">{Math.round(f.confidence * 100)}%</span>
                    </div>
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

export function CaseChecks({ data }: { data: ReviewCase }) {
  const entries = Object.entries(data.checks).filter(([name]) => name !== "nfc_status").sort(([a], [b]) => a.localeCompare(b));
  return (
    <div className={card}>
      <h3 className={title}>Verification checks</h3>
      {entries.length === 0 ? (
        <p className="mt-3 font-label text-sm text-foreground-500">No checks recorded yet.</p>
      ) : (
        <div className="mt-3.5 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {entries.map(([name, value]) => (
            <div key={name} className="flex items-center justify-between gap-3 rounded-md border border-background-100 px-3 py-2.5">
              <span className="truncate font-label text-sm text-foreground-800">{CHECK_LABELS[name] ?? humanize(name)}</span>
              <StatusBadge meta={checkBadge(value)} size="sm" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function RiskPanel({ data }: { data: ReviewCase }) {
  const risk = data.risk;
  return (
    <div className={card}>
      <h3 className={title}>Risk engine</h3>
      {!risk ? (
        <p className="mt-3 font-label text-sm text-foreground-500">Not assessed yet.</p>
      ) : (
        <>
          <div className="mt-3 flex items-center gap-2">
            <StatusBadge meta={decisionMeta(risk.decision)} />
            <span className="font-label text-xs text-foreground-500">{dateTime(risk.assessed_at)}</span>
          </div>
          <div className="mt-3 rounded-md border border-background-200 bg-background-100/60 px-3 py-2.5">
            <div className="font-label text-xs text-foreground-500">Reason codes</div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {risk.reason_codes.length === 0 && <span className="font-label text-xs text-foreground-500">None</span>}
              {risk.reason_codes.map((code) => (
                <span key={code} className="rounded bg-background-200 px-2 py-0.5 font-mono text-[10px] text-foreground-700">{code}</span>
              ))}
            </div>
          </div>
          {risk.trace.length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer font-label text-xs font-medium text-primary-700">Rule trace ({risk.trace.length})</summary>
              <ol className="mt-2 flex flex-col gap-1 pl-4 font-mono text-[10px] text-foreground-600">
                {risk.trace.map((item, i) => <li key={i} className="list-decimal">{item.outcome} · {item.reason} ({item.rule})</li>)}
              </ol>
            </details>
          )}
          <p className="mt-3 font-mono text-[10px] text-foreground-400">policy {risk.policy_version}</p>
        </>
      )}
    </div>
  );
}

export function BiometricPanel({ data }: { data: ReviewCase }) {
  const { face_comparisons: faces, liveness, mrz, nfc, barcodes } = data;
  if (!faces.length && !liveness && !mrz && !nfc && !barcodes.length) return null;
  return (
    <div className={card}>
      <h3 className={title}>Evidence detail</h3>
      <div className="mt-3 flex flex-col gap-2">
        {faces.map((face, i) => (
          <Row key={`face-${i}`} label={`Face vs ${humanize(face.reference).toLowerCase()}`}
               value={`${face.result} · score ${face.score.toFixed(3)}${face.calibrated ? "" : " · uncalibrated"}`} />
        ))}
        {liveness && (
          <Row label="Liveness" value={`${liveness.result}${liveness.calibrated ? "" : " · uncalibrated"}${
            liveness.attack_type ? ` · ${humanize(liveness.attack_type)}` : ""}${
            liveness.reason_codes.length ? ` · ${liveness.reason_codes.join(", ")}` : ""}`} />
        )}
        {mrz && <Row label={`MRZ (${mrz.format})`} value={mrz.valid ? "Check digits valid" : "Check digits failed"} />}
        {nfc && <Row label="ePassport chip" value={`${humanize(nfc.status)}${nfc.reason_codes.length ? ` · ${nfc.reason_codes.join(", ")}` : ""}`} />}
        {barcodes.map((code, i) => (
          <Row key={`code-${i}`} label={`Barcode (${code.symbology})`}
               value={`${code.format_valid ? "format valid" : "format invalid"} · ${
                 code.signature_present ? (code.signature_valid ? "signature valid" : "signature INVALID") : "unsigned"}`} />
        ))}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-background-100 px-3 py-2.5">
      <div className="font-label text-xs text-foreground-500">{label}</div>
      <div className="mt-0.5 break-words font-label text-sm text-foreground-900">{value}</div>
    </div>
  );
}

function Meta({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="font-label text-[11px] text-foreground-500">{label}</dt>
      <dd className={`mt-0.5 text-sm text-foreground-900 ${mono ? "font-mono" : "font-label"}`}>{value}</dd>
    </div>
  );
}
