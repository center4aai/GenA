import { describe, expect, it } from 'vitest';
import { formatTimestamp } from './dateTime';
import { latestVersion, versionByNumber, versionOptionLabel } from './versionLabel';
import type { DatasetVersion } from '../types/documents';

const V: DatasetVersion[] = [
  { version: 1, created_at: '2026-08-17T10:00:00', user_updated_at: '2026-08-19T09:00:00' },
  { version: 2, created_at: '2026-08-18T12:53:04', user_updated_at: null },
];

describe('latestVersion', () => {
  it('prefers the most recent activity, edits included', () => {
    expect(latestVersion(V)?.version).toBe(1);
  });

  it('falls back to creation time when nothing was edited', () => {
    const untouched = V.map((v) => ({ ...v, user_updated_at: null }));
    expect(latestVersion(untouched)?.version).toBe(2);
  });

  it('breaks ties by the higher number and handles empty lists', () => {
    const same = [
      { version: 1, created_at: '2026-08-18T12:00:00' },
      { version: 2, created_at: '2026-08-18T12:00:00' },
    ];
    expect(latestVersion(same)?.version).toBe(2);
    expect(latestVersion([])).toBeNull();
  });

  it('reads offset-less server timestamps as UTC, so a Z-suffixed one ties with it', () => {
    const mixed = [
      { version: 1, created_at: '2026-08-18T12:00:00Z' },
      { version: 2, created_at: '2026-08-18T12:00:00' },
    ];
    expect(latestVersion(mixed)?.version).toBe(2);
    expect(latestVersion([...mixed].reverse())?.version).toBe(2);
  });
});

describe('versionOptionLabel', () => {
  const t = (key: string, opts?: Record<string, unknown>) => `${key}:${opts?.n}:${opts?.at}`;

  it('says modified for edited versions and created otherwise, in the viewer zone', () => {
    const modified = formatTimestamp(V[0].user_updated_at);
    const created = formatTimestamp(V[1].created_at);
    expect(modified).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(versionOptionLabel(V[0], t)).toBe(`results:versionModifiedAt:1:${modified}`);
    expect(versionOptionLabel(V[1], t)).toBe(`results:versionCreatedAt:2:${created}`);
  });

  it('finds a version by number', () => {
    expect(versionByNumber(V, 2)?.created_at).toBe('2026-08-18T12:53:04');
    expect(versionByNumber(V, 7)).toBeNull();
    expect(versionByNumber(V, null)).toBeNull();
  });
});
