import { Search } from 'lucide-react';

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

export function FileFilters({ filters, profiles, onChange }: FileFiltersProps) {
  function update(patch: Partial<FileFilterState>) {
    onChange({ ...filters, ...patch });
  }

  return (
    <div className="file-filters">
      <label className="search-field">
        <Search size={16} aria-hidden="true" />
        <input
          value={filters.query}
          onChange={(event) => update({ query: event.target.value })}
          placeholder="Search files"
          aria-label="Search source files"
        />
      </label>
      <select value={filters.profile} onChange={(event) => update({ profile: event.target.value })}>
        <option value="">All profiles</option>
        {profiles.map((profile) => (
          <option key={profile.name} value={profile.name}>
            {profile.name}
          </option>
        ))}
      </select>
      <select
        value={filters.sourceType}
        onChange={(event) => update({ sourceType: event.target.value })}
      >
        <option value="">All types</option>
        <option value="book">Books</option>
        <option value="paper">Papers</option>
        <option value="article">Articles</option>
        <option value="video">Videos</option>
      </select>
      <select value={filters.state} onChange={(event) => update({ state: event.target.value })}>
        <option value="">All states</option>
        <option value="new">New</option>
        <option value="converted">Converted</option>
        <option value="uploaded">Uploaded</option>
        <option value="parsed">Parsed</option>
        <option value="failed">Failed</option>
      </select>
      <select value={filters.parser} onChange={(event) => update({ parser: event.target.value })}>
        <option value="">All parsers</option>
        <option value="marker">Marker</option>
        <option value="mineru">MinerU</option>
        <option value="passthrough">Passthrough</option>
      </select>
      <select value={filters.ragflow} onChange={(event) => update({ ragflow: event.target.value })}>
        <option value="">All RAGFlow</option>
        <option value="not_uploaded">Not uploaded</option>
        <option value="not_started">Uploaded only</option>
        <option value="parsed">Parsed</option>
      </select>
    </div>
  );
}
