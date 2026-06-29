import { useEffect, useMemo, useState } from 'react';

import {
  fetchJobs,
  fetchStatus,
  killQueue,
  pauseQueue,
  resumeQueue,
  type JobRecord,
  type QueueStatus,
  type UsageProviderSummary,
} from '../api';
import { DataTableShell } from './ui/DataTableShell';
import { MetricStrip } from './ui/MetricStrip';
import { ToolbarGroup } from './ui/ToolbarGroup';

const QUEUE_REFRESH_EVENT = 'rag-sync:queue-refresh';
const POLL_INTERVAL_MS = 2000;

type JobVisibility = {
  queued: boolean;
  running: boolean;
  failed: boolean;
  completed: boolean;
  canceled: boolean;
};

const defaultVisibility: JobVisibility = {
  queued: true,
  running: true,
  failed: true,
  completed: false,
  canceled: false,
};

function formatTimestamp(value?: string | null) {
  return value || '-';
}

export function formatCostUsd(value?: number | null) {
  const amount = value ?? 0;
  if (amount === 0) {
    return '$0.00';
  }
  if (Math.abs(amount) < 0.01) {
    return `$${amount.toFixed(6)}`;
  }
  return `$${amount.toFixed(2)}`;
}

function formatTokens(value?: number | null) {
  return `${(value ?? 0).toLocaleString()} tokens`;
}

export function progressPercent(job: JobRecord) {
  if (typeof job.progress_percent === 'number') {
    return Math.max(0, Math.min(100, Math.round(job.progress_percent)));
  }
  if (job.status === 'queued') {
    return 0;
  }
  const stageProgress = job.stage?.progress ?? job.progress ?? 0;
  return Math.max(0, Math.min(100, Math.round(stageProgress * 100)));
}

export function stageDetail(job: JobRecord) {
  if (job.status === 'completed') {
    return '100%';
  }
  if (job.status === 'failed') {
    return 'failed';
  }
  if (job.status === 'canceled') {
    return 'canceled';
  }
  if (typeof job.progress_percent === 'number') {
    return `${progressPercent(job)}%`;
  }
  if (job.eta_label) {
    return job.eta_label;
  }
  if (job.status === 'running' || job.status === 'queued') {
    return 'estimating';
  }
  if (job.status === 'completed') {
    return '100%';
  }
  return '-';
}

export function queueEtaSummary(status: QueueStatus | null) {
  const eta = status?.queue_eta ?? status?.eta;
  return {
    remaining: eta?.label ?? 'estimating',
    finishAt: formatEtaTimestamp(eta?.estimated_finish_at),
    throughput: eta?.throughput_label ?? 'Building timing history',
    apiCost: eta?.estimated_api_cost_label,
  };
}

export function formatEtaTimestamp(value?: string | null) {
  if (!value) {
    return 'unknown';
  }
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(value);
  if (match) {
    return `${match[1]} ${match[2]}`;
  }
  return value;
}

export function resolvedPausedState(commandPaused: boolean, status: QueueStatus | null) {
  if (typeof status?.queue.paused === 'boolean') {
    return status.queue.paused;
  }
  return commandPaused;
}

function confidenceLabel(confidence?: string) {
  return confidence ? confidence.replace('_', ' ') : 'estimating';
}

function timingLines(job: JobRecord) {
  const lines = [
    `Start ${formatTimestamp(job.started_at)}`,
    `End ${formatTimestamp(job.finished_at)}`,
  ];
  if (job.status === 'running' || job.status === 'queued') {
    lines.push(`Wait ${job.wait_label ?? '-'}`);
    lines.push(`ETA ${job.eta_label ?? 'estimating'}`);
    lines.push(confidenceLabel(job.confidence));
    return lines;
  }
  if (job.status === 'completed') {
    lines.push('Completed');
  } else if (job.status === 'failed') {
    lines.push('Failed');
  } else if (job.status === 'canceled') {
    lines.push('Canceled');
  }
  return lines;
}

export function buildTimingChips(job: JobRecord) {
  const chips: string[] = [];

  if (job.started_at) {
    chips.push(`Start ${formatTimestamp(job.started_at)}`);
  }

  if (job.status === 'running' || job.status === 'queued') {
    if (job.wait_label && job.wait_label !== 'unknown') {
      chips.push(`Wait ${job.wait_label}`);
    }
    if (job.eta_label && job.eta_label !== 'unknown') {
      chips.push(`ETA ${job.eta_label}`);
    }
    chips.push(`Confidence ${confidenceLabel(job.confidence)}`);
    return chips;
  }

  if (job.finished_at) {
    chips.push(`End ${formatTimestamp(job.finished_at)}`);
  }

  if (job.status === 'completed') {
    chips.push('Completed');
  } else if (job.status === 'failed') {
    chips.push('Failed');
  } else if (job.status === 'canceled') {
    chips.push('Canceled');
  }

  return chips;
}

function stageTone(job: JobRecord) {
  if (job.status === 'failed') return 'failed';
  if (job.status === 'completed') return 'completed';
  if (job.status === 'canceled') return 'canceled';
  if (job.status === 'running') return 'running';
  return 'queued';
}

function hasMeasuredProgress(job: JobRecord) {
  return typeof job.progress_percent === 'number' || typeof job.stage?.progress === 'number';
}

export function filterJobs(
  jobs: JobRecord[],
  visibility: JobVisibility,
  query: string,
): JobRecord[] {
  const normalized = query.trim().toLowerCase();
  return jobs.filter((job) => {
    if (!visibility[job.status as keyof JobVisibility]) {
      return false;
    }
    if (!normalized) {
      return true;
    }
    const haystack = [
      job.file_name,
      job.source_path,
      job.profile_name,
      job.stage?.label,
      job.error_summary,
      job.status,
    ]
      .join(' ')
      .toLowerCase();
    return haystack.includes(normalized);
  });
}

export function queueBadge(job: JobRecord) {
  if (job.status === 'running') return 'Now';
  if (job.status === 'queued' && typeof job.queue_position === 'number') {
    return `#${job.queue_position}`;
  }
  return '-';
}

function providerMeta(provider: UsageProviderSummary | undefined) {
  if (!provider) {
    return {
      label: 'Provider',
      value: '$0.00',
      detail: 'not tracked',
      tracked: false,
    };
  }
  const hasCredits = typeof provider.total_credits === 'number';
  return {
    label: provider.label,
    value: provider.tracked ? formatCostUsd(provider.cost_usd) : 'not tracked',
    detail:
      provider.tracked && hasCredits
        ? `${formatCostUsd(provider.remaining_credits)} left of ${formatCostUsd(provider.total_credits)} credits`
        : provider.tracked
          ? `${formatTokens(provider.tokens)} · ${provider.calls.toLocaleString()} calls`
          : (provider.note ?? 'usage unavailable'),
    tracked: provider.tracked,
  };
}

function JobsMetricCard({
  label,
  value,
  detail,
  wide = false,
}: {
  label: string;
  value: string | number;
  detail?: string;
  wide?: boolean;
}) {
  return (
    <div className={wide ? 'jobs-metric-card jobs-metric-card-wide' : 'jobs-metric-card'}>
      <strong>{value}</strong>
      <span>{label}</span>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

function QueueMetricsStrip({
  summary,
  status,
}: {
  summary: { queued: number; running: number; failed: number; completed: number };
  status: QueueStatus | null;
}) {
  const etaSummary = queueEtaSummary(status);
  const zApi = providerMeta(status?.usage?.providers?.['z-ai']);
  const openrouter = providerMeta(status?.usage?.providers?.openrouter);

  return (
    <div className="jobs-metric-strip">
      <MetricStrip label="Queue and usage metrics">
        <JobsMetricCard label="Queue overview" value={buildQueueOverviewValue(summary)} wide />
        <JobsMetricCard label="Queue ETA" value={etaSummary.remaining} detail={etaSummary.apiCost} wide />
        <JobsMetricCard label="Estimated finish" value={etaSummary.finishAt} wide />
        <JobsMetricCard label="Recent throughput" value={etaSummary.throughput} wide />
        <JobsMetricCard
          label="Total API spend"
          value={formatCostUsd(status?.usage?.total_cost_usd)}
          detail={formatTokens(status?.usage?.total_tokens)}
        />
        <JobsMetricCard label={zApi.label} value={zApi.value} detail={zApi.detail} wide />
        <JobsMetricCard label={openrouter.label} value={openrouter.value} detail={openrouter.detail} wide />
      </MetricStrip>
    </div>
  );
}

export function buildQueueOverviewValue(summary: {
  queued: number;
  running: number;
  failed: number;
  completed: number;
}) {
  return `${summary.running} active · ${summary.queued} queued · ${summary.failed} failed · ${summary.completed} completed`;
}

export function JobTableRow({ job }: { job: JobRecord }) {
  const showMeasuredProgress = hasMeasuredProgress(job);
  const timingChips = buildTimingChips(job);

  return (
    <tr>
      <td>
        <div className="queue-cell queue-cell-inline">
          <span className={`state-badge state-${job.status}`}>{queueBadge(job)}</span>
          <span className="queue-meta">#{job.id}</span>
        </div>
      </td>
      <td>
        <div className="job-file">
          <strong>{job.file_name || '-'}</strong>
          <span>{job.source_path || '-'}</span>
        </div>
      </td>
      <td>
        <div className="stage-cell">
          <div className="stage-cell-top">
            <span className={`state-badge state-${stageTone(job)}`}>{job.stage?.label ?? job.kind}</span>
            <span className="stage-percent">{stageDetail(job)}</span>
          </div>
          <div
            className={showMeasuredProgress ? 'mini-progress' : 'mini-progress is-indeterminate'}
            role="progressbar"
            aria-label={`${job.stage?.label ?? job.kind} progress`}
            aria-valuemin={showMeasuredProgress ? 0 : undefined}
            aria-valuemax={showMeasuredProgress ? 100 : undefined}
            aria-valuenow={showMeasuredProgress ? progressPercent(job) : undefined}
            aria-valuetext={showMeasuredProgress ? undefined : stageDetail(job)}
          >
            {showMeasuredProgress ? <span style={{ width: `${progressPercent(job)}%` }} /> : null}
          </div>
        </div>
      </td>
      <td>{job.profile_name || '-'}</td>
      <td>
        <div className="job-time-grid">
          {timingChips.map((line) => (
            <span
              key={line}
              className={
                line.startsWith('Confidence ') && (job.status === 'running' || job.status === 'queued')
                  ? 'job-time-chip job-confidence'
                  : 'job-time-chip'
              }
            >
              {line}
            </span>
          ))}
          {timingChips.length === 0 ? (
            <span className="job-time-chip">No timing data</span>
          ) : null}
        </div>
      </td>
      <td className="job-error">{job.error_summary || '-'}</td>
    </tr>
  );
}

export function JobsPanel() {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [status, setStatus] = useState<QueueStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [visibility, setVisibility] = useState<JobVisibility>(defaultVisibility);
  const [query, setQuery] = useState('');
  const [paused, setPaused] = useState(false);
  const [queueAction, setQueueAction] = useState('');

  useEffect(() => {
    let cancelled = false;
    let loading = false;

    async function load() {
      if (loading) {
        return;
      }
      loading = true;
      try {
        const [nextJobs, status] = await Promise.all([fetchJobs(), fetchStatus()]);
        if (!cancelled) {
          setJobs(nextJobs);
          setStatus(status);
          setPaused((current) => resolvedPausedState(current, status));
          setError('');
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : 'Failed to fetch jobs');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
        loading = false;
      }
    }

    function handleVisibilityRefresh() {
      if (document.visibilityState === 'visible') {
        void load();
      }
    }

    function handleRefreshEvent() {
      void load();
    }

    void load();
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    window.addEventListener('focus', handleRefreshEvent);
    document.addEventListener('visibilitychange', handleVisibilityRefresh);
    window.addEventListener(QUEUE_REFRESH_EVENT, handleRefreshEvent);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener('focus', handleRefreshEvent);
      document.removeEventListener('visibilitychange', handleVisibilityRefresh);
      window.removeEventListener(QUEUE_REFRESH_EVENT, handleRefreshEvent);
    };
  }, []);

  const summary = useMemo(() => {
    return {
      queued: status?.queue.queued ?? jobs.filter((job) => job.status === 'queued').length,
      running: status?.queue.running ?? jobs.filter((job) => job.status === 'running').length,
      failed: status?.queue.failed ?? jobs.filter((job) => job.status === 'failed').length,
      completed:
        status?.queue.completed ?? jobs.filter((job) => job.status === 'completed').length,
    };
  }, [jobs, status]);

  const visibleJobs = useMemo(() => filterJobs(jobs, visibility, query), [jobs, query, visibility]);

  function toggleStatus(status: keyof JobVisibility) {
    setVisibility((current) => ({ ...current, [status]: !current[status] }));
  }

  async function togglePause() {
    setQueueAction(paused ? 'resume' : 'pause');
    try {
      const result = paused ? await resumeQueue() : await pauseQueue();
      setPaused(result.paused);
      const [nextJobs, nextStatus] = await Promise.all([fetchJobs(), fetchStatus()]);
      setJobs(nextJobs);
      setStatus(nextStatus);
      setPaused(resolvedPausedState(result.paused, nextStatus));
      setError('');
      window.dispatchEvent(new Event(QUEUE_REFRESH_EVENT));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to update queue state');
    } finally {
      setQueueAction('');
    }
  }

  async function handleKill() {
    setQueueAction('kill');
    try {
      const result = await killQueue();
      setPaused(result.paused);
      const [nextJobs, status] = await Promise.all([fetchJobs(), fetchStatus()]);
      setJobs(nextJobs);
      setStatus(status);
      setPaused(resolvedPausedState(result.paused, status));
      setError('');
      window.dispatchEvent(new Event(QUEUE_REFRESH_EVENT));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to stop running job');
    } finally {
      setQueueAction('');
    }
  }

  const activityLabel =
    queueAction === 'kill'
      ? 'Stopping the active queue job'
      : queueAction === 'pause'
        ? 'Pausing queue dispatch'
        : queueAction === 'resume'
          ? 'Resuming queue dispatch'
          : loading
            ? 'Refreshing queue status'
            : status?.label ?? 'Queue status ready';

  return (
    <div className="jobs-view">
      <QueueMetricsStrip summary={summary} status={status} />

      <div className="jobs-control-band">
        <div className="jobs-control-top">
          <label className="search-field jobs-search">
            <span className="sr-only">Search jobs</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search jobs"
              aria-label="Search jobs"
            />
          </label>
          <ToolbarGroup label="Queue controls">
            <button
              className="action-button danger"
              type="button"
              onClick={handleKill}
              disabled={Boolean(queueAction)}
            >
              {queueAction === 'kill' ? 'Stopping now' : 'Kill switch'}
            </button>
            <button
              className={paused ? 'action-button' : 'action-button primary'}
              type="button"
              onClick={togglePause}
              disabled={Boolean(queueAction)}
            >
              {queueAction === 'pause'
                ? 'Pausing'
                : queueAction === 'resume'
                  ? 'Resuming'
                  : paused
                    ? 'Resume queue'
                    : 'Pause queue'}
            </button>
          </ToolbarGroup>
        </div>

        <div className="jobs-control-bottom">
          <p className="jobs-activity-label" aria-live="polite">
            {activityLabel}
          </p>
          <ToolbarGroup label="Visibility filters">
            <button
              className={visibility.running ? 'action-button primary' : 'action-button'}
              type="button"
              aria-pressed={visibility.running}
              onClick={() => toggleStatus('running')}
            >
              Active
            </button>
            <button
              className={visibility.queued ? 'action-button primary' : 'action-button'}
              type="button"
              aria-pressed={visibility.queued}
              onClick={() => toggleStatus('queued')}
            >
              Queued
            </button>
            <button
              className={visibility.failed ? 'action-button primary' : 'action-button'}
              type="button"
              aria-pressed={visibility.failed}
              onClick={() => toggleStatus('failed')}
            >
              Failed
            </button>
            <button
              className={visibility.completed ? 'action-button primary' : 'action-button'}
              type="button"
              aria-pressed={visibility.completed}
              onClick={() => toggleStatus('completed')}
            >
              Completed
            </button>
            <button
              className={visibility.canceled ? 'action-button primary' : 'action-button'}
              type="button"
              aria-pressed={visibility.canceled}
              onClick={() => toggleStatus('canceled')}
            >
              Canceled
            </button>
          </ToolbarGroup>
        </div>
      </div>

      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="jobs-table-shell">
        <DataTableShell
        label="Job queue"
        toolbar={
          <div className="jobs-table-toolbar">
            <div className="table-summary" aria-live="polite">
              {visibleJobs.length} visible jobs · {summary.running} active · {summary.queued} queued
            </div>
            <div className="table-summary">
              {query ? `Search: ${query}` : 'Search all queue activity'}
            </div>
          </div>
        }
      >
        <div className="jobs-table-wrap">
          <table className="jobs-table jobs-table-compact">
            <thead>
              <tr>
                <th scope="col">Queue</th>
                <th scope="col">File</th>
                <th scope="col">Stage</th>
                <th scope="col">Profile</th>
                <th scope="col">Timing</th>
                <th scope="col">Error</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="empty-cell">
                    Loading jobs
                  </td>
                </tr>
              ) : visibleJobs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="empty-cell">
                    No jobs found
                  </td>
                </tr>
              ) : (
                visibleJobs.map((job) => <JobTableRow key={job.id} job={job} />)
              )}
            </tbody>
          </table>
        </div>
        </DataTableShell>
      </div>
    </div>
  );
}
