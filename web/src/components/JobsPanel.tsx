import { useEffect, useMemo, useState } from 'react';

import {
  fetchJobs,
  fetchStatus,
  killQueue,
  pauseQueue,
  resumeQueue,
  type JobRecord,
  type QueueStatus,
} from '../api';

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

function stageTone(job: JobRecord) {
  if (job.status === 'failed') return 'failed';
  if (job.status === 'completed') return 'completed';
  if (job.status === 'canceled') return 'canceled';
  if (job.status === 'running') return 'running';
  return 'queued';
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

export function JobsSummaryStrip({
  summary,
  status,
}: {
  summary: { queued: number; running: number; failed: number; completed: number };
  status: QueueStatus | null;
}) {
  const etaSummary = queueEtaSummary(status);

  return (
    <div className="jobs-summary">
      <div className="jobs-summary-card">
        <strong>{summary.running}</strong>
        <span>Active</span>
      </div>
      <div className="jobs-summary-card">
        <strong>{summary.queued}</strong>
        <span>Queued</span>
      </div>
      <div className="jobs-summary-card">
        <strong>{summary.failed}</strong>
        <span>Failed</span>
      </div>
      <div className="jobs-summary-card">
        <strong>{summary.completed}</strong>
        <span>Completed</span>
      </div>
      <div className="jobs-summary-card jobs-summary-wide">
        <strong>{etaSummary.remaining}</strong>
        <span>Queue ETA</span>
      </div>
      <div className="jobs-summary-card jobs-summary-wide">
        <strong>{etaSummary.finishAt}</strong>
        <span>Estimated finish</span>
      </div>
      <div className="jobs-summary-card jobs-summary-wide">
        <strong>{etaSummary.throughput}</strong>
        <span>Recent throughput</span>
      </div>
    </div>
  );
}

export function JobTableRow({ job }: { job: JobRecord }) {
  return (
    <tr>
      <td>
        <div className="queue-cell">
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
          <div className="mini-progress">
            <span style={{ width: `${progressPercent(job)}%` }} />
          </div>
        </div>
      </td>
      <td>{job.profile_name || '-'}</td>
      <td>
        <div className="job-time">
          {timingLines(job).map((line) => (
            <span
              key={line}
              className={
                line === confidenceLabel(job.confidence) && (job.status === 'running' || job.status === 'queued')
                  ? 'job-confidence'
                  : undefined
              }
            >
              {line}
            </span>
          ))}
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
      canceled: jobs.filter((job) => job.status === 'canceled').length,
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

  if (loading) {
    return <p className="muted">Loading jobs.</p>;
  }

  return (
    <div className="jobs-view">
      <JobsSummaryStrip summary={summary} status={status} />
      <div className="jobs-toolbar">
        <label className="search-field jobs-search">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search jobs"
          />
        </label>
        <div className="jobs-filters">
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
          <button
            className={visibility.running ? 'action-button primary' : 'action-button'}
            type="button"
            onClick={() => toggleStatus('running')}
          >
            Active
          </button>
          <button
            className={visibility.queued ? 'action-button primary' : 'action-button'}
            type="button"
            onClick={() => toggleStatus('queued')}
          >
            Queued
          </button>
          <button
            className={visibility.failed ? 'action-button primary' : 'action-button'}
            type="button"
            onClick={() => toggleStatus('failed')}
          >
            Failed
          </button>
          <button
            className={visibility.completed ? 'action-button primary' : 'action-button'}
            type="button"
            onClick={() => toggleStatus('completed')}
          >
            Completed
          </button>
          <button
            className={visibility.canceled ? 'action-button primary' : 'action-button'}
            type="button"
            onClick={() => toggleStatus('canceled')}
          >
            Canceled
          </button>
        </div>
      </div>
      {error ? <p className="inline-error">{error}</p> : null}
      <div className="jobs-table-wrap">
        <table className="jobs-table jobs-table-compact">
          <thead>
            <tr>
              <th>Queue</th>
              <th>File</th>
              <th>Stage</th>
              <th>Profile</th>
              <th>Timing</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {visibleJobs.length === 0 ? (
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
    </div>
  );
}
