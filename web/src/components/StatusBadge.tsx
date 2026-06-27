import { useEffect, useState } from 'react';

import { fetchStatus, type QueueStatus } from '../api';

const QUEUE_REFRESH_EVENT = 'rag-sync:queue-refresh';
const POLL_INTERVAL_MS = 2000;

export function StatusBadgeContent({ status }: { status: QueueStatus | null }) {
  const metrics = status?.system ? Object.entries(status.system) : [];

  return (
    <div className="status-badges" aria-label="System status">
      <div className="status-badge">
        {status?.queue?.paused ? 'Paused · ' : ''}
        {status?.label ?? 'Loading status'}
      </div>
      {metrics.map(([key, metric]) => (
        <div className="status-badge" key={key} title={metric.detail || metric.label}>
          {metric.label}
        </div>
      ))}
    </div>
  );
}

export function StatusBadge() {
  const [status, setStatus] = useState<QueueStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    let loading = false;

    async function load() {
      if (loading) {
        return;
      }
      loading = true;
      try {
        const nextStatus = await fetchStatus();
        if (!cancelled) {
          setStatus(nextStatus);
        }
      } catch {
        if (!cancelled) {
          setStatus({ label: 'Status unavailable', queue: {} });
        }
      } finally {
        loading = false;
      }
    }

    function handleRefreshEvent() {
      void load();
    }

    function handleVisibilityRefresh() {
      if (document.visibilityState === 'visible') {
        void load();
      }
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

  return <StatusBadgeContent status={status} />;
}
