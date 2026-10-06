import type { BadgeMeta } from "@/lib/badges";

interface StatusBadgeProps {
  meta: BadgeMeta;
  size?: "sm" | "md";
}

export default function StatusBadge({ meta, size = "md" }: StatusBadgeProps) {
  const pad = size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-label font-medium whitespace-nowrap ${pad} ${meta.className}`}
    >
      <i className={`${meta.icon} text-[13px] leading-none`}></i>
      {meta.label}
    </span>
  );
}