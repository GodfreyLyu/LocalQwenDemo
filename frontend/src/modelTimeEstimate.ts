export const BASE_ESTIMATE_MS = 200_000;
export const MAX_ESTIMATE_MS = 300_000;

export function estimateModelTimeMs(
  sourceLength: number,
  sourceMaxChars: number,
) {
  const safeLength = Number.isFinite(sourceLength)
    ? Math.max(0, sourceLength)
    : 0;
  const safeMaximum =
    Number.isFinite(sourceMaxChars) && sourceMaxChars > 0 ? sourceMaxChars : 0;
  const ratio = safeMaximum
    ? Math.min(Math.max(safeLength / safeMaximum, 0), 1)
    : 0;
  return Math.round(
    BASE_ESTIMATE_MS + ratio * (MAX_ESTIMATE_MS - BASE_ESTIMATE_MS),
  );
}

export function formatEstimatedModelTime(estimateMs: number) {
  const halfMinutes = Math.round(estimateMs / 30_000);
  return `${halfMinutes / 2} minutes`;
}
