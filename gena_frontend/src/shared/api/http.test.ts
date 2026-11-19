import { describe, expect, it } from 'vitest';
import { parseErrorBody } from './http';

describe('parseErrorBody', () => {
  it('returns HTTP status for empty body', () => {
    expect(parseErrorBody(404, '')).toBe('HTTP 404');
  });

  it('extracts FastAPI detail string', () => {
    expect(parseErrorBody(400, '{"detail":"Bad credentials"}')).toBe('Bad credentials');
  });

  it('extracts chunker error field', () => {
    expect(parseErrorBody(500, '{"error":"chunk failed"}')).toBe('chunk failed');
  });

  it('formats validation error arrays', () => {
    const body = JSON.stringify({
      detail: [{ msg: 'field required', loc: ['body', 'name'] }],
    });
    expect(parseErrorBody(422, body)).toBe('field required');
  });

  it('falls back to trimmed plain text', () => {
    expect(parseErrorBody(502, 'upstream unavailable')).toBe('upstream unavailable');
  });
});
