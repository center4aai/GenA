/** Longest dataset-derived part we allow in a queue name (the `queue_…_<ts>` wrapper adds ~21 chars). */
const MAX_SLUG_LENGTH = 200;

/**
 * Make a dataset run name safe to embed in a queue name that later travels as a
 * URL path segment and a Mongo key. Replaces `/ \ # ? %` and control characters
 * with `_`, collapses whitespace and trims to MAX_SLUG_LENGTH. Cyrillic, dashes,
 * dots, parentheses and commas are kept so the name stays readable.
 */
export function queueNameSlug(name: string): string {
  return name
    .replace(/\s+/g, ' ')
    .replace(/[/\\#?%\x00-\x1f\x7f]/g, '_')
    .trim()
    .slice(0, MAX_SLUG_LENGTH)
    .trim();
}
