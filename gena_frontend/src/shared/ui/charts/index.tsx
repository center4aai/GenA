import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

const PALETTE = ['#3f6b4f', '#c08a3e', '#2f8f83', '#8b5cf6', '#ef4444', '#0ea5e9', '#ec4899'];

interface ChartCardProps {
  title: string;
  children: React.ReactNode;
  emptyLabel?: string;
  isEmpty?: boolean;
}

function ChartCard({ title, children, emptyLabel = 'No data', isEmpty }: ChartCardProps) {
  return (
    <div className="card">
      <h3 className="font-semibold text-slate-900">{title}</h3>
      {isEmpty ? (
        <p className="mt-3 text-sm text-slate-500">{emptyLabel}</p>
      ) : (
        <div className="mt-4 h-64">{children}</div>
      )}
    </div>
  );
}

export interface CategoryDatum {
  label: string;
  value: number;
}

export function BarChartRc({
  title,
  data,
  color = PALETTE[0],
  emptyLabel,
}: {
  title: string;
  data: CategoryDatum[];
  color?: string;
  emptyLabel?: string;
}) {
  return (
    <ChartCard title={title} isEmpty={!data.length} emptyLabel={emptyLabel}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: -16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 12, fill: '#64748b' }} />
          <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: '#64748b' }} />
          <Tooltip />
          <Bar dataKey="value" fill={color} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function PieChartRc({
  title,
  data,
  emptyLabel,
}: {
  title: string;
  data: CategoryDatum[];
  emptyLabel?: string;
}) {
  return (
    <ChartCard title={title} isEmpty={!data.length} emptyLabel={emptyLabel}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="label" outerRadius={80} label>
            {data.map((_, i) => (
              <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
            ))}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function LineChartRc({
  title,
  data,
  color = PALETTE[0],
  ydomain,
  emptyLabel,
}: {
  title: string;
  data: CategoryDatum[];
  color?: string;
  ydomain?: [number, number];
  emptyLabel?: string;
}) {
  return (
    <ChartCard title={title} isEmpty={!data.length} emptyLabel={emptyLabel}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: -16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 12, fill: '#64748b' }} />
          <YAxis domain={ydomain} tick={{ fontSize: 12, fill: '#64748b' }} />
          <Tooltip />
          <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export interface ProgressSegment {
  label: string;
  value: number;
  colorClass: string;
}

/**
 * Multi-segment horizontal bar (pending / processing / completed / failed …)
 * so it's clear how a queue is moving. Segments are proportional to `value`.
 */
export function SegmentedProgressBar({ title, segments }: { title?: string; segments: ProgressSegment[] }) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  return (
    <div>
      {title && (
        <div className="mb-1 flex items-center justify-between text-sm">
          <span className="truncate text-slate-700">{title}</span>
          <span className="text-slate-400">{total} tasks</span>
        </div>
      )}
      <div className="flex h-3 overflow-hidden rounded-full bg-slate-100">
        {total > 0 &&
          segments
            .filter((s) => s.value > 0)
            .map((s) => (
              <div
                key={s.label}
                className={`h-full ${s.colorClass}`}
                style={{ width: `${(s.value / total) * 100}%` }}
                title={`${s.label}: ${s.value}`}
              />
            ))}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-slate-500">
        {segments
          .filter((s) => s.value > 0)
          .map((s) => (
            <span key={s.label} className="inline-flex items-center gap-1">
              <span className={`inline-block h-2 w-2 rounded-full ${s.colorClass}`} />
              {s.label} {s.value}
            </span>
          ))}
      </div>
    </div>
  );
}

/** Horizontal completed/remaining bar for a single dataset. */
export function StackedProgressBar({ label, percent }: { label: string; percent: number }) {
  const pct = Math.min(100, Math.max(0, percent));
  return (
    <div>
      <div className="mb-1 flex justify-between text-sm">
        <span className="truncate text-slate-600">{label}</span>
        <span className="font-medium text-slate-900">{pct.toFixed(1)}%</span>
      </div>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full bg-gena-primary transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
