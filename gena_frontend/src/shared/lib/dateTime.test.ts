import { describe, expect, it } from 'vitest';
import {
  compareTimestamps,
  formatTimestamp,
  formatTimestampDay,
  parseServerDate,
  timestampMs,
} from './dateTime';

const MSK = 'Europe/Moscow';

describe('parseServerDate', () => {
  it('reads an offset-less server timestamp as UTC, whatever the process zone', () => {
    expect(parseServerDate('2026-08-18T12:53:04.123000')?.toISOString()).toBe('2026-08-18T12:53:04.123Z');
    expect(parseServerDate('2026-08-18T12:53:04')?.toISOString()).toBe('2026-08-18T12:53:04.000Z');
  });

  it('honours an explicit offset and tolerates the Python space separator', () => {
    const utc = parseServerDate('2026-08-18T12:53:04Z')!.getTime();
    expect(parseServerDate('2026-08-18T15:53:04+03:00')!.getTime()).toBe(utc);
    expect(parseServerDate('2026-08-18 12:53:04')!.getTime()).toBe(utc);
    expect(parseServerDate(new Date(utc))!.getTime()).toBe(utc);
  });

  it('returns null for empty or unparseable input', () => {
    expect(parseServerDate(null)).toBeNull();
    expect(parseServerDate('')).toBeNull();
    expect(parseServerDate('garbage')).toBeNull();
  });
});

describe('formatTimestamp', () => {
  it('shifts the UTC digits into the display zone', () => {
    expect(formatTimestamp('2026-08-18T12:53:04.123000', MSK)).toBe('2026-08-18 15:53:04');
    expect(formatTimestamp('2026-08-18T12:53:04Z', MSK)).toBe('2026-08-18 15:53:04');
    expect(formatTimestamp('2026-08-18T15:53:04+03:00', MSK)).toBe('2026-08-18 15:53:04');
    expect(formatTimestamp('2026-08-18T12:53:04', 'UTC')).toBe('2026-08-18 12:53:04');
  });

  it('rolls the date over and prints midnight as 00, not 24', () => {
    expect(formatTimestamp('2026-08-18T22:30:00', MSK)).toBe('2026-08-19 01:30:00');
    expect(formatTimestamp('2026-08-18T21:00:00', MSK)).toBe('2026-08-19 00:00:00');
  });

  it('keeps the old behaviour for empties and unparseable strings', () => {
    expect(formatTimestamp(null)).toBe('');
    expect(formatTimestamp('')).toBe('');
    expect(formatTimestamp('garbage')).toBe('garbage');
  });

  it('buckets by the display day', () => {
    expect(formatTimestampDay('2026-08-18T22:30:00', MSK)).toBe('2026-08-19');
    expect(formatTimestampDay('2026-08-18T22:30:00', 'UTC')).toBe('2026-08-18');
    expect(formatTimestampDay('')).toBe('');
  });
});

describe('sorting helpers', () => {
  it('treats offset-less and Z timestamps of the same instant as equal', () => {
    expect(timestampMs('2026-08-18T12:00:00')).toBe(timestampMs('2026-08-18T12:00:00Z'));
    expect(compareTimestamps('2026-08-18T12:00:00', '2026-08-18T12:00:00Z')).toBe(0);
  });

  it('orders chronologically with empties last', () => {
    expect(compareTimestamps('2026-08-18T12:00:00', '2026-08-18T13:00:00')).toBeLessThan(0);
    expect(compareTimestamps('2026-08-18T13:00:00', '2026-08-18T12:00:00')).toBeGreaterThan(0);
    expect(compareTimestamps(null, '2026-08-18T12:00:00')).toBeGreaterThan(0);
    expect(compareTimestamps('2026-08-18T12:00:00', undefined)).toBeLessThan(0);
    expect(compareTimestamps(null, '')).toBe(0);
    expect(timestampMs('nope')).toBeNull();
  });
});
