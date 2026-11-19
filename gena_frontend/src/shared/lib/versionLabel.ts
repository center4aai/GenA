import { formatTimestamp, timestampMs } from '@/shared/lib/dateTime';
import type { Translate } from '@/shared/lib/labels';
import type { DatasetVersion } from '@/shared/types/documents';

/** When the version last changed for a person: a manual edit, else its creation. */
export function versionActivityAt(v: DatasetVersion): string {
  return String(v.user_updated_at ?? v.created_at ?? '');
}

function activityTime(v: DatasetVersion): number {
  return timestampMs(versionActivityAt(v)) ?? Number.NEGATIVE_INFINITY;
}

/**
 * The version the editor opens by default: the most recently created or
 * edited one, not necessarily `current_version`. Ties go to the higher number.
 */
export function latestVersion(versions: DatasetVersion[]): DatasetVersion | null {
  let best: DatasetVersion | null = null;
  for (const v of versions) {
    if (!best) {
      best = v;
      continue;
    }
    const diff = activityTime(v) - activityTime(best);
    if (diff > 0 || (diff === 0 && v.version > best.version)) best = v;
  }
  return best;
}

export function versionByNumber(versions: DatasetVersion[], n: number | null | undefined): DatasetVersion | null {
  if (n == null) return null;
  return versions.find((v) => v.version === n) ?? null;
}

/** "Версия 2 — изменена 2026-08-17 16:26:17" or "… — создана …" for untouched versions. */
export function versionOptionLabel(v: DatasetVersion, t: Translate): string {
  if (v.user_updated_at) {
    return t('results:versionModifiedAt', { n: v.version, at: formatTimestamp(v.user_updated_at) });
  }
  return t('results:versionCreatedAt', { n: v.version, at: formatTimestamp(v.created_at) });
}
