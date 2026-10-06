import { useEffect, useState } from "react";
import Modal from "@/components/base/Modal";
import type { ReviewAction } from "@/api/types";
import { humanize } from "@/pages/review/format";

export type { ReviewAction };

interface DecisionModalProps {
  action: ReviewAction | null;
  caseLabel: string;
  reasonCodes: string[];
  busy: boolean;
  error: string;
  onClose: () => void;
  onConfirm: (action: ReviewAction, reasonCode: string, note: string) => void;
}

const config: Record<ReviewAction, { title: string; subtitle: string; icon: string; confirmLabel: string; confirmClass: string }> = {
  APPROVE: {
    title: "Approve verification",
    subtitle: "The session becomes VERIFIED and your app is notified. Missing or failed required evidence cannot be approved.",
    icon: "ri-checkbox-circle-line",
    confirmLabel: "Approve",
    confirmClass: "bg-primary-500 hover:bg-primary-600 text-background-50",
  },
  REJECT: {
    title: "Reject verification",
    subtitle: "The session becomes REJECTED. This is final: the person must start a new session.",
    icon: "ri-close-circle-line",
    confirmLabel: "Reject",
    confirmClass: "bg-accent-600 hover:bg-accent-700 text-background-50",
  },
  REQUEST_RECAPTURE: {
    title: "Request recapture",
    subtitle: "The document photos and extracted data are cleared and the person is asked to capture again, with a fresh 15-minute window.",
    icon: "ri-camera-lens-line",
    confirmLabel: "Request recapture",
    confirmClass: "bg-secondary-500 hover:bg-secondary-600 text-background-50",
  },
};

const NOTE_MIN = 5;
const NOTE_MAX = 1000;

export default function DecisionModal({ action, caseLabel, reasonCodes, busy, error, onClose, onConfirm }: DecisionModalProps) {
  const [reasonCode, setReasonCode] = useState("");
  const [note, setNote] = useState("");
  const [localError, setLocalError] = useState("");

  useEffect(() => {
    setReasonCode("");
    setNote("");
    setLocalError("");
  }, [action]);

  if (!action) return null;
  const cfg = config[action];

  const confirm = () => {
    if (!reasonCode) return setLocalError("Choose a reason.");
    if (note.trim().length < NOTE_MIN) return setLocalError(`Write a note of at least ${NOTE_MIN} characters.`);
    setLocalError("");
    onConfirm(action, reasonCode, note.trim());
  };

  const message = localError || error;
  return (
    <Modal
      open
      onClose={busy ? () => undefined : onClose}
      title={cfg.title}
      subtitle={cfg.subtitle}
      icon={cfg.icon}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}
                  className="inline-flex items-center justify-center whitespace-nowrap rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100 disabled:opacity-60">
            Cancel
          </button>
          <button type="button" onClick={confirm} disabled={busy}
                  className={`inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md px-4 py-2.5 font-label text-sm font-medium transition-colors disabled:opacity-60 ${cfg.confirmClass}`}>
            <i className={`${busy ? "ri-loader-4-line animate-spin" : cfg.icon} text-base leading-none`}></i>
            {busy ? "Saving…" : cfg.confirmLabel}
          </button>
        </>
      }
    >
      <div className="rounded-md border border-background-200 bg-background-100/60 px-3 py-2.5">
        <div className="font-label text-xs text-foreground-500">Case</div>
        <div className="font-mono text-sm font-medium text-foreground-900">{caseLabel}</div>
      </div>

      <label className="mt-4 block font-label text-sm font-medium text-foreground-800" htmlFor="decision-reason">
        Reason<span className="ml-1 text-accent-600">*</span>
      </label>
      <select id="decision-reason" value={reasonCode} onChange={(e) => { setReasonCode(e.target.value); setLocalError(""); }}
              className="mt-1.5 w-full cursor-pointer rounded-md border border-background-300 bg-background-50 px-3 py-2 font-label text-sm text-foreground-900 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100">
        <option value="">Choose a reason…</option>
        {reasonCodes.map((code) => <option key={code} value={code}>{humanize(code)}</option>)}
      </select>

      <label className="mt-4 block font-label text-sm font-medium text-foreground-800" htmlFor="decision-note">
        Note<span className="ml-1 text-accent-600">*</span>
        <span className="ml-1 font-normal text-foreground-500">— stored encrypted, visible to reviewers only</span>
      </label>
      <textarea id="decision-note" value={note} rows={4} maxLength={NOTE_MAX}
                onChange={(e) => { setNote(e.target.value); setLocalError(""); }}
                placeholder="What did you check, and why this decision?"
                className="mt-1.5 w-full resize-none rounded-md border border-background-300 bg-background-50 px-3 py-2 font-label text-sm text-foreground-900 placeholder:text-foreground-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100" />
      <div className="mt-1 font-label text-[11px] text-foreground-500">{note.length}/{NOTE_MAX} characters</div>

      {message && (
        <p role="alert" className="mt-3 rounded-md border border-accent-200 bg-accent-50 px-3 py-2 font-label text-sm text-accent-800">{message}</p>
      )}
    </Modal>
  );
}
