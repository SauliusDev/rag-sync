import type { ReactNode } from 'react';

type ScreenHeaderProps = {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  id?: string;
};

export function ScreenHeader({ title, subtitle, actions, children, id }: ScreenHeaderProps) {
  return (
    <header className="screen-header">
      <div className="screen-header-row">
        <div className="screen-header-copy">
          <h1 id={id}>{title}</h1>
          {subtitle ? <p className="screen-header-subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="screen-header-actions">{actions}</div> : null}
      </div>
      {children ? <div className="screen-header-meta">{children}</div> : null}
    </header>
  );
}
