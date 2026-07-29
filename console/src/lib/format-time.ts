/**
 * Format a backend-supplied timestamp safely.
 * Backends sometimes return:
 *  - Unix seconds (10 digits, e.g. 1751206338)
 *  - Unix milliseconds (13 digits)
 *  - ISO 8601 strings
 *  - null / undefined / "" / 0
 *
 * Returns "—" for empty values rather than the epoch ("1/1/1970").
 */
export function formatSyncTime(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";

  let ms: number | null = null;

  if (typeof value === "number") {
    if (!Number.isFinite(value) || value <= 0) return "—";
    // Heuristic: anything below year 2001 in ms (< ~1e12) is almost certainly seconds.
    ms = value < 1e12 ? value * 1000 : value;
  } else {
    // Numeric-looking string?
    const trimmed = value.trim();
    if (/^\d+(\.\d+)?$/.test(trimmed)) {
      const n = parseFloat(trimmed);
      if (!Number.isFinite(n) || n <= 0) return "—";
      ms = n < 1e12 ? n * 1000 : n;
    } else {
      const d = new Date(trimmed);
      if (Number.isNaN(d.getTime())) return "—";
      ms = d.getTime();
    }
  }

  if (ms === null || ms <= 0) return "—";
  return new Date(ms).toLocaleString();
}
