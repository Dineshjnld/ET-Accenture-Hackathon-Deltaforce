import { describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './client';

describe('api client', () => {
  it('returns parsed JSON on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ a: 1 }) }) as unknown as Response));
    expect(await api('/api/v1/health')).toEqual({ a: 1 });
  });

  it('raises ApiError with code and message from the error envelope', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 404,
      json: async () => ({ error: { code: 'not_found', message: 'gone', details: {} } }),
    }) as unknown as Response));
    try {
      await api('/api/v1/documents/nope');
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).code).toBe('not_found');
      expect((e as ApiError).status).toBe(404);
    }
  });

  it('handles non-JSON error bodies', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => { throw new Error('no json'); } }) as unknown as Response));
    try {
      await api('/api/v1/x');
      expect.unreachable();
    } catch (e) {
      expect((e as ApiError).message).toContain('500');
    }
  });
});

