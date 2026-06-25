import { describe, expect, it } from 'vitest';

import { fetchFiles, fetchProfiles, fetchQuerySet, scanProfile } from './api';

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

describe('files API', () => {
  it('returns source files from the API response', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          files: [
            {
              id: 7,
              profile_name: 'quant-articles',
              source_path: '/atlas/articles/example.md',
              source_type: 'article',
              extension: 'md',
              state: 'converted',
              included: 1,
              tags: 'review',
              note: 'clean',
              updated_at: '2026-06-25 10:00:00',
            },
          ],
        }),
        { status: 200 },
      );

    try {
      await expect(fetchFiles()).resolves.toEqual([
        {
          id: 7,
          profile_name: 'quant-articles',
          source_path: '/atlas/articles/example.md',
          source_type: 'article',
          extension: 'md',
          state: 'converted',
          included: 1,
          tags: 'review',
          note: 'clean',
          updated_at: '2026-06-25 10:00:00',
        },
      ]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('posts scan requests for a profile', async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return new Response(JSON.stringify({ count: 1 }), { status: 200 });
    };

    try {
      await scanProfile('quant articles');
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(calls).toEqual([['/api/scan/quant%20articles', { method: 'POST' }]]);
  });
});

describe('retrieval API', () => {
  it('returns retrieval query sets from the API response', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          queries: [{ id: 'Q1', question: 'What is d1?' }],
        }),
        { status: 200 },
      );

    try {
      await expect(fetchQuerySet('formula-benchmark')).resolves.toEqual([
        { id: 'Q1', question: 'What is d1?' },
      ]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
