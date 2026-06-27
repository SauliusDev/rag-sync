import { RefreshCcw, Upload } from 'lucide-react';
import { useMemo, useState } from 'react';

import {
  importBatch,
  previewImportBatch,
  type ImportBatchPreviewFile,
  type ImportBatchPreviewResponse,
  type ImportBatchRequest,
  type ImportBatchResponse,
  type ImportBatchValidationStatus,
} from '../api';

type ImportBatchDialogProps = {
  onClose: () => void;
  onImported: (result: ImportBatchResponse) => void | Promise<void>;
  initialBatchDir?: string;
  initialPreview?: ImportBatchPreviewResponse | null;
  initialSelectedRelpaths?: string[];
  initialReason?: string;
};

type BuildImportBatchRequestArgs = {
  batchDir: string;
  preview: ImportBatchPreviewResponse | null;
  selectedRelpaths: string[];
  force: boolean;
  reason: string;
};

const readyStatuses = new Set<ImportBatchValidationStatus>(['match']);
const forceableStatuses = new Set<ImportBatchValidationStatus>(['hash_mismatch']);

function formatCount(count: number, label: string) {
  return `${count} ${label}`;
}

export function formatImportValidationStatus(status: ImportBatchValidationStatus) {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (value) => value.toUpperCase());
}

export function defaultImportBatchSelection(preview: ImportBatchPreviewResponse | null) {
  if (!preview) return [];
  return preview.files
    .filter((file) => readyStatuses.has(file.validation_status))
    .map((file) => file.source_relpath);
}

export function buildImportBatchRequest({
  batchDir,
  preview,
  selectedRelpaths,
  force,
  reason,
}: BuildImportBatchRequestArgs): { request: ImportBatchRequest } | { error: string } {
  const normalizedBatchDir = batchDir.trim();
  if (!normalizedBatchDir) {
    return { error: 'Batch directory is required' };
  }
  if (!preview) {
    return { error: 'Preview the batch before importing' };
  }

  const selected = new Set(selectedRelpaths);
  const selectedFiles = preview.files.filter((file) => selected.has(file.source_relpath));

  if (force) {
    if (selectedFiles.length === 0) {
      return { error: 'Select at least one batch row to force import' };
    }
    if (!selectedFiles.some((file) => forceableStatuses.has(file.validation_status))) {
      return { error: 'Select at least one hash mismatch to force import' };
    }
    if (!reason.trim()) {
      return { error: 'Force import requires a reason' };
    }
    return {
      request: {
        batch_dir: normalizedBatchDir,
        force: true,
        reason: reason.trim(),
        selected_relpaths: selectedFiles.map((file) => file.source_relpath),
      },
    };
  }

  const readyFiles = selectedFiles.filter((file) => readyStatuses.has(file.validation_status));
  if (readyFiles.length === 0) {
    return { error: 'Select at least one ready row for strict import' };
  }

  return {
    request: {
      batch_dir: normalizedBatchDir,
      selected_relpaths: readyFiles.map((file) => file.source_relpath),
    },
  };
}

function validationTone(status: ImportBatchValidationStatus) {
  if (status === 'match') return 'ready';
  if (status === 'hash_mismatch') return 'warning';
  return 'blocked';
}

function statusTone(file: ImportBatchPreviewFile) {
  return file.status === 'ok' ? 'ready' : 'blocked';
}

export function ImportBatchDialog({
  onClose,
  onImported,
  initialBatchDir = '',
  initialPreview = null,
  initialSelectedRelpaths,
  initialReason = '',
}: ImportBatchDialogProps) {
  const [batchDir, setBatchDir] = useState(initialBatchDir);
  const [preview, setPreview] = useState<ImportBatchPreviewResponse | null>(initialPreview);
  const [selectedRelpaths, setSelectedRelpaths] = useState<string[]>(
    initialSelectedRelpaths ?? defaultImportBatchSelection(initialPreview),
  );
  const [forceReason, setForceReason] = useState(initialReason);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [loadingImport, setLoadingImport] = useState<'strict' | 'force' | ''>('');
  const [error, setError] = useState('');

  const selectedSet = useMemo(() => new Set(selectedRelpaths), [selectedRelpaths]);
  const selectedFiles = useMemo(
    () => preview?.files.filter((file) => selectedSet.has(file.source_relpath)) ?? [],
    [preview, selectedSet],
  );
  const selectedReadyCount = selectedFiles.filter((file) =>
    readyStatuses.has(file.validation_status),
  ).length;
  const selectedMismatchCount = selectedFiles.filter((file) =>
    forceableStatuses.has(file.validation_status),
  ).length;

  function toggleSelection(relpath: string, checked: boolean) {
    setSelectedRelpaths((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(relpath);
      } else {
        next.delete(relpath);
      }
      return Array.from(next);
    });
  }

  function selectByStatus(mode: 'ready' | 'mismatch') {
    if (!preview) return;
    const next = preview.files
      .filter((file) =>
        mode === 'ready'
          ? readyStatuses.has(file.validation_status)
          : forceableStatuses.has(file.validation_status),
      )
      .map((file) => file.source_relpath);
    setSelectedRelpaths(next);
  }

  async function handlePreview() {
    const normalizedBatchDir = batchDir.trim();
    if (!normalizedBatchDir) {
      setError('Batch directory is required');
      return;
    }

    setLoadingPreview(true);
    setError('');
    try {
      const nextPreview = await previewImportBatch({ batch_dir: normalizedBatchDir });
      setPreview(nextPreview);
      setSelectedRelpaths(defaultImportBatchSelection(nextPreview));
      setForceReason('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to preview import batch');
    } finally {
      setLoadingPreview(false);
    }
  }

  async function handleImport(force: boolean) {
    const submission = buildImportBatchRequest({
      batchDir,
      preview,
      selectedRelpaths,
      force,
      reason: forceReason,
    });
    if ('error' in submission) {
      setError(submission.error);
      return;
    }

    setLoadingImport(force ? 'force' : 'strict');
    setError('');
    try {
      const result = await importBatch(submission.request);
      await onImported(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to import batch');
    } finally {
      setLoadingImport('');
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        className="dialog-shell import-batch-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Import batch"
      >
        <div className="dialog-header">
          <div>
            <h2>Import batch</h2>
            <p>Preview a transferred Marker batch and import only the rows you approve.</p>
          </div>
          <button className="action-button" type="button" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="dialog-section">
          <label className="dialog-field">
            <span>Batch directory</span>
            <input
              type="text"
              value={batchDir}
              onChange={(event) => setBatchDir(event.target.value)}
              placeholder="/path/to/batch-dir"
            />
          </label>
          <button
            className="action-button primary"
            type="button"
            onClick={() => void handlePreview()}
            disabled={loadingPreview}
          >
            <RefreshCcw size={16} aria-hidden="true" />
            {loadingPreview ? 'Previewing' : 'Preview batch'}
          </button>
        </div>

        {error ? <p className="inline-error">{error}</p> : null}

        {preview ? (
          <>
            <div className="import-batch-summary">
              <div className="import-batch-card">
                <dt>Batch</dt>
                <dd>{preview.batch_id}</dd>
              </div>
              <div className="import-batch-card">
                <dt>Profile</dt>
                <dd>{preview.profile}</dd>
              </div>
              <div className="import-batch-card">
                <dt>Parser</dt>
                <dd>
                  {preview.parser} {preview.parser_version}
                </dd>
              </div>
              <div className="import-batch-card">
                <dt>Summary</dt>
                <dd>
                  {formatCount(preview.summary.total, 'files')} ·{' '}
                  {formatCount(preview.summary.importable, 'ready')} ·{' '}
                  {formatCount(preview.summary.hash_mismatch, 'mismatch')}
                </dd>
              </div>
            </div>

            <div className="dialog-section dialog-section-compact">
              <div className="bulk-actions" aria-label="Batch selection actions">
                <button className="action-button" type="button" onClick={() => selectByStatus('ready')}>
                  Select ready
                </button>
                <button
                  className="action-button"
                  type="button"
                  onClick={() => selectByStatus('mismatch')}
                >
                  Select mismatches
                </button>
                <button className="action-button" type="button" onClick={() => setSelectedRelpaths([])}>
                  Clear selection
                </button>
              </div>
              <div className="table-summary" aria-live="polite">
                {selectedRelpaths.length} selected · {selectedReadyCount} ready · {selectedMismatchCount} mismatch
              </div>
            </div>

            <div className="dialog-table-wrap">
              <table className="settings-table import-batch-table">
                <thead>
                  <tr>
                    <th>Pick</th>
                    <th>File</th>
                    <th>Remote</th>
                    <th>Validation</th>
                    <th>Markdown</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.files.map((file) => (
                    <tr key={file.source_relpath}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${file.source_filename}`}
                          checked={selectedSet.has(file.source_relpath)}
                          onChange={(event) => toggleSelection(file.source_relpath, event.target.checked)}
                        />
                      </td>
                      <td>
                        <div className="import-batch-file">
                          <span className="file-name">{file.source_filename}</span>
                          <span className="file-path">{file.source_relpath}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`state-pill state-pill-${statusTone(file)}`}>{file.status}</span>
                      </td>
                      <td>
                        <span className={`state-pill state-pill-${validationTone(file.validation_status)}`}>
                          {formatImportValidationStatus(file.validation_status)}
                        </span>
                      </td>
                      <td>
                        <div className="import-batch-file">
                          <span className="file-path">{file.markdown_relpath}</span>
                          {file.local_source_sha256 ? (
                            <span className="file-path">Local hash {file.local_source_sha256}</span>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="dialog-section dialog-section-stack">
              <label className="dialog-field">
                <span>Force import reason</span>
                <textarea
                  value={forceReason}
                  onChange={(event) => setForceReason(event.target.value)}
                  placeholder="Why are these mismatches still safe to import?"
                  rows={3}
                />
              </label>
              <div className="file-actions" aria-label="Batch import actions">
                <button
                  className="action-button primary"
                  type="button"
                  onClick={() => void handleImport(false)}
                  disabled={loadingPreview || loadingImport !== '' || selectedReadyCount === 0}
                >
                  <Upload size={16} aria-hidden="true" />
                  {loadingImport === 'strict' ? 'Importing' : 'Strict import ready rows'}
                </button>
                <button
                  className="action-button danger"
                  type="button"
                  onClick={() => void handleImport(true)}
                  disabled={loadingPreview || loadingImport !== '' || selectedMismatchCount === 0}
                >
                  <Upload size={16} aria-hidden="true" />
                  {loadingImport === 'force' ? 'Importing' : 'Force import selected mismatches'}
                </button>
              </div>
            </div>
          </>
        ) : (
          <p className="details-empty">Preview a batch to review validation status and choose what to import.</p>
        )}
      </div>
    </div>
  );
}
