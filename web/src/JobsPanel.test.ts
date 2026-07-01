import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import {
  buildTimingChips,
  buildQueueOverviewValue,
  formatCostUsd,
  formatEtaTimestamp,
  JobTableRow,
  JobsPanel,
  filterJobs,
  progressPercent,
  queueBadge,
  queueEtaSummary,
  resolvedPausedState,
  stageDetail,
} from './components/JobsPanel';
import { App } from './App';
import { StatusBadgeContent } from './components/StatusBadge';

function installLocalStorage(initialValues: Record<string, string> = {}) {
  const store = new Map<string, string>(Object.entries(initialValues));
  const localStorageMock = {
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
    removeItem(key: string) {
      store.delete(key);
    },
    clear() {
      store.clear();
    },
  };

  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: localStorageMock,
  });
}

describe('jobs panel helpers', () => {
  it('renders the jobs layout with metrics strip, control band, and table shell', () => {
    const markup = renderToStaticMarkup(createElement(JobsPanel));

    expect(markup).toContain('class="jobs-metric-strip"');
    expect(markup).toContain('class="metric-strip"');
    expect(markup).toContain('class="jobs-control-band"');
    expect(markup).toContain('aria-label="Queue controls"');
    expect(markup).toContain('aria-label="Visibility filters"');
    expect(markup).toContain('aria-label="Search jobs"');
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain('aria-pressed="false"');
    expect(markup).toContain('class="data-table-shell"');
    expect(markup).toContain('class="jobs-table-shell"');
    expect(markup).toContain('Total API spend');
    expect(markup).toContain('Queue overview');
    expect(markup).toContain('Search all queue activity');
  });

  it('collapses queue counts into one compact overview value', () => {
    expect(
      buildQueueOverviewValue({
        running: 1,
        queued: 58,
        failed: 2,
        completed: 79,
      }),
    ).toBe('1 active · 58 queued · 2 failed · 79 completed');
  });

  it('renders the app jobs path with one jobs header and the current jobs shell', () => {
    installLocalStorage({ 'rag-sync.active-tab': JSON.stringify('Jobs') });

    const markup = renderToStaticMarkup(createElement(App));

    expect(markup.match(/<h1/g)?.length).toBe(5);
    expect(markup).toContain('<section class="screen-panel" hidden="" aria-hidden="true"><div class="files-screen">');
    expect(markup).toContain('<section class="screen-panel" aria-hidden="false"><div class="jobs-screen">');
    expect(markup).toContain('id="jobs-screen-title"');
    expect(markup).toContain('Monitor queue activity, control workers, and inspect timing without losing the job list.');
    expect(markup).toContain('class="jobs-metric-strip"');
    expect(markup).toContain('class="jobs-control-band"');
    expect(markup).toContain('class="data-table-shell"');
  });

  it('renders the app files path with one files header', () => {
    installLocalStorage({ 'rag-sync.active-tab': JSON.stringify('Files') });

    const markup = renderToStaticMarkup(createElement(App));

    expect(markup.match(/<h1/g)?.length).toBe(5);
    expect(markup).toContain('<section class="screen-panel" aria-hidden="false"><div class="files-screen">');
    expect(markup).toContain('Files</h1>');
  });

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
    expect(
      queueBadge({
        id: 3,
        kind: 'sync_file',
        status: 'queued',
        queue_position: 0,
        progress: 0,
        error_summary: '',
      }),
    ).toBe('#0');
    expect(
      queueBadge({
        id: 4,
        kind: 'sync_file',
        status: 'completed',
        queue_position: 2,
        progress: 1,
        error_summary: '',
      }),
    ).toBe('-');
  });

  it('prefers backend progress percent and eta detail when present', () => {
    const job = {
      id: 9,
      kind: 'sync_file',
      status: 'running',
      progress: 0.1,
      progress_percent: 42,
      eta_label: '8m remaining',
      error_summary: '',
    };

    expect(progressPercent(job)).toBe(42);
    expect(stageDetail(job)).toBe('42%');
  });

  it('uses eta text instead of fake percentages when no backend progress exists', () => {
    const queued = {
      id: 10,
      kind: 'sync_file',
      status: 'queued',
      progress: 0,
      eta_label: '15m remaining',
      error_summary: '',
    };

    expect(progressPercent(queued)).toBe(0);
    expect(stageDetail(queued)).toBe('15m remaining');
  });

  it('renders running jobs with unknown backend progress as indeterminate', () => {
    const markup = renderToStaticMarkup(
      createElement(
        'table',
        null,
        createElement(
          'tbody',
          null,
          createElement(JobTableRow, {
            job: {
              id: 11,
              kind: 'sync_file',
              status: 'running',
              progress: 0,
              error_summary: '',
              file_name: 'unknown-progress.pdf',
              source_path: '/tmp/unknown-progress.pdf',
              profile_name: 'quant-books',
            },
          }),
        ),
      ),
    );

    expect(markup).toContain('mini-progress is-indeterminate');
    expect(markup).toContain('role="progressbar"');
    expect(markup).toContain('aria-valuetext="estimating"');
    expect(markup).not.toContain('style="width:0%"');
    expect(markup).not.toContain('stage-percent">0%');
  });

  it('builds queue eta summary values from queue status', () => {
    expect(
      queueEtaSummary({
        label: '1 active · 10 queued',
        queue: { running: 1, queued: 10 },
        eta: {
          label: '2h remaining',
          throughput_label: 'recent median 8m/file',
          estimated_finish_at: '2026-06-26T12:00:00',
          confidence: 'medium',
        },
      }),
    ).toEqual({
      remaining: '2h remaining',
      finishAt: '2026-06-26 12:00',
      throughput: 'recent median 8m/file',
      apiCost: undefined,
    });
  });

  it('prefers the primary queue_eta payload when present', () => {
    expect(
      queueEtaSummary({
        label: '1 active · 10 queued',
        queue: { running: 1, queued: 10 },
        queue_eta: {
          label: '90m remaining',
          throughput_label: 'recent median 6m/file',
          estimated_finish_at: '2026-06-26T11:30:00',
          confidence: 'high',
        },
      }),
    ).toEqual({
      remaining: '90m remaining',
      finishAt: '2026-06-26 11:30',
      throughput: 'recent median 6m/file',
      apiCost: undefined,
    });
  });

  it('includes estimated api cost in the queue eta summary when present', () => {
    expect(
      queueEtaSummary({
        label: '1 active · 10 queued',
        queue: { running: 1, queued: 10 },
        queue_eta: {
          label: '90m remaining',
          throughput_label: 'recent median 6m/file',
          estimated_finish_at: '2026-06-26T11:30:00',
          confidence: 'high',
          estimated_api_cost_label: '$0.003000 estimated GLM OCR',
        },
      }),
    ).toEqual({
      remaining: '90m remaining',
      finishAt: '2026-06-26 11:30',
      throughput: 'recent median 6m/file',
      apiCost: '$0.003000 estimated GLM OCR',
    });
  });

  it('formats estimated finish timestamps for the summary strip', () => {
    expect(
      queueEtaSummary({
        label: '1 active · 10 queued',
        queue: { running: 1, queued: 10 },
        queue_eta: {
          label: '90m remaining',
          throughput_label: 'recent median 6m/file',
          estimated_finish_at: '2026-06-26T11:30:00',
          confidence: 'high',
        },
      }),
    ).toEqual({
      remaining: '90m remaining',
      finishAt: '2026-06-26 11:30',
      throughput: 'recent median 6m/file',
      apiCost: undefined,
    });
  });

  it('formats iso eta timestamps and falls back cleanly', () => {
    expect(formatEtaTimestamp('2026-06-26T11:30:00')).toBe('2026-06-26 11:30');
    expect(formatEtaTimestamp('unknown')).toBe('unknown');
    expect(formatEtaTimestamp(null)).toBe('unknown');
  });

  it('reconciles paused state from the refreshed status snapshot', () => {
    expect(
      resolvedPausedState(false, {
        label: 'Paused',
        queue: { paused: true },
      }),
    ).toBe(true);
    expect(
      resolvedPausedState(true, {
        label: 'Running',
        queue: { paused: false },
      }),
    ).toBe(false);
    expect(
      resolvedPausedState(true, {
        label: 'Running',
        queue: {},
      }),
    ).toBe(true);
    expect(resolvedPausedState(true, null)).toBe(true);
  });

  it('does not render the active file badge in the status header', () => {
    const markup = renderToStaticMarkup(
      createElement(StatusBadgeContent, {
        status: {
          label: '1 active · 10 queued',
          queue: { running: 1, queued: 10 },
          active: {
            id: 1,
            kind: 'sync_file',
            status: 'running',
            progress: 0.2,
            error_summary: '',
            file_name: 'Very Long File Name.pdf',
            stage: { key: 'convert', label: 'Marker conversion', status: 'running', progress: 0.2 },
          },
          system: { cpu: { label: 'CPU 18%' } },
        },
      }),
    );

    expect(markup).toContain('1 active · 10 queued');
    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).not.toContain('Very Long File Name.pdf');
    expect(markup).not.toContain('Marker conversion');
  });

  it('formats api spend amounts for jobs metrics', () => {
    expect(formatCostUsd(0)).toBe('$0.00');
    expect(formatCostUsd(0.00003)).toBe('$0.000030');
  });

  it('renders eta, wait, and confidence in a job row without fake percent text', () => {
    const markup = renderToStaticMarkup(
      createElement(
        'table',
        null,
        createElement(
          'tbody',
          null,
          createElement(JobTableRow, {
            job: {
              id: 1,
              kind: 'sync_file',
              status: 'queued',
              progress: 0,
              error_summary: '',
              file_name: 'book.pdf',
              source_path: '/tmp/book.pdf',
              profile_name: 'quant-books',
              wait_label: '12m',
              eta_label: '45m remaining',
              confidence: 'low',
              stage: { key: 'queued', label: 'Queued', status: 'queued', progress: 0 },
            },
          }),
        ),
      ),
    );

    expect(markup).toContain('class="queue-cell queue-cell-inline"');
    expect(markup).toContain('class="job-time-grid"');
    expect(markup).toContain('Wait 12m');
    expect(markup).toContain('ETA 45m remaining');
    expect(markup).toContain('Confidence low');
    expect(markup).toContain('45m remaining');
    expect(markup).not.toContain('stage-percent">0%');
  });

  it('builds compact timing chips for live jobs', () => {
    expect(
      buildTimingChips({
        id: 1,
        kind: 'sync_file',
        status: 'queued',
        progress: 0,
        error_summary: '',
        wait_label: '12m',
        eta_label: '45m remaining',
        confidence: 'low',
        started_at: '2026-06-28 12:00:00',
        finished_at: null,
      }),
    ).toEqual([
      'Start 2026-06-28 12:00:00',
      'Wait 12m',
      'ETA 45m remaining',
      'Confidence low',
    ]);
  });

  it('renders terminal rows without queue timing placeholders', () => {
    const markup = renderToStaticMarkup(
      createElement(
        'table',
        null,
        createElement(
          'tbody',
          null,
          createElement(JobTableRow, {
            job: {
              id: 2,
              kind: 'sync_file',
              status: 'completed',
              progress: 1,
              error_summary: '',
              file_name: 'done.pdf',
              source_path: '/tmp/done.pdf',
              profile_name: 'quant-books',
              eta_label: 'unknown',
              wait_label: 'unknown',
              confidence: 'estimating',
              stage: { key: 'done', label: 'Done', status: 'completed', progress: 1 },
            },
          }),
        ),
      ),
    );

    expect(markup).toContain('Completed');
    expect(markup).toContain('100%');
    expect(markup).toContain('aria-valuenow="100"');
    expect(markup).not.toContain('ETA unknown');
    expect(markup).not.toContain('Wait unknown');
  });

  it('renders canceled rows with canceled styling tone', () => {
    const markup = renderToStaticMarkup(
      createElement(
        'table',
        null,
        createElement(
          'tbody',
          null,
          createElement(JobTableRow, {
            job: {
              id: 3,
              kind: 'sync_file',
              status: 'canceled',
              progress: 0,
              error_summary: '',
              file_name: 'canceled.pdf',
              source_path: '/tmp/canceled.pdf',
              profile_name: 'quant-books',
              stage: { key: 'done', label: 'Done', status: 'canceled', progress: 0 },
            },
          }),
        ),
      ),
    );

    expect(markup).toContain('state-canceled');
    expect(markup).toContain('Canceled');
  });
});
