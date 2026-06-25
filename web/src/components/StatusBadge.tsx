import { useEffect, useState } from 'react';

import { fetchStatus, type QueueStatus } from '../api';

export function StatusBadge() {
  const [status, setStatus] = useState<QueueStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const nextStatus = await fetchStatus();
        if (!cancelled) {
          setStatus(nextStatus);
        }
      } catch {
        if (!cancelled) {
          setStatus({ label: 'Status unavailable', queue: {} });
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

  const metrics = status?.system ? Object.entries(status.system) : [];

  return (
    <div className="status-badges" aria-label="System status">
      <div className="status-badge">
        {status?.queue?.paused ? 'Paused · ' : ''}
        {status?.label ?? 'Loading status'}
      </div>
      {status?.active?.stage?.label ? (
        <div className="status-badge">
          {status.active.stage.label}
          {status.active.file_name ? ` · ${status.active.file_name}` : ''}
        </div>
      ) : null}
      {metrics.map(([key, metric]) => (
        <div className="status-badge" key={key} title={metric.detail || metric.label}>
          {metric.label}
        </div>
      ))}
    </div>
  );
}
