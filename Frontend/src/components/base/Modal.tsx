import { useEffect, type ReactNode } from "react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  icon?: string;
  children: ReactNode;
  footer?: ReactNode;
  maxWidthClass?: string;
}

export default function Modal({
  open,
  onClose,
  title,
  subtitle,
  icon = "ri-question-line",
  children,
  footer,
  maxWidthClass = "max-w-lg",
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-foreground-950/50 backdrop-blur-sm" onClick={onClose}></div>
      <div
        role="dialog"
        aria-modal="true"
        className={`relative z-10 w-full ${maxWidthClass} animate-modal-in rounded-lg border border-background-200 bg-background-50 shadow-none`}
      >
        <div className="flex items-start gap-3 border-b border-background-200 px-5 py-4">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-100 text-primary-700">
            <i className={`${icon} text-lg leading-none`}></i>
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="font-heading text-base font-semibold text-foreground-950">{title}</h3>
            {subtitle && <p className="mt-0.5 font-label text-sm text-foreground-600">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-md text-foreground-500 transition-colors hover:bg-background-100 hover:text-foreground-800"
          >
            <i className="ri-close-line text-xl leading-none"></i>
          </button>
        </div>

        <div className="max-h-[60vh] overflow-y-auto px-5 py-4">{children}</div>

        {footer && (
          <div className="flex flex-col-reverse gap-2 border-t border-background-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-end">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}