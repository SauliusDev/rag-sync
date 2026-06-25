import { describe, expect, it } from 'vitest';

import {
  bulkEnqueueJobs,
  convertFile,
  enqueueJob,
  fetchFiles,
  fetchJobs,
  fetchProfiles,
  fetchQuerySet,
  fetchSettings,
  fetchStatus,
  killQueue,
  loadJson,
  parseFile,
  pauseQueue,
  saveJson,
  scanProfile,
  resumeQueue,
  uploadFile,
} from './api';

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

describe('fetchSettings', () => {
  it('returns runtime settings from the API response', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          profile_path: '/srv/rag-sync/config/profiles.toml',
          ragflow_base_url: 'http://127.0.0.1:9380',
          protected_datasets: ['quant-books-legacy'],
          dataset_defaults: {
            'quant-books': {
              chunk_method: 'naive',
              parser_config: { chunk_token_num: 1000 },
            },
          },
          profiles: [],
        }),
        { status: 200 },
      );

    try {
      await expect(fetchSettings()).resolves.toMatchObject({
        profile_path: '/srv/rag-sync/config/profiles.toml',
        protected_datasets: ['quant-books-legacy'],
        dataset_defaults: {
          'quant-books': {
            chunk_method: 'naive',
          },
        },
      });
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

  it('fetches queue status', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ label: '1 active · 2 queued', queue: {} }), {
        status: 200,
      });

    try {
      await expect(fetchStatus()).resolves.toMatchObject({
        label: '1 active · 2 queued',
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('posts queue pause and resume requests', async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return new Response(JSON.stringify({ paused: true }), { status: 200 });
    };

    try {
      await pauseQueue();
      await resumeQueue();
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(calls).toEqual([
      ['/api/queue/pause', { method: 'POST' }],
      ['/api/queue/resume', { method: 'POST' }],
    ]);
  });

  it('posts queue kill requests', async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return new Response(
        JSON.stringify({ paused: true, canceled_running_job: true, terminated_processes: 1 }),
        { status: 200 },
      );
    };

    try {
      await expect(killQueue()).resolves.toEqual({
        paused: true,
        canceled_running_job: true,
        terminated_processes: 1,
      });
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(calls).toEqual([['/api/queue/kill', { method: 'POST' }]]);
  });

  it('fetches jobs', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ jobs: [{ id: 9, status: 'running', progress: 0.35 }] }), {
        status: 200,
      });

    try {
      await expect(fetchJobs()).resolves.toEqual([{ id: 9, status: 'running', progress: 0.35 }]);
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

  it('posts file conversion requests with the selected parser', async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return new Response(JSON.stringify({ output_path: '/tmp/output.md' }), { status: 200 });
    };

    try {
      await expect(convertFile(7, 'marker')).resolves.toEqual({ output_path: '/tmp/output.md' });
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(calls).toEqual([
      [
        '/api/files/7/convert',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ parser: 'marker' }),
        },
      ],
    ]);
  });

  it('posts upload and parse requests for a file', async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    };

    try {
      await uploadFile(7);
      await parseFile(7);
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(calls).toEqual([
      ['/api/files/7/upload', { method: 'POST' }],
      ['/api/files/7/parse', { method: 'POST' }],
    ]);
  });

  it('posts queue job requests', async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return new Response(JSON.stringify({ job_id: 9 }), { status: 200 });
    };

    try {
      await expect(
        enqueueJob({ kind: 'sync_file', source_file_id: 7, profile_name: 'quant-books' }),
      ).resolves.toEqual({ job_id: 9 });
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(calls).toEqual([
      [
        '/api/jobs',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: 'sync_file',
            source_file_id: 7,
            profile_name: 'quant-books',
          }),
        },
      ],
    ]);
  });

  it('posts bulk queue job requests', async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return new Response(JSON.stringify({ count: 2, job_ids: [9, 10] }), { status: 200 });
    };

    try {
      await expect(
        bulkEnqueueJobs({
          kind: 'sync_file',
          source_file_ids: [7, 8],
        }),
      ).resolves.toEqual({ count: 2, job_ids: [9, 10] });
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(calls).toEqual([
      [
        '/api/jobs/bulk',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: 'sync_file',
            source_file_ids: [7, 8],
          }),
        },
      ],
    ]);
  });
});

describe('local storage helpers', () => {
  it('loads fallback when storage is empty', () => {
    const store = new Map<string, string>();
    Object.defineProperty(globalThis, 'localStorage', {
      value: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => store.set(key, value),
      },
      configurable: true,
    });
    expect(loadJson('missing', { value: 1 })).toEqual({ value: 1 });
  });

  it('saves and loads json values', () => {
    const store = new Map<string, string>();
    Object.defineProperty(globalThis, 'localStorage', {
      value: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => store.set(key, value),
      },
      configurable: true,
    });
    saveJson('filters', { query: 'matrix' });
    expect(loadJson('filters', { query: '' })).toEqual({ query: 'matrix' });
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
