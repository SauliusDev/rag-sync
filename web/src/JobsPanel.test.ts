import { describe, expect, it } from 'vitest';

import { filterJobs, queueBadge } from './components/JobsPanel';

describe('jobs panel helpers', () => {
  it('filters canceled and completed jobs out of the default live queue view', () => {
    const jobs = [
      { id: 1, kind: 'sync_file', status: 'running', file_name: 'A.pdf', progress: 0, error_summary: '' },
      { id: 2, kind: 'sync_file', status: 'queued', file_name: 'B.pdf', progress: 0, error_summary: '' },
      { id: 3, kind: 'sync_file', status: 'canceled', file_name: 'C.pdf', progress: 0, error_summary: '' },
      { id: 4, kind: 'sync_file', status: 'completed', file_name: 'D.pdf', progress: 1, error_summary: '' },
    ];

    const visible = filterJobs(
      jobs,
      { running: true, queued: true, failed: true, completed: false, canceled: false },
      '',
    );

    expect(visible.map((job) => job.id)).toEqual([1, 2]);
  });

  it('matches jobs by search query', () => {
    const jobs = [
      {
        id: 1,
        kind: 'sync_file',
        status: 'failed',
        file_name: 'Advanced Portfolio Management.pdf',
        source_path: '/books/advanced.pdf',
        profile_name: 'quant-books',
        error_summary: 'marker produced no markdown',
        progress: 0,
      },
    ];

    const visible = filterJobs(
      jobs,
      { running: true, queued: true, failed: true, completed: true, canceled: true },
      'no markdown',
    );

    expect(visible.map((job) => job.id)).toEqual([1]);
  });

  it('formats queue badges for running and queued jobs', () => {
    expect(
      queueBadge({ id: 1, kind: 'sync_file', status: 'running', progress: 0, error_summary: '' }),
    ).toBe('Now');
    expect(
      queueBadge({
        id: 2,
        kind: 'sync_file',
        status: 'queued',
        queue_position: 7,
        progress: 0,
        error_summary: '',
      }),
    ).toBe('#7');
  });
});
