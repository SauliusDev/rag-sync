import { FileUp, Play, RefreshCcw, WandSparkles } from 'lucide-react';
import { useEffect, useMemo, useState, type UIEvent } from 'react';

import {
  bulkEnqueueJobs,
  convertFile,
  enqueueJob,
  fetchFiles,
  type ImportBatchResponse,
  parseFile,
  scanProfile,
  type Profile,
  type SourceFile,
  uploadFile,
} from '../api';
import { loadJson, saveJson } from '../storage';
import { FileFilters, type FileFilterState } from './FileFilters';
import { ImportBatchDialog } from './ImportBatchDialog';
import { DataTableShell } from './ui/DataTableShell';
import { ToolbarGroup } from './ui/ToolbarGroup';

function fileName(path: string) {
  return path.split('/').pop() || path;
}

function formatState(state: string) {
  return state.replace(/_/g, ' ');
}

type FileStage = {
  label: string;
  detail: string;
  progress: number | null;
  tone: string;
};

export function toneForFileState(state: string) {
  if (['failed', 'missing', 'changed'].includes(state)) {
    return state;
  }
  if (['parsed', 'uploaded', 'converted', 'completed'].includes(state)) {
    return 'completed';
  }
  if (state === 'canceled') {
    return 'canceled';
  }
  return 'queued';
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
      progress: Number.isFinite(file.job.progress) ? Math.round(file.job.progress * 100) : null,
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
    tone: toneForFileState(file.state),
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
      progress: Number.isFinite(file.job.progress) ? Math.round(file.job.progress * 100) : null,
      tone: 'running',
    };
  }
  if (file.job?.status === 'queued' && ['sync_file', 'upload', 'parse'].includes(file.job.kind)) {
    return { label: 'Queued', detail: 'Waiting for RAGFlow', progress: 0, tone: 'queued' };
  }
  if (file.ragflow?.upload_status === 'uploaded') {
    return { label: 'Uploaded', detail: 'Ready to parse', progress: 0, tone: 'queued' };
  }
  return {
    label: 'Not uploaded',
    detail: 'No RAGFlow document',
    progress: 0,
    tone: toneForFileState(file.state),
  };
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
  initialImportBatchOpen?: boolean;
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

export function resolveInspectorFile(
  selected: SourceFile | null,
  selectedFiles: SourceFile[],
): SourceFile | null {
  if (selectedFiles.length === 1) {
    return selectedFiles[0];
  }
  return selected;
}

export function resolveReloadSelection(
  nextFiles: SourceFile[],
  current: SourceFile | null,
): SourceFile | null {
  if (!current) {
    return null;
  }
  return nextFiles.find((file) => file.id === current.id) ?? null;
}

export function shouldShowSelectionBar(selectedCount: number) {
  return selectedCount > 1;
}

export function buildLibraryStatus({
  loading,
  working,
  bulkQueueAction,
  filteredCount,
}: {
  loading: boolean;
  working: boolean;
  bulkQueueAction: 'sync_file' | 'sync_filtered' | '';
  filteredCount: number;
}) {
  if (working) {
    return 'Scanning configured profiles';
  }
  if (bulkQueueAction === 'sync_filtered') {
    return 'Queueing filtered files';
  }
  if (bulkQueueAction === 'sync_file') {
    return 'Queueing selected files';
  }
  if (loading) {
    return 'Refreshing file library';
  }
  return `${filteredCount} files match current filters`;
}

export function FileWorkbench({
  profiles,
  profilesError,
  profilesLoading,
  initialImportBatchOpen = false,
}: FileWorkbenchProps) {
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
  const [selectedQueueAction, setSelectedQueueAction] = useState<'sync_file' | 'restart_ragflow' | ''>('');
  const [bulkQueueAction, setBulkQueueAction] = useState<'sync_file' | 'sync_filtered' | ''>('');
  const [importBatchOpen, setImportBatchOpen] = useState(initialImportBatchOpen);
  const [error, setError] = useState('');

  async function reload() {
    setLoading(true);
    try {
      const nextFiles = await fetchFiles();
      setFiles(nextFiles);
      setSelectedIds((current) =>
        current.filter((sourceFileId) => nextFiles.some((file) => file.id === sourceFileId)),
      );
      setSelected((current) => resolveReloadSelection(nextFiles, current));
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
  const inspectorFile = useMemo(
    () => resolveInspectorFile(selected, selectedFiles),
    [selected, selectedFiles],
  );
  const selectedConversionStage = inspectorFile ? buildConversionStage(inspectorFile) : null;
  const selectedRagflowStage = inspectorFile ? buildRagflowStage(inspectorFile) : null;

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
    if (!inspectorFile) return;
    setFileAction(action);
    setError('');
    try {
      if (action === 'convert') {
        const profile = profiles.find((candidate) => candidate.name === inspectorFile.profile_name);
        await convertFile(inspectorFile.id, profile?.parser_mode);
      } else if (action === 'upload') {
        await uploadFile(inspectorFile.id);
      } else {
        await parseFile(inspectorFile.id);
      }
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Failed to ${action} file`);
    } finally {
      setFileAction('');
    }
  }

  async function enqueueSelected(kind: 'sync_file' | 'restart_ragflow') {
    if (!inspectorFile) return;
    setSelectedQueueAction(kind);
    setError('');
    try {
      await enqueueJob({
        kind,
        source_file_id: inspectorFile.id,
        profile_name: inspectorFile.profile_name,
      });
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Failed to enqueue ${kind}`);
    } finally {
      setSelectedQueueAction('');
    }
  }

  async function enqueueBulk(kind: 'sync_file' | 'sync_filtered') {
    setBulkQueueAction(kind);
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
    } finally {
      setBulkQueueAction('');
    }
  }

  async function handleBatchImported(_: ImportBatchResponse) {
    await reload();
    setImportBatchOpen(false);
  }

  const currentVisibleCount = Math.min(filtered.length, visibleCount);
  const visibleSummary =
    filtered.length === 0
      ? 'No matching files'
      : `Showing ${currentVisibleCount} of ${filtered.length} files`;
  const libraryStatus = buildLibraryStatus({
    loading,
    working,
    bulkQueueAction,
    filteredCount: filtered.length,
  });
  const selectedFileActivity =
    fileAction === 'convert'
      ? 'Running conversion'
      : fileAction === 'upload'
        ? 'Uploading markdown to RAGFlow'
        : fileAction === 'parse'
          ? 'Starting RAGFlow parse'
          : selectedQueueAction === 'sync_file'
            ? 'Queueing sync job'
            : selectedQueueAction === 'restart_ragflow'
              ? 'Queueing RAGFlow restart'
              : '';
  const singleFileActionsDisabled =
    !inspectorFile || bulkSelectionActive || Boolean(fileAction) || Boolean(selectedQueueAction) || Boolean(bulkQueueAction);
  const selectionActionsDisabled =
    selectedCount === 0 || Boolean(fileAction) || Boolean(selectedQueueAction) || Boolean(bulkQueueAction);
  const focusTitle = bulkSelectionActive
    ? `${selectedCount} files selected`
    : inspectorFile
      ? fileName(inspectorFile.source_path)
      : 'No file selected';
  const focusCountLabel =
    selectedCount > 0 ? `${selectedCount} selected` : inspectorFile ? 'Focused' : '0 selected';
  const focusPath = bulkSelectionActive
    ? selectionSummary?.profileSummary ?? 'Selection spans multiple files.'
    : inspectorFile?.source_path ?? 'Select a file to inspect its sync state and launch file-specific actions.';

  return (
    <div className="workbench file-workbench">
      <div className="file-control-band">
        <div className="file-control-row">
          <FileFilters filters={filters} profiles={profiles} onChange={updateFilters} />
          <ToolbarGroup label="Library actions">
            <button className="action-button" type="button" onClick={reload} disabled={loading}>
              <RefreshCcw size={16} aria-hidden="true" />
              {loading ? 'Refreshing' : 'Refresh'}
            </button>
            <button
              className="action-button primary"
              type="button"
              onClick={scanDefaultProfiles}
              disabled={working || profilesLoading || profiles.length === 0 || Boolean(profilesError)}
            >
              <RefreshCcw size={16} aria-hidden="true" />
              {working ? 'Scanning profiles' : 'Scan profiles'}
            </button>
            <button className="action-button" type="button" onClick={() => setImportBatchOpen(true)}>
              <FileUp size={16} aria-hidden="true" />
              {importBatchOpen ? 'Import dialog open' : 'Import batch'}
            </button>
          </ToolbarGroup>
        </div>
        <div className="file-control-meta">
          <p className="file-activity-label" aria-live="polite">
            {libraryStatus}
          </p>
          <p className="file-activity-label">{visibleSummary}</p>
        </div>
        {error ? (
          <p className="inline-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>

      <div className={inspectorFile && !bulkSelectionActive ? 'file-focus-bar is-compact' : 'file-focus-bar'}>
        <div className="file-focus-copy">
          <div className="file-focus-topline">
            <strong>{focusTitle}</strong>
            <span className="file-focus-count">{focusCountLabel}</span>
          </div>
          <p className="file-focus-path">{focusPath}</p>
          <div className="file-focus-meta">
            {bulkSelectionActive && selectionSummary ? (
              <>
                <span>{selectionSummary.typeSummary}</span>
                <span>{selectionSummary.stateSummary}</span>
                <span>{selectionSummary.ragflowSummary}</span>
              </>
            ) : inspectorFile && selectedConversionStage && selectedRagflowStage ? (
              <>
                <span>{inspectorFile.profile_name}</span>
                <span>
                  {selectedConversionStage.label} · {selectedConversionStage.detail}
                </span>
                <span>
                  {selectedRagflowStage.label} · {selectedRagflowStage.detail}
                </span>
                {selectedFileActivity ? <span>{selectedFileActivity}</span> : null}
              </>
            ) : (
              <span>Choose a row to inspect, or use the checkboxes to queue work in bulk.</span>
            )}
          </div>
        </div>
        <div className="file-focus-actions" aria-label="File sync actions">
          <button
            className="action-button primary"
            type="button"
            onClick={() => runFileAction('convert')}
            disabled={singleFileActionsDisabled}
          >
            <WandSparkles size={16} aria-hidden="true" />
            {fileAction === 'convert' ? 'Converting' : 'Convert'}
          </button>
          <button
            className="action-button"
            type="button"
            onClick={() => runFileAction('upload')}
            disabled={singleFileActionsDisabled}
          >
            <FileUp size={16} aria-hidden="true" />
            {fileAction === 'upload' ? 'Uploading' : 'Upload'}
          </button>
          <button
            className="action-button"
            type="button"
            onClick={() => runFileAction('parse')}
            disabled={singleFileActionsDisabled}
          >
            <Play size={16} aria-hidden="true" />
            {fileAction === 'parse' ? 'Parsing' : 'Parse'}
          </button>
          <button
            className="action-button primary"
            type="button"
            onClick={() => enqueueSelected('sync_file')}
            disabled={singleFileActionsDisabled}
          >
            <Play size={16} aria-hidden="true" />
            {selectedQueueAction === 'sync_file' ? 'Queueing sync' : 'Sync'}
          </button>
          <button
            className="action-button"
            type="button"
            onClick={() => enqueueSelected('restart_ragflow')}
            disabled={singleFileActionsDisabled}
          >
            <RefreshCcw size={16} aria-hidden="true" />
            {selectedQueueAction === 'restart_ragflow' ? 'Queueing restart' : 'Restart RAGFlow'}
          </button>
          <button
            className="action-button"
            type="button"
            onClick={clearSelection}
            disabled={selectedCount === 0}
          >
            Clear
          </button>
          <button
            className="action-button"
            type="button"
            onClick={() => enqueueBulk('sync_file')}
            disabled={selectionActionsDisabled}
          >
            <Play size={16} aria-hidden="true" />
            {bulkQueueAction === 'sync_file' ? 'Queueing selected' : 'Sync selected'}
          </button>
        </div>
      </div>

      <div className="workbench-split file-workbench-split">
        <DataTableShell
          label="Source file library"
          toolbar={
            <div className="file-table-toolbar">
              <div className="table-summary" aria-live="polite">
                {selectedCount} selected · {visibleSummary}
              </div>
              <ToolbarGroup label="Selection tools">
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
                  onClick={() => enqueueBulk('sync_filtered')}
                  disabled={filtered.length === 0 || bulkQueueAction === 'sync_filtered'}
                >
                  <Play size={16} aria-hidden="true" />
                  {bulkQueueAction === 'sync_filtered' ? 'Queueing filtered' : 'Sync filtered'}
                </button>
              </ToolbarGroup>
            </div>
          }
          footer={
            filtered.length > currentVisibleCount ? (
              <div className="file-table-footer">
                <span className="table-summary">Scroll to load more rows</span>
                <span className="table-summary">
                  {currentVisibleCount} rendered · {filtered.length - currentVisibleCount} remaining
                </span>
              </div>
            ) : undefined
          }
        >
          <div className="table-wrap" onScroll={handleListScroll}>
            <table className="file-table file-table-dense">
              <thead>
                <tr>
                  <th scope="col">
                    <label className="table-checkbox-target">
                      <input
                        type="checkbox"
                        aria-label="Select visible files"
                        checked={allVisibleSelected}
                        onChange={(event) => toggleVisibleSelection(event.target.checked)}
                      />
                    </label>
                  </th>
                  <th scope="col">File</th>
                  <th scope="col">Type</th>
                  <th scope="col">Profile</th>
                  <th scope="col">Conversion</th>
                  <th scope="col">RAGFlow</th>
                </tr>
              </thead>
              <tbody>
                {loading && files.length === 0 ? (
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
                        <label className="table-checkbox-target">
                          <input
                            type="checkbox"
                            aria-label={`Select ${fileName(file.source_path)}`}
                            checked={selectedIdSet.has(file.id)}
                            onChange={(event) => toggleFileSelection(file.id, event.target.checked)}
                            onClick={(event) => event.stopPropagation()}
                          />
                        </label>
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
        </DataTableShell>
      </div>
      {importBatchOpen ? (
        <ImportBatchDialog
          onClose={() => setImportBatchOpen(false)}
          onImported={handleBatchImported}
        />
      ) : null}
    </div>
  );
}

export function StageCell({ stage }: { stage: FileStage }) {
  const showMeasuredProgress = typeof stage.progress === 'number';
  const progressValue = showMeasuredProgress ? stage.progress ?? undefined : undefined;

  return (
    <div className="stage-cell">
      <div className="stage-cell-top">
        <span className={`state-badge state-${stage.tone}`}>{stage.label}</span>
        {showMeasuredProgress ? <span className="stage-percent">{stage.progress}%</span> : null}
      </div>
      <div
        className={showMeasuredProgress ? 'mini-progress' : 'mini-progress is-indeterminate'}
        role="progressbar"
        aria-label={`${stage.label} progress`}
        aria-valuemin={showMeasuredProgress ? 0 : undefined}
        aria-valuemax={showMeasuredProgress ? 100 : undefined}
        aria-valuenow={progressValue}
        aria-valuetext={showMeasuredProgress ? undefined : stage.detail}
      >
        {showMeasuredProgress ? <span style={{ width: `${stage.progress}%` }} /> : null}
      </div>
      <div className="stage-detail">{stage.detail}</div>
    </div>
  );
}
