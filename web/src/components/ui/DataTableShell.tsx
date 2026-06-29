import type { ReactNode } from 'react';

type DataTableShellProps = {
  children: ReactNode;
  toolbar?: ReactNode;
  footer?: ReactNode;
  label?: string;
};

export function DataTableShell({ children, toolbar, footer, label }: DataTableShellProps) {
  return (
    <section className="data-table-shell" aria-label={label}>
      {toolbar ? <div className="data-table-shell-toolbar">{toolbar}</div> : null}
      <div className="data-table-shell-body">{children}</div>
      {footer ? <div className="data-table-shell-footer">{footer}</div> : null}
    </section>
  );
}
