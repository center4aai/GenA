interface MetricCardProps {
  label: string;
  value: string | number;
  hint?: string;
  tone?: 'default' | 'success' | 'warning' | 'danger';
}

const toneClasses = {
  default: 'border-slate-200 bg-white',
  success: 'border-emerald-200 bg-emerald-50/50',
  warning: 'border-amber-200 bg-amber-50/50',
  danger: 'border-red-200 bg-red-50/50',
};

export function MetricCard({ label, value, hint, tone = 'default' }: MetricCardProps) {
  return (
    <div className={`card text-center ${toneClasses[tone]}`}>
      <div className="text-2xl font-bold tracking-tight text-slate-900">{value}</div>
      <div className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      {hint && <div className="mt-1 text-xs text-slate-400">{hint}</div>}
    </div>
  );
}
