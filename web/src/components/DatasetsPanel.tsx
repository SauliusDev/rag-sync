import { useEffect, useMemo, useState } from 'react';

import type { DatasetDrift, DatasetSummary } from '../api';
import { DataTableShell } from './ui/DataTableShell';
import { SectionBlock } from './ui/SectionBlock';

function formatValue(value: DatasetDrift['expected']) {
  if (typeof value === 'boolean') {
    return value ? 'on' : 'off';
  }
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  return String(value);
}

function coverageLabel(dataset: DatasetSummary) {
  const { file_count, parsed_documents, stuck_documents, failed_documents } = dataset.coverage;
  return `${file_count} files · ${parsed_documents} parsed · ${stuck_documents} stuck · ${failed_documents} failed`;
}

function statusLabel(datasetCount: number, loading: boolean) {
  if (loading) {
    return datasetCount > 0 ? 'Refreshing dataset overview' : 'Loading dataset overview';
  }
  return `${datasetCount} datasets in view`;
}

type DatasetsPanelProps = {
  datasets: DatasetSummary[];
  loading: boolean;
  error: string;
  remoteError: string;
};

export function DatasetsPanel({ datasets, loading, error, remoteError }: DatasetsPanelProps) {
  const [selectedName, setSelectedName] = useState<string>('');

  useEffect(() => {
    if (datasets.length === 0) {
      if (selectedName) {
        setSelectedName('');
      }
      return;
    }
    if (!selectedName || !datasets.some((dataset) => dataset.name === selectedName)) {
      setSelectedName(datasets[0].name);
    }
  }, [datasets, selectedName]);

  const selectedDataset = useMemo(
    () => datasets.find((dataset) => dataset.name === selectedName) ?? datasets[0] ?? null,
    [datasets, selectedName],
  );

  if (error) {
    return (
      <p className="muted" role="alert">
        {error}
      </p>
    );
  }

  if (loading && datasets.length === 0) {
    return (
      <p className="muted" role="status" aria-live="polite">
        Loading dataset overview.
      </p>
    );
  }

  if (datasets.length === 0) {
    return <p className="muted">No datasets are connected to configured profiles.</p>;
  }

  const activeDataset = selectedDataset ?? datasets[0];

  return (
    <div className="datasets-view">
      {remoteError ? (
        <p className="dataset-banner" role="alert">
          {remoteError}
        </p>
      ) : null}
      <div className="datasets-layout">
        <SectionBlock
          title="Datasets"
          description={statusLabel(datasets.length, loading)}
          id="datasets-list-title"
        >
          <div className="datasets-selection-grid" aria-labelledby="datasets-list-title">
            {datasets.map((dataset) => {
              const selected = dataset.name === activeDataset.name;
              return (
                <button
                  key={dataset.name}
                  type="button"
                  className={selected ? 'dataset-list-item is-selected' : 'dataset-list-item'}
                  aria-pressed={selected}
                  aria-controls="dataset-detail-panel"
                  onClick={() => setSelectedName(dataset.name)}
                >
                  <div className="dataset-list-header">
                    <strong>{dataset.name}</strong>
                    <span className={dataset.exists ? 'dataset-chip' : 'dataset-chip missing'}>
                      {dataset.exists ? 'Connected' : 'Missing in RAGFlow'}
                    </span>
                  </div>
                  <p className="dataset-summary">{coverageLabel(dataset)}</p>
                  <div className="dataset-list-meta">
                    {dataset.protected ? <span className="dataset-chip">Protected</span> : null}
                    <span>{dataset.profiles.length} profiles</span>
                    <span>{dataset.coverage.chunk_count} chunks</span>
                  </div>
                </button>
              );
            })}
          </div>
        </SectionBlock>

        <div className="datasets-detail-grid" id="dataset-detail-panel" aria-labelledby="dataset-detail-title">
          <SectionBlock
            title={activeDataset.name}
            description={coverageLabel(activeDataset)}
            id="dataset-detail-title"
          >
            <dl className="settings-list">
              <div>
                <dt>Status</dt>
                <dd>{activeDataset.exists ? 'Connected in RAGFlow' : 'Missing in RAGFlow'}</dd>
              </div>
              <div>
                <dt>Protection</dt>
                <dd>{activeDataset.protected ? 'Protected' : 'Standard'}</dd>
              </div>
              <div>
                <dt>Profiles</dt>
                <dd>{activeDataset.profiles.length}</dd>
              </div>
              <div>
                <dt>Indexed docs</dt>
                <dd>{activeDataset.coverage.indexed_documents}</dd>
              </div>
              <div>
                <dt>Parsed docs</dt>
                <dd>{activeDataset.coverage.parsed_documents}</dd>
              </div>
              <div>
                <dt>Chunks</dt>
                <dd>{activeDataset.coverage.chunk_count}</dd>
              </div>
              <div>
                <dt>Remote docs</dt>
                <dd>{activeDataset.remote?.document_count ?? activeDataset.coverage.indexed_documents}</dd>
              </div>
            </dl>
          </SectionBlock>

          <SectionBlock title="Profiles" id="dataset-profiles-title">
            <div className="dataset-profile-grid">
              {activeDataset.profiles.length > 0 ? (
                activeDataset.profiles.map((profile) => (
                  <article className="dataset-profile-card" key={profile.name}>
                    <div className="dataset-profile-header">
                      <strong>{profile.name}</strong>
                      <span>{profile.file_count} files</span>
                    </div>
                    <p>
                      {profile.parser_mode} · {profile.source_type}
                    </p>
                    <p>{profile.source_paths.join(', ')}</p>
                  </article>
                ))
              ) : (
                <p className="dataset-empty">No profiles target this dataset.</p>
              )}
            </div>
          </SectionBlock>

          <SectionBlock title="Drift" id="dataset-drift-title">
            {activeDataset.drift.length > 0 ? (
              <DataTableShell label="Dataset drift detail">
                <div className="settings-table-wrap">
                  <table className="settings-table">
                    <thead>
                      <tr>
                        <th scope="col">Field</th>
                        <th scope="col">Expected</th>
                        <th scope="col">Actual</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activeDataset.drift.map((item) => (
                        <tr key={item.field}>
                          <th scope="row">{item.label}</th>
                          <td>{formatValue(item.expected)}</td>
                          <td>{formatValue(item.actual)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </DataTableShell>
            ) : (
              <p className="dataset-empty">Matches configured defaults.</p>
            )}
          </SectionBlock>
        </div>
      </div>
    </div>
  );
}
