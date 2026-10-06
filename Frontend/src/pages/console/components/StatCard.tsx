interface StatCardProps {
  label: string;
  value: string;
  delta: string;
  trend: "up" | "down" | "flat";
  icon: string;
  tone: "primary" | "accent" | "secondary";
}

const toneMap = {
  primary: "bg-primary-100 text-primary-800",
  accent: "bg-accent-100 text-accent-800",
  secondary: "bg-secondary-100 text-secondary-800",
};

export default function StatCard({ label, value, delta, trend, icon, tone }: StatCardProps) {
  const trendIcon =
    trend === "up" ? "ri-arrow-up-line" : trend === "down" ? "ri-arrow-down-line" : "ri-subtract-line";
  const trendColor =
    trend === "up" ? "text-primary-700" : trend === "down" ? "text-accent-700" : "text-foreground-500";

  return (
    <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
      <div className="flex items-start justify-between">
        <span className="font-label text-xs font-medium uppercase tracking-wide text-foreground-500">
          {label}
        </span>
        <span className={`flex h-8 w-8 items-center justify-center rounded-md ${toneMap[tone]}`}>
          <i className={`${icon} text-base leading-none`}></i>
        </span>
      </div>
      <div className="mt-3 font-heading text-2xl font-semibold text-foreground-950 md:text-3xl">{value}</div>
      <div className={`mt-1.5 flex items-center gap-1 font-label text-xs ${trendColor}`}>
        <i className={`${trendIcon} text-sm leading-none`}></i>
        {delta}
      </div>
    </div>
  );
}