import { FileUp, Play, RefreshCcw, WandSparkles } from 'lucide-react';
import { useEffect, useMemo, useState, type UIEvent } from 'react';

import {
  bulkEnqueueJobs,
  convertFile,
  enqueueJob,
  fetchFiles,
  parseFile,
  scanProfile,
  type Profile,
  type SourceFile,
  uploadFile,
} from '../api';
import { loadJson, saveJson } from '../storage';
import { FileFilters, type FileFilterState } from './FileFilters';

function fileName(path: string) {
  return path.split('/').pop() || path;
}

function formatState(state: string) {
  return state.replace(/_/g, ' ');
}

export function buildConversionStage(file: SourceFile) {
  if (file.artifact) {
    return {
      label: file.artifact.parser,
      detail: file.artifact.quality_status,
      progress: 100,
      tone: 'completed',
    };
  }
  if (file.job?.status === 'running' && ['sync_file', 'convert'].includes(file.job.kind)) {
    return {
      label: 'Marker running',
      detail: 'Converting',
      progress: Math.max(8, Math.round((file.job.progress || 0.35) * 100)),
      tone: 'running',
    };
  }
  if (file.job?.status === 'queued' && ['sync_file', 'convert'].includes(file.job.kind)) {
    return { label: 'Queued', detail: 'Waiting for conversion', progress: 0, tone: 'queued' };
  }
  return {
    label: formatState(file.state),
    detail: file.extension,
    progress: 0,
    tone: 'queued',
  };
}

export function buildRagflowStage(file: SourceFile) {
  if (file.ragflow?.parse_status === 'parsed') {
    return {
      label: 'Parsed',
      detail:
        file.ragflow.chunk_count != null ? `${file.ragflow.chunk_count} chunks` : 'Indexed',
      progress: 100,
      tone: 'completed',
    };
  }
  if (file.job?.status === 'running' && ['sync_file', 'upload', 'parse'].includes(file.job.kind)) {
    const hasUpload = Boolean(file.ragflow);
    return {
      label: hasUpload ? 'Parsing' : 'Uploading',
      detail: hasUpload ? 'RAGFlow ingest' : 'Sending markdown',
      progress: hasUpload ? 82 : 65,
      tone: 'running',
    };
  }
  if (file.job?.status === 'queued' && ['sync_file', 'upload', 'parse'].includes(file.job.kind)) {
    return { label: 'Queued', detail: 'Waiting for RAGFlow', progress: 0, tone: 'queued' };
  }
  if (file.ragflow?.upload_status === 'uploaded') {
    return { label: 'Uploaded', detail: 'Ready to parse', progress: 68, tone: 'running' };
  }
  return { label: 'Not uploaded', detail: 'No RAGFlow document', progress: 0, tone: 'queued' };
}

function summarizeCounts(values: string[]) {
  const counts = new Map<string, number>();
  for (const value of values) {
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .map(([value, count]) => `${value} (${count})`)
    .join(', ');
}

export function buildSelectionSummary(files: SourceFile[]) {
  return {
    title: `${files.length} files selected`,
    selectionLabel: 'Bulk selection active',
    profileSummary: summarizeCounts(files.map((file) => file.profile_name)),
    typeSummary: summarizeCounts(files.map((file) => file.source_type)),
    stateSummary: summarizeCounts(files.map((file) => file.state)),
    parserSummary: summarizeCounts(files.map((file) => file.artifact?.parser ?? 'not converted')),
    ragflowSummary: summarizeCounts(
      files.map((file) => file.ragflow?.parse_status ?? 'not uploaded'),
    ),
  };
}

type FileWorkbenchProps = {
  profiles: Profile[];
  profilesError: string;
  profilesLoading: boolean;
};

const defaultFilters: FileFilterState = {
  query: '',
  profile: '',
  sourceType: '',
  state: '',
  parser: '',
  ragflow: '',
};

export const INITIAL_VISIBLE_ROWS = 50;
const VISIBLE_ROW_BATCH = 50;
const SCROLL_LOAD_THRESHOLD = 240;

export function growVisibleCount(current: number, total: number) {
  return Math.min(total, current + VISIBLE_ROW_BATCH);
}

export function isNearListEnd(metrics: {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
}) {
  return metrics.scrollTop + metrics.clientHeight >= metrics.scrollHeight - SCROLL_LOAD_THRESHOLD;
}

export function FileWorkbench({ profiles, profilesError, profilesLoading }: FileWorkbenchProps) {
  const [files, setFiles] = useState<SourceFile[]>([]);
  const [filters, setFilters] = useState<FileFilterState>(() =>
    loadJson('rag-sync.file-filters', defaultFilters),
  );
  const [selected, setSelected] = useState<SourceFile | null>(null);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_ROWS);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [fileAction, setFileAction] = useState<'convert' | 'upload' | 'parse' | ''>('');
  const [error, setError] = useState('');

  async function reload() {
    setLoading(true);
    try {
      const nextFiles = await fetchFiles();
      setFiles(nextFiles);
      setSelectedIds((current) =>
        current.filter((sourceFileId) => nextFiles.some((file) => file.id === sourceFileId)),
      );
      setSelected((current) => {
        const rememberedId = loadJson<number | null>('rag-sync.selected-file-id', null);
        const selectedId = current?.id ?? rememberedId;
        if (selectedId == null) return nextFiles[0] ?? null;
        return nextFiles.find((file) => file.id === selectedId) ?? nextFiles[0] ?? null;
      });
      setError('');
    } catch (cause) {
      setFiles([]);
      setSelected(null);
      setError(cause instanceof Error ? cause.message : 'Failed to fetch files');
    } finally {
      setLoading(false);
    }
  }

  async function scanDefaultProfiles() {
    if (profilesError) {
      setError(profilesError);
      return;
    }
    if (profilesLoading) {
      setError('Profiles are still loading');
      return;
    }
    if (profiles.length === 0) {
      setError('No profiles configured');
      return;
    }
    const profileNames = profiles.map((profile) => profile.name);
    setWorking(true);
    try {
      const failures: string[] = [];
      for (const profile of profileNames) {
        try {
          await scanProfile(profile);
        } catch {
          failures.push(profile);
        }
      }
      await reload();
      if (failures.length > 0) {
        setError(`Scan failed for: ${failures.join(', ')}`);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to scan profiles');
    } finally {
      setWorking(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  const filtered = useMemo(() => {
    const normalized = filters.query.trim().toLowerCase();
    return files.filter((file) =>
      {
        const parser = file.artifact?.parser ?? '';
        const ragflowStatus = file.ragflow?.parse_status ?? 'not_uploaded';
        const matchesSearch =
          !normalized ||
          [
            file.source_path,
            file.profile_name,
            file.source_type,
            file.extension,
            file.state,
            file.tags,
            parser,
            ragflowStatus,
          ]
            .join(' ')
            .toLowerCase()
            .includes(normalized);
        return (
          matchesSearch &&
          (!filters.profile || file.profile_name === filters.profile) &&
          (!filters.sourceType || file.source_type === filters.sourceType) &&
          (!filters.state || file.state === filters.state) &&
          (!filters.parser || parser === filters.parser) &&
          (!filters.ragflow || ragflowStatus === filters.ragflow)
        );
      },
    );
  }, [files, filters]);

  const visibleFiles = useMemo(
    () => filtered.slice(0, Math.min(filtered.length, visibleCount)),
    [filtered, visibleCount],
  );
  const visibleFileIds = useMemo(() => visibleFiles.map((file) => file.id), [visibleFiles]);
  const filteredIds = useMemo(() => filtered.map((file) => file.id), [filtered]);
  const selectedIdSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedFiles = useMemo(
    () => files.filter((file) => selectedIdSet.has(file.id)),
    [files, selectedIdSet],
  );
  const visibleSelectedCount = useMemo(
    () => visibleFileIds.filter((sourceFileId) => selectedIdSet.has(sourceFileId)).length,
    [selectedIdSet, visibleFileIds],
  );
  const allVisibleSelected = visibleFiles.length > 0 && visibleSelectedCount === visibleFiles.length;
  const selectedCount = selectedIds.length;
  const bulkSelectionActive = selectedFiles.length > 1;
  const selectionSummary = useMemo(
    () => (bulkSelectionActive ? buildSelectionSummary(selectedFiles) : null),
    [bulkSelectionActive, selectedFiles],
  );

  useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE_ROWS);
  }, [filters, files]);

  function handleListScroll(event: UIEvent<HTMLDivElement>) {
    if (visibleCount >= filtered.length) return;
    const currentTarget = event.currentTarget;
    if (
      isNearListEnd({
        scrollTop: currentTarget.scrollTop,
        clientHeight: currentTarget.clientHeight,
        scrollHeight: currentTarget.scrollHeight,
      })
    ) {
      setVisibleCount((current) => growVisibleCount(current, filtered.length));
    }
  }

  function selectFile(file: SourceFile) {
    setSelected(file);
    saveJson('rag-sync.selected-file-id', file.id);
  }

  function toggleFileSelection(sourceFileId: number, checked: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(sourceFileId);
      } else {
        next.delete(sourceFileId);
      }
      return Array.from(next);
    });
  }

  function toggleVisibleSelection(checked: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current);
      for (const sourceFileId of visibleFileIds) {
        if (checked) {
          next.add(sourceFileId);
        } else {
          next.delete(sourceFileId);
        }
      }
      return Array.from(next);
    });
  }

  function selectFilteredFiles() {
    setSelectedIds(filteredIds);
  }

  function clearSelection() {
    setSelectedIds([]);
  }

  function updateFilters(next: FileFilterState) {
    setFilters(next);
    saveJson('rag-sync.file-filters', next);
  }

  async function runFileAction(action: 'convert' | 'upload' | 'parse') {
    if (!selected) return;
    setFileAction(action);
    setError('');
    try {
      if (action === 'convert') {
        const profile = profiles.find((candidate) => candidate.name === selected.profile_name);
        await convertFile(selected.id, profile?.parser_mode);
      } else if (action === 'upload') {
        await uploadFile(selected.id);
      } else {
        await parseFile(selected.id);
      }
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Failed to ${action} file`);
    } finally {
      setFileAction('');
    }
  }

  async function enqueueSelected(kind: 'sync_file' | 'restart_ragflow') {
    if (!selected) return;
    setError('');
    try {
      await enqueueJob({
        kind,
        source_file_id: selected.id,
        profile_name: selected.profile_name,
      });
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Failed to enqueue ${kind}`);
    }
  }

  async function enqueueBulk(kind: 'sync_file' | 'sync_filtered') {
    setError('');
    try {
      if (kind === 'sync_file') {
        if (selectedIds.length === 0) return;
        await bulkEnqueueJobs({ kind, source_file_ids: selectedIds });
      } else {
        await bulkEnqueueJobs({ kind, filters });
      }
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Failed to enqueue ${kind}`);
    }
  }

  return (
    <div className="workbench">
      <div className="workbench-toolbar">
        <FileFilters filters={filters} profiles={profiles} onChange={updateFilters} />
        <button className="action-button" type="button" onClick={reload} disabled={loading}>
          <RefreshCcw size={16} aria-hidden="true" />
          Refresh
        </button>
        <button
          className="action-button primary"
          type="button"
          onClick={scanDefaultProfiles}
          disabled={working || profilesLoading || profiles.length === 0 || Boolean(profilesError)}
        >
          <RefreshCcw size={16} aria-hidden="true" />
          {working ? 'Scanning' : 'Scan profiles'}
        </button>
      </div>

      {error ? <p className="inline-error">{error}</p> : null}

      <div className="workbench-split">
        <div className="table-pane">
          <div className="table-toolbar">
            <div className="table-summary" aria-live="polite">
              {selectedCount} selected · Showing {Math.min(filtered.length, visibleCount)} of {filtered.length} files
            </div>
            <div className="bulk-actions" aria-label="Bulk file actions">
              <button
                className="action-button"
                type="button"
                onClick={() => toggleVisibleSelection(true)}
                disabled={visibleFiles.length === 0}
              >
                Select visible
              </button>
              <button
                className="action-button"
                type="button"
                onClick={selectFilteredFiles}
                disabled={filtered.length === 0}
              >
                Select filtered
              </button>
              <button
                className="action-button"
                type="button"
                onClick={clearSelection}
                disabled={selectedCount === 0}
              >
                Clear selection
              </button>
              <button
                className="action-button primary"
                type="button"
                onClick={() => enqueueBulk('sync_file')}
                disabled={selectedCount === 0}
              >
                Sync selected
              </button>
              <button
                className="action-button primary"
                type="button"
                onClick={() => enqueueBulk('sync_filtered')}
                disabled={filtered.length === 0}
              >
                Sync filtered
              </button>
            </div>
          </div>
          <div className="table-wrap" onScroll={handleListScroll}>
            <table className="file-table">
              <thead>
                <tr>
                  <th>
                    <input
                      type="checkbox"
                      aria-label="Select visible files"
                      checked={allVisibleSelected}
                      onChange={(event) => toggleVisibleSelection(event.target.checked)}
                    />
                  </th>
                  <th>File</th>
                  <th>Type</th>
                  <th>Profile</th>
                  <th>Conversion</th>
                  <th>RAGFlow</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={6} className="empty-cell">
                      Loading files
                    </td>
                  </tr>
                ) : filtered.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="empty-cell">
                      No files found
                    </td>
                  </tr>
                ) : (
                  visibleFiles.map((file) => (
                    <tr
                      key={file.id}
                      className={selected?.id === file.id ? 'selected' : undefined}
                      aria-selected={selected?.id === file.id}
                    >
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${fileName(file.source_path)}`}
                          checked={selectedIdSet.has(file.id)}
                          onChange={(event) => toggleFileSelection(file.id, event.target.checked)}
                          onClick={(event) => event.stopPropagation()}
                        />
                      </td>
                      <td>
                        <button
                          className="file-select-button"
                          type="button"
                          onClick={() => selectFile(file)}
                        >
                          <span className="file-name">{fileName(file.source_path)}</span>
                          <span className="file-path">{file.source_path}</span>
                        </button>
                      </td>
                      <td>{file.source_type}</td>
                      <td>{file.profile_name}</td>
                      <td>
                        <StageCell stage={buildConversionStage(file)} />
                      </td>
                      <td>
                        <StageCell stage={buildRagflowStage(file)} />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="file-details" aria-label="File details">
          {bulkSelectionActive && selectionSummary ? (
            <>
              <div>
                <h2>{selectionSummary.title}</h2>
                <p>{selectionSummary.selectionLabel}</p>
              </div>
              <div className="file-actions" aria-label="Bulk sync actions">
                <button
                  className="action-button primary"
                  type="button"
                  onClick={() => enqueueBulk('sync_file')}
                  disabled={selectedCount === 0}
                >
                  <Play size={16} aria-hidden="true" />
                  Sync selected
                </button>
                <button
                  className="action-button"
                  type="button"
                  onClick={clearSelection}
                  disabled={selectedCount === 0}
                >
                  <RefreshCcw size={16} aria-hidden="true" />
                  Clear selection
                </button>
              </div>
              <dl>
                <div>
                  <dt>Profiles</dt>
                  <dd>{selectionSummary.profileSummary}</dd>
                </div>
                <div>
                  <dt>Types</dt>
                  <dd>{selectionSummary.typeSummary}</dd>
                </div>
                <div>
                  <dt>States</dt>
                  <dd>{selectionSummary.stateSummary}</dd>
                </div>
                <div>
                  <dt>Parsers</dt>
                  <dd>{selectionSummary.parserSummary}</dd>
                </div>
                <div>
                  <dt>RAGFlow</dt>
                  <dd>{selectionSummary.ragflowSummary}</dd>
                </div>
              </dl>
            </>
          ) : selected ? (
            <>
              <div>
                <h2>{fileName(selected.source_path)}</h2>
                <p>{selected.source_path}</p>
                <p className="details-meta">
                  {selectedIdSet.has(selected.id)
                    ? `Selected for bulk actions · ${selectedCount} total selected`
                    : `${selectedCount} selected`}
                </p>
              </div>
              <div className="file-actions" aria-label="File sync actions">
                <button
                  className="action-button primary"
                  type="button"
                  onClick={() => runFileAction('convert')}
                  disabled={Boolean(fileAction)}
                >
                  <WandSparkles size={16} aria-hidden="true" />
                  {fileAction === 'convert' ? 'Converting' : 'Convert'}
                </button>
                <button
                  className="action-button"
                  type="button"
                  onClick={() => runFileAction('upload')}
                  disabled={Boolean(fileAction)}
                >
                  <FileUp size={16} aria-hidden="true" />
                  {fileAction === 'upload' ? 'Uploading' : 'Upload'}
                </button>
                <button
                  className="action-button"
                  type="button"
                  onClick={() => runFileAction('parse')}
                  disabled={Boolean(fileAction)}
                >
                  <Play size={16} aria-hidden="true" />
                  {fileAction === 'parse' ? 'Parsing' : 'Parse'}
                </button>
                <button
                  className="action-button primary"
                  type="button"
                  onClick={() => enqueueSelected('sync_file')}
                >
                  <Play size={16} aria-hidden="true" />
                  Sync
                </button>
                <button
                  className="action-button"
                  type="button"
                  onClick={() => enqueueSelected('restart_ragflow')}
                >
                  <RefreshCcw size={16} aria-hidden="true" />
                  Restart RAGFlow
                </button>
              </div>
              <dl>
                <div>
                  <dt>Profile</dt>
                  <dd>{selected.profile_name}</dd>
                </div>
                <div>
                  <dt>Type</dt>
                  <dd>{selected.source_type}</dd>
                </div>
                <div>
                  <dt>Extension</dt>
                  <dd>{selected.extension}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>{formatState(selected.state)}</dd>
                </div>
                <div>
                  <dt>Conversion stage</dt>
                  <dd>
                    {buildConversionStage(selected).label} · {buildConversionStage(selected).detail}
                  </dd>
                </div>
                <div>
                  <dt>RAGFlow stage</dt>
                  <dd>{buildRagflowStage(selected).label} · {buildRagflowStage(selected).detail}</dd>
                </div>
                <div>
                  <dt>Included</dt>
                  <dd>{selected.included ? 'yes' : 'no'}</dd>
                </div>
                <div>
                  <dt>Tags</dt>
                  <dd>{selected.tags || 'none'}</dd>
                </div>
                <div>
                  <dt>Note</dt>
                  <dd>{selected.note || 'none'}</dd>
                </div>
                <div>
                  <dt>Updated</dt>
                  <dd>{selected.updated_at}</dd>
                </div>
                <div>
                  <dt>Artifact</dt>
                  <dd>{selected.artifact?.output_path ?? 'none'}</dd>
                </div>
                <div>
                  <dt>Artifact quality</dt>
                  <dd>{selected.artifact?.quality_status ?? 'none'}</dd>
                </div>
                <div>
                  <dt>RAGFlow document</dt>
                  <dd>{selected.ragflow?.document_name ?? 'none'}</dd>
                </div>
                <div>
                  <dt>RAGFlow status</dt>
                  <dd>{selected.ragflow?.parse_status ?? 'not uploaded'}</dd>
                </div>
                <div>
                  <dt>Chunks</dt>
                  <dd>{selected.ragflow?.chunk_count ?? '-'}</dd>
                </div>
              </dl>
            </>
          ) : (
            <p className="details-empty">Select a file to inspect sync state.</p>
          )}
        </aside>
      </div>
    </div>
  );
}

function StageCell({
  stage,
}: {
  stage: { label: string; detail: string; progress: number; tone: string };
}) {
  return (
    <div className="stage-cell">
      <div className="stage-cell-top">
        <span className={`state-badge state-${stage.tone}`}>{stage.label}</span>
        <span className="stage-percent">{stage.progress}%</span>
      </div>
      <div className="mini-progress">
        <span style={{ width: `${stage.progress}%` }} />
      </div>
      <div className="stage-detail">{stage.detail}</div>
    </div>
  );
}
