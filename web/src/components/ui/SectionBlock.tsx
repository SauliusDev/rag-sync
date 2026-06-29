import type { ReactNode } from 'react';

type SectionBlockProps = {
  children: ReactNode;
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  id?: string;
};

export function SectionBlock({
  children,
  title,
  description,
  actions,
  id,
}: SectionBlockProps) {
  return (
    <section className="section-block" aria-labelledby={id}>
      {title || description || actions ? (
        <div className="section-block-header">
          <div className="section-block-copy">
            {title ? <h2 id={id}>{title}</h2> : null}
            {description ? <p className="section-block-description">{description}</p> : null}
          </div>
          {actions ? <div className="section-block-actions">{actions}</div> : null}
        </div>
      ) : null}
      <div className="section-block-body">{children}</div>
    </section>
  );
}
