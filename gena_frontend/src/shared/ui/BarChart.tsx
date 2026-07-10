interface BarChartProps {
  title: string;
  data: Record<string, number>;
  emptyLabel?: string;
  colorClass?: string;
}

export function BarChart({ title, data, emptyLabel = 'No data', colorClass = 'bg-gena-primary' }: BarChartProps) {
  const entries = Object.entries(data).filter(([, v]) => v > 0);
  const max = Math.max(...entries.map(([, v]) => v), 1);

  return (
    <div className="card">
      <h3 className="font-semibold text-slate-900">{title}</h3>
      {entries.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">{emptyLabel}</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {entries.map(([label, value]) => (
            <li key={label}>
              <div className="mb-1 flex justify-between text-sm">
                <span className="text-slate-600">{label}</span>
                <span className="font-medium text-slate-900">{value}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                <div
                  className={`h-full rounded-full transition-all ${colorClass}`}
                  style={{ width: `${(value / max) * 100}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
