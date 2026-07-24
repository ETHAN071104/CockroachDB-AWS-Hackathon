import type { PublicId } from './types';

export const PUBLIC_ID_PATTERN = /^[1-9][0-9]*$/;

export function isPublicId(value: string): value is PublicId {
  return PUBLIC_ID_PATTERN.test(value);
}

