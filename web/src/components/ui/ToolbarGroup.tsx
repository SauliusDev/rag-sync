import type { ReactNode } from 'react';

type ToolbarGroupProps = {
  children: ReactNode;
  label?: string;
};

export function ToolbarGroup({ children, label }: ToolbarGroupProps) {
  return (
    <div className="toolbar-group" role={label ? 'group' : undefined} aria-label={label}>
      {children}
    </div>
  );
}
