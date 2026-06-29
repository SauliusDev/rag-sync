import { Search, SlidersHorizontal } from 'lucide-react';

import type { Profile } from '../api';

export type FileFilterState = {
  query: string;
  profile: string;
  sourceType: string;
  state: string;
  parser: string;
  ragflow: string;
};

type FileFiltersProps = {
  filters: FileFilterState;
  profiles: Profile[];
  onChange: (filters: FileFilterState) => void;
};

const emptyFilterPatch = {
  profile: '',
  sourceType: '',
  state: '',
  parser: '',
  ragflow: '',
};

function activeFilterSummary(filters: FileFilterState) {
  const activeCount = [
    filters.profile,
    filters.sourceType,
    filters.state,
    filters.parser,
    filters.ragflow,
  ].filter(Boolean).length;

  return activeCount === 0 ? 'No extra filters' : `${activeCount} active`;
}

export function FileFilters({ filters, profiles, onChange }: FileFiltersProps) {
  function update(patch: Partial<FileFilterState>) {
    onChange({ ...filters, ...patch });
  }

  return (
    <div className="file-filters" aria-label="File filters">
      <div className="file-filter-cluster file-filter-cluster-search">
        <label className="search-field">
          <Search size={16} aria-hidden="true" />
          <input
            type="search"
            value={filters.query}
            onChange={(event) => update({ query: event.target.value })}
            placeholder="Search files"
            aria-label="Search source files"
          />
        </label>
      </div>

      <details className="file-filter-menu">
        <summary className="action-button file-filter-summary">
          <SlidersHorizontal size={16} aria-hidden="true" />
          <span>Filters</span>
          <span className="file-filter-count">{activeFilterSummary(filters)}</span>
        </summary>
        <div className="file-filter-popover">
          <div className="file-filter-popover-header">
            <strong>Filters</strong>
            <button
              className="action-button"
              type="button"
              onClick={() => update(emptyFilterPatch)}
            >
              Reset
            </button>
          </div>
          <div className="file-filter-controls">
            <label>
              <span>Profile</span>
              <select
                value={filters.profile}
                onChange={(event) => update({ profile: event.target.value })}
                aria-label="Filter by profile"
              >
                <option value="">All profiles</option>
                {profiles.map((profile) => (
                  <option key={profile.name} value={profile.name}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Type</span>
              <select
                value={filters.sourceType}
                onChange={(event) => update({ sourceType: event.target.value })}
                aria-label="Filter by source type"
              >
                <option value="">All types</option>
                <option value="book">Books</option>
                <option value="paper">Papers</option>
                <option value="article">Articles</option>
                <option value="video">Videos</option>
              </select>
            </label>
            <label>
              <span>State</span>
              <select
                value={filters.state}
                onChange={(event) => update({ state: event.target.value })}
                aria-label="Filter by file state"
              >
                <option value="">All states</option>
                <option value="new">New</option>
                <option value="converted">Converted</option>
                <option value="uploaded">Uploaded</option>
                <option value="parsed">Parsed</option>
                <option value="failed">Failed</option>
              </select>
            </label>
            <label>
              <span>Parser</span>
              <select
                value={filters.parser}
                onChange={(event) => update({ parser: event.target.value })}
                aria-label="Filter by parser"
              >
                <option value="">All parsers</option>
                <option value="glm-ocr">GLM OCR</option>
                <option value="marker">Marker</option>
                <option value="mineru">MinerU</option>
                <option value="passthrough">Passthrough</option>
              </select>
            </label>
            <label>
              <span>RAGFlow</span>
              <select
                value={filters.ragflow}
                onChange={(event) => update({ ragflow: event.target.value })}
                aria-label="Filter by RAGFlow state"
              >
                <option value="">All RAGFlow</option>
                <option value="not_uploaded">Not uploaded</option>
                <option value="not_started">Uploaded only</option>
                <option value="parsed">Parsed</option>
              </select>
            </label>
          </div>
        </div>
      </details>
    </div>
  );
}
