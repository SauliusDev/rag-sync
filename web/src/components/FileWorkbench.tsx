import { FileUp, Play, RefreshCcw, WandSparkles } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
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

export function FileWorkbench({ profiles, profilesError, profilesLoading }: FileWorkbenchProps) {
  const [files, setFiles] = useState<SourceFile[]>([]);
  const [filters, setFilters] = useState<FileFilterState>(() =>
    loadJson('rag-sync.file-filters', defaultFilters),
  );
  const [selected, setSelected] = useState<SourceFile | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [fileAction, setFileAction] = useState<'convert' | 'upload' | 'parse' | ''>('');
  const [error, setError] = useState('');

  async function reload() {
    setLoading(true);
    try {
      const nextFiles = await fetchFiles();
      setFiles(nextFiles);
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

  function selectFile(file: SourceFile) {
    setSelected(file);
    saveJson('rag-sync.selected-file-id', file.id);
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
        <div className="table-wrap">
          <table className="file-table">
            <thead>
              <tr>
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
                  <td colSpan={5} className="empty-cell">
                    Loading files
                  </td>
                </tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={5} className="empty-cell">
                    No files found
                  </td>
                </tr>
              ) : (
                filtered.map((file) => (
                  <tr
                    key={file.id}
                    className={selected?.id === file.id ? 'selected' : undefined}
                    aria-selected={selected?.id === file.id}
                  >
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
                      <span className={`state-badge state-${file.artifact ? 'converted' : file.state}`}>
                        {file.artifact ? file.artifact.parser : formatState(file.state)}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`state-badge state-${file.ragflow?.parse_status ?? 'not-uploaded'}`}
                      >
                        {file.ragflow?.parse_status ?? 'not uploaded'}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <aside className="file-details" aria-label="File details">
          {selected ? (
            <>
              <div>
                <h2>{fileName(selected.source_path)}</h2>
                <p>{selected.source_path}</p>
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
