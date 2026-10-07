interface StatCardProps {
  label: string;
  value: number | null | undefined;
  note?: string;
  icon: string;
  tone: "primary" | "accent" | "secondary";
}

const toneMap = {
  primary: "bg-primary-100 text-primary-800",
  accent: "bg-accent-100 text-accent-800",
  secondary: "bg-secondary-100 text-secondary-800",
};

export default function StatCard({ label, value, note, icon, tone }: StatCardProps) {
  return (
    <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
      <div className="flex items-start justify-between gap-2">
        <span className="font-label text-xs font-medium uppercase tracking-wide text-foreground-500">{label}</span>
        <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${toneMap[tone]}`}>
          <i className={`${icon} text-base leading-none`}></i>
        </span>
      </div>
      <div className="mt-3 font-heading text-2xl font-semibold text-foreground-950 md:text-3xl">
        {value === null || value === undefined ? "—" : value.toLocaleString()}
      </div>
      {note && <div className="mt-1.5 font-label text-xs text-foreground-500">{note}</div>}
    </div>
  );
}
