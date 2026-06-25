import { describe, expect, it } from 'vitest';

import { fetchProfiles } from './api';

describe('fetchProfiles', () => {
  it('returns profile data from the API response', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          profiles: [
            {
              name: 'quant-articles',
              parser_mode: 'passthrough',
              target_dataset: 'quant-articles',
              source_paths: ['/atlas/articles'],
            },
          ],
        }),
        { status: 200 },
      );

    try {
      await expect(fetchProfiles()).resolves.toEqual([
        {
          name: 'quant-articles',
          parser_mode: 'passthrough',
          target_dataset: 'quant-articles',
          source_paths: ['/atlas/articles'],
        },
      ]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
