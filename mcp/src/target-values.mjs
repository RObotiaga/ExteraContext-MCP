// Unspecified dimensions are constraints the caller did not provide, not mismatches.
export function targetValueRelation(actual, requested) {
  if (requested === undefined || requested === null || requested === '') return 'not-requested';
  if (actual === undefined || actual === null || actual === '') return 'unknown';
  return String(actual).toLowerCase() === String(requested).toLowerCase() ? 'match' : 'mismatch';
}
