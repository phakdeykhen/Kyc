export interface FlowStep {
  key: string;
  label: string;
  icon: string;
}

interface StepperProps {
  steps: FlowStep[];
  current: number;
}

export default function Stepper({ steps, current }: StepperProps) {
  return (
    <div className="flex items-center gap-1 overflow-x-auto pb-1">
      {steps.map((step, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={step.key} className="flex items-center">
            <div className="flex items-center gap-2">
              <span
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-sm transition-colors ${
                  done
                    ? "border-primary-500 bg-primary-500 text-background-50"
                    : active
                    ? "border-primary-500 bg-primary-50 text-primary-700"
                    : "border-background-300 bg-background-50 text-foreground-400"
                }`}
              >
                <i className={`${done ? "ri-check-line" : step.icon} text-base leading-none`}></i>
              </span>
              <span
                className={`hidden whitespace-nowrap font-label text-xs sm:block ${
                  active ? "font-medium text-foreground-950" : done ? "text-foreground-700" : "text-foreground-400"
                }`}
              >
                {step.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <span className={`mx-2 h-px w-5 shrink-0 sm:w-9 ${done ? "bg-primary-400" : "bg-background-300"}`}></span>
            )}
          </div>
        );
      })}
    </div>
  );
}