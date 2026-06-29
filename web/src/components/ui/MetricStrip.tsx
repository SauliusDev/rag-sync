import type { ReactNode } from 'react';

type MetricStripProps = {
  children: ReactNode;
  label?: string;
};

export function MetricStrip({ children, label }: MetricStripProps) {
  return (
    <div className="metric-strip" aria-label={label}>
      {children}
    </div>
  );
}
