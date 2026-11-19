/**
 * Timestamps from dataset_api / agent_api are `datetime.utcnow()` serialised
 * without an offset ("2026-08-18T12:53:04.123000"): the digits are UTC even
 * though nothing in the string says so. Everything here treats an offset-less
 * string as UTC and renders in the viewer's own time zone (or an explicit one,
 * which tests use to stay deterministic).
 */

const HAS_OFFSET = /(Z|[+-]\d{2}:?\d{2})$/i;

/** `Date` for a server timestamp; `null` for empty or unparseable input. */
export function parseServerDate(value: unknown): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  let s = String(value ?? '').trim();
  if (!s) return null;
  // Python's str(datetime) separates date and time with a space.
  s = s.replace(/^(\d{4}-\d{2}-\d{2}) (\d)/, '$1T$2');
  // JS Date reads at most three fractional digits; Python writes six.
  s = s.replace(/(\.\d{3})\d+/, '$1');
  if (s.includes('T') && !HAS_OFFSET.test(s)) s += 'Z';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(timeZone?: string): Intl.DateTimeFormat {
  const key = timeZone ?? '';
  let f = formatters.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat('en-GB', {
      ...(timeZone ? { timeZone } : {}),
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    formatters.set(key, f);
  }
  return f;
}

function dateParts(d: Date, timeZone?: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of formatter(timeZone).formatToParts(d)) {
    if (p.type !== 'literal') out[p.type] = p.value;
  }
  return out;
}

/** "YYYY-MM-DD HH:mm:ss" in the viewer's zone. Unparseable input is shown as before: cut to seconds. */
export function formatTimestamp(value: unknown, timeZone?: string): string {
  if (value == null || value === '') return '';
  const d = parseServerDate(value);
  if (!d) return String(value).slice(0, 19).replace('T', ' ');
  const p = dateParts(d, timeZone);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`;
}

/** "YYYY-MM-DD" in the viewer's zone — day buckets for charts. */
export function formatTimestampDay(value: unknown, timeZone?: string): string {
  if (value == null || value === '') return '';
  const d = parseServerDate(value);
  if (!d) return String(value).slice(0, 10);
  const p = dateParts(d, timeZone);
  return `${p.year}-${p.month}-${p.day}`;
}

/** Epoch milliseconds for sorting; `null` sorts last in `useSort`. */
export function timestampMs(value: unknown): number | null {
  const d = parseServerDate(value);
  return d ? d.getTime() : null;
}

/** Chronological comparator for `Array.sort`; empties go last. */
export function compareTimestamps(a: unknown, b: unknown): number {
  const ta = timestampMs(a);
  const tb = timestampMs(b);
  if (ta === tb) return 0;
  if (ta == null) return 1;
  if (tb == null) return -1;
  return ta - tb;
}
