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

  return <div className="status-badge">{status?.label ?? 'Loading status'}</div>;
}
