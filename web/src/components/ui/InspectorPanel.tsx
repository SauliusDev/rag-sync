import type { ReactNode } from 'react';

type InspectorPanelProps = {
  children: ReactNode;
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  id?: string;
};

export function InspectorPanel({
  children,
  title,
  subtitle,
  actions,
  footer,
  id,
}: InspectorPanelProps) {
  return (
    <aside className="inspector-panel">
      {title || subtitle || actions ? (
        <div className="inspector-panel-header">
          <div className="inspector-panel-copy">
            {title ? <h2 id={id}>{title}</h2> : null}
            {subtitle ? <p className="inspector-panel-subtitle">{subtitle}</p> : null}
          </div>
          {actions ? <div className="inspector-panel-actions">{actions}</div> : null}
        </div>
      ) : null}
      <div className="inspector-panel-body">{children}</div>
      {footer ? <div className="inspector-panel-footer">{footer}</div> : null}
    </aside>
  );
}
