interface DemoToggleProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}

export default function DemoToggle({ enabled, onChange }: DemoToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      onClick={() => onChange(!enabled)}
      className={`inline-flex items-center gap-2 whitespace-nowrap rounded-full border px-3 py-1.5 font-label text-[11px] font-medium transition-colors ${
        enabled
          ? "border-accent-300 bg-accent-100 text-accent-900"
          : "border-background-300 bg-background-50 text-foreground-600 hover:bg-background-100"
      }`}
      title="Demo control: force the capture to fail the quality gate"
    >
      <span
        className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
          enabled ? "bg-accent-500" : "bg-background-300"
        }`}
      >
        <span
          className={`absolute h-3 w-3 rounded-full bg-background-50 transition-all ${
            enabled ? "left-3.5" : "left-0.5"
          }`}
        ></span>
      </span>
      <i className={`${enabled ? "ri-bug-2-line" : "ri-bug-line"} text-xs leading-none`}></i>
      Simulate poor capture
    </button>
  );
}