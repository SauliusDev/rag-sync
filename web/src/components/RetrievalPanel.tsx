import { useEffect, useState } from 'react';

import { fetchQuerySet, type RetrievalQuery } from '../api';

export function RetrievalPanel() {
  const [queries, setQueries] = useState<RetrievalQuery[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchQuerySet('formula-benchmark')
      .then((nextQueries) => {
        setQueries(nextQueries);
        setError('');
      })
      .catch((cause: unknown) => {
        setQueries([]);
        setError(cause instanceof Error ? cause.message : 'Failed to fetch query set');
      })
      .finally(() => setLoading(false));
  }, []);

  if (error) {
    return <p className="inline-error panel-message">{error}</p>;
  }

  if (loading) {
    return <p className="muted">Loading retrieval queries.</p>;
  }

  return (
    <div className="retrieval-list">
      {queries.map((query) => (
        <article className="query-card" key={query.id}>
          <h2>{query.id}</h2>
          <p>{query.question}</p>
        </article>
      ))}
    </div>
  );
}
