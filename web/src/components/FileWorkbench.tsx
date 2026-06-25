import { RefreshCcw, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { fetchFiles, scanProfile, type SourceFile } from '../api';

const defaultProfiles = ['quant-books-md', 'quant-papers', 'quant-articles', 'quant-videos'];

function fileName(path: string) {
  return path.split('/').pop() || path;
}

function formatState(state: string) {
  return state.replace(/_/g, ' ');
}

export function FileWorkbench() {
  const [files, setFiles] = useState<SourceFile[]>([]);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<SourceFile | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');

  async function reload() {
    setLoading(true);
    try {
      const nextFiles = await fetchFiles();
      setFiles(nextFiles);
      setSelected((current) => {
        if (current === null) return nextFiles[0] ?? null;
        return nextFiles.find((file) => file.id === current.id) ?? nextFiles[0] ?? null;
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
    setWorking(true);
    try {
      for (const profile of defaultProfiles) {
        await scanProfile(profile);
      }
      await reload();
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
    const normalized = query.trim().toLowerCase();
    if (!normalized) return files;
    return files.filter((file) =>
      [
        file.source_path,
        file.profile_name,
        file.source_type,
        file.extension,
        file.state,
        file.tags,
      ]
        .join(' ')
        .toLowerCase()
        .includes(normalized),
    );
  }, [files, query]);

  return (
    <div className="workbench">
      <div className="workbench-toolbar">
        <label className="search-field">
          <Search size={16} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search files"
            aria-label="Search source files"
          />
        </label>
        <button className="action-button" type="button" onClick={reload} disabled={loading}>
          <RefreshCcw size={16} aria-hidden="true" />
          Refresh
        </button>
        <button
          className="action-button primary"
          type="button"
          onClick={scanDefaultProfiles}
          disabled={working}
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
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={4} className="empty-cell">
                    Loading files
                  </td>
                </tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={4} className="empty-cell">
                    No files found
                  </td>
                </tr>
              ) : (
                filtered.map((file) => (
                  <tr
                    key={file.id}
                    className={selected?.id === file.id ? 'selected' : undefined}
                    onClick={() => setSelected(file)}
                  >
                    <td>
                      <span className="file-name">{fileName(file.source_path)}</span>
                      <span className="file-path">{file.source_path}</span>
                    </td>
                    <td>{file.source_type}</td>
                    <td>{file.profile_name}</td>
                    <td>
                      <span className={`state-badge state-${file.state}`}>
                        {formatState(file.state)}
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
