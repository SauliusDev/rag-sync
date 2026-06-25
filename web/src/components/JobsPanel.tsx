import { useEffect, useMemo, useState } from 'react';

import { fetchJobs, fetchStatus, killQueue, pauseQueue, resumeQueue, type JobRecord } from '../api';

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

function progressPercent(job: JobRecord) {
  const stageProgress = job.stage?.progress ?? job.progress ?? 0;
  return Math.max(0, Math.min(100, Math.round(stageProgress * 100)));
}

function stageTone(job: JobRecord) {
  if (job.status === 'failed') return 'failed';
  if (job.status === 'completed') return 'completed';
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
  if (job.queue_position) return `#${job.queue_position}`;
  return '-';
}

export function JobsPanel() {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [visibility, setVisibility] = useState<JobVisibility>(defaultVisibility);
  const [query, setQuery] = useState('');
  const [paused, setPaused] = useState(false);
  const [queueAction, setQueueAction] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [nextJobs, status] = await Promise.all([fetchJobs(), fetchStatus()]);
        if (!cancelled) {
          setJobs(nextJobs);
          setPaused(Boolean(status.queue.paused));
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
      }
    }

    void load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const summary = useMemo(() => {
    const counts = { queued: 0, running: 0, failed: 0, completed: 0, canceled: 0 };
    for (const job of jobs) {
      if (job.status in counts) {
        counts[job.status as keyof typeof counts] += 1;
      }
    }
    return counts;
  }, [jobs]);

  const visibleJobs = useMemo(() => filterJobs(jobs, visibility, query), [jobs, query, visibility]);

  function toggleStatus(status: keyof JobVisibility) {
    setVisibility((current) => ({ ...current, [status]: !current[status] }));
  }

  async function togglePause() {
    setQueueAction(paused ? 'resume' : 'pause');
    try {
      const result = paused ? await resumeQueue() : await pauseQueue();
      setPaused(result.paused);
      const nextJobs = await fetchJobs();
      setJobs(nextJobs);
      setError('');
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
      setPaused(Boolean(status.queue.paused));
      setError('');
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
      </div>
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
              visibleJobs.map((job) => (
                <tr key={job.id}>
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
                        <span className={`state-badge state-${stageTone(job)}`}>
                          {job.stage?.label ?? job.kind}
                        </span>
                        <span className="stage-percent">{progressPercent(job)}%</span>
                      </div>
                      <div className="mini-progress">
                        <span style={{ width: `${progressPercent(job)}%` }} />
                      </div>
                    </div>
                  </td>
                  <td>{job.profile_name || '-'}</td>
                  <td>
                    <div className="job-time">
                      <span>Start {formatTimestamp(job.started_at)}</span>
                      <span>End {formatTimestamp(job.finished_at)}</span>
                    </div>
                  </td>
                  <td className="job-error">{job.error_summary || '-'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
