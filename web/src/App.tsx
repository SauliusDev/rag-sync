import { useEffect, useState } from 'react';
import {
  fetchDatasets,
  fetchProfiles,
  fetchSettings,
  type AppSettings,
  type DatasetSummary,
  type Profile,
} from './api';
import { JobsScreen } from './components/JobsScreen';
import { formatCostUsd } from './components/JobsPanel';
import { StatusBadge } from './components/StatusBadge';
import { FilesScreen } from './components/FilesScreen';
import { DatasetsScreen } from './components/DatasetsScreen';
import { DataTableShell } from './components/ui/DataTableShell';
import { ScreenHeader } from './components/ui/ScreenHeader';
import { SectionBlock } from './components/ui/SectionBlock';
import { loadJson, saveJson } from './storage';
import { ThemeButton } from './theme';

const tabs = ['Files', 'Jobs', 'Datasets', 'Settings'] as const;
type Tab = (typeof tabs)[number];

function initialTab(): Tab {
  const stored = loadJson<string>('rag-sync.active-tab', 'Files');
  if (stored === 'Profiles' || stored === 'Retrieval Tests') {
    return 'Datasets';
  }
  return tabs.includes(stored as Tab) ? (stored as Tab) : 'Files';
}

export function App() {
  const [active, setActive] = useState<Tab>(initialTab);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profilesError, setProfilesError] = useState('');
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(true);
  const [datasetsError, setDatasetsError] = useState('');
  const [datasetsRemoteError, setDatasetsRemoteError] = useState('');

  function selectTab(tab: Tab) {
    setActive(tab);
    saveJson('rag-sync.active-tab', tab);
  }

  useEffect(() => {
    setProfilesLoading(true);
    fetchProfiles()
      .then((nextProfiles) => {
        setProfiles(nextProfiles);
        setProfilesError('');
      })
      .catch((error: unknown) => {
        setProfiles([]);
        setProfilesError(error instanceof Error ? error.message : 'Failed to fetch profiles');
      })
      .finally(() => setProfilesLoading(false));

    fetchSettings()
      .then((nextSettings) => {
        setSettings(nextSettings);
        setSettingsError('');
      })
      .catch((error: unknown) => {
        setSettings(null);
        setSettingsError(error instanceof Error ? error.message : 'Failed to fetch settings');
      });

    setDatasetsLoading(true);
    fetchDatasets()
      .then((payload) => {
        setDatasets(payload.datasets);
        setDatasetsRemoteError(payload.remote_error ?? '');
        setDatasetsError('');
      })
      .catch((error: unknown) => {
        setDatasets([]);
        setDatasetsRemoteError('');
        setDatasetsError(error instanceof Error ? error.message : 'Failed to fetch datasets');
      })
      .finally(() => setDatasetsLoading(false));
  }, []);

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="topbar">
        <div className="brand">
          <img src="/logo.png" alt="" className="brand-icon" aria-hidden="true" />
          RAG Sync
        </div>
        <nav className="tabs" aria-label="Workbench">
          {tabs.map((item) => (
            <button
              key={item}
              className={item === active ? 'tab active' : 'tab'}
              type="button"
              aria-current={item === active ? 'page' : undefined}
              onClick={() => selectTab(item)}
            >
              {item}
            </button>
          ))}
        </nav>
        <StatusBadge />
        <ThemeButton />
      </header>

      <main className="main" id="main-content">
        <div className="screen-frame">
          <section className="screen-panel" hidden={active !== 'Files'} aria-hidden={active !== 'Files'}>
            <FilesScreen
              profiles={profiles}
              profilesError={profilesError}
              profilesLoading={profilesLoading}
            />
          </section>
          <section className="screen-panel" hidden={active !== 'Jobs'} aria-hidden={active !== 'Jobs'}>
            <JobsScreen />
          </section>
          <section className="screen-panel" hidden={active !== 'Datasets'} aria-hidden={active !== 'Datasets'}>
            <DatasetsScreen
              datasets={datasets}
              loading={datasetsLoading}
              error={datasetsError}
              remoteError={datasetsRemoteError}
            />
          </section>
          <section className="screen-panel" hidden={active !== 'Settings'} aria-hidden={active !== 'Settings'}>
            <>
              <ScreenHeader id="screen-title" title={active} />
              <section className="screen-content" aria-labelledby="screen-title">
                <SettingsPanel settings={settings} error={settingsError} />
              </section>
            </>
          </section>
        </div>
      </main>
    </div>
  );
}

function parserModeLabel(value: string) {
  if (value === 'glm-ocr') {
    return 'GLM OCR';
  }
  if (value === 'passthrough') {
    return 'Passthrough';
  }
  return value.replace(/-/g, ' ');
}

function parserDefaultsSummary(profiles: AppSettings['profiles']) {
  const pdfProfiles = profiles.filter((profile) => profile.file_types?.includes('pdf'));
  const glmPdfProfiles = pdfProfiles.filter((profile) => profile.parser_mode === 'glm-ocr');
  const markdownProfiles = profiles.filter(
    (profile) => profile.file_types?.includes('md') && profile.parser_mode === 'passthrough',
  );

  if (pdfProfiles.length > 0 && glmPdfProfiles.length === pdfProfiles.length) {
    if (markdownProfiles.length > 0) {
      return 'PDF profiles default to GLM OCR. Markdown profiles stay passthrough.';
    }
    return 'All configured PDF profiles default to GLM OCR.';
  }

  return 'Parser defaults are defined per profile so each source type keeps the right ingest path.';
}

export function SettingsPanel({ settings, error }: { settings: AppSettings | null; error: string }) {
  if (error) {
    return (
      <p className="muted" role="alert">
        {error}
      </p>
    );
  }

  if (!settings) {
    return (
      <p className="muted" role="status" aria-live="polite">
        Loading settings.
      </p>
    );
  }

  return (
    <div className="settings-view">
      <div className="settings-top-grid">
        <SectionBlock title="Runtime" id="runtime-settings-title">
          <dl className="settings-list">
            <div>
              <dt>Profile config</dt>
              <dd>{settings.profile_path}</dd>
            </div>
            <div>
              <dt>RAGFlow API</dt>
              <dd>{settings.ragflow_base_url}</dd>
            </div>
            <div>
              <dt>Protected datasets</dt>
              <dd>{settings.protected_datasets.join(', ') || 'none'}</dd>
            </div>
          </dl>
        </SectionBlock>

        <SectionBlock title="API usage" id="usage-settings-title">
          <dl className="settings-list">
            <div>
              <dt>Total spend</dt>
              <dd>{formatCostUsd(settings.usage?.total_cost_usd ?? 0)}</dd>
            </div>
            <div>
              <dt>Z API GLM OCR</dt>
              <dd>
                {formatCostUsd(settings.usage?.providers?.['z-ai']?.cost_usd ?? 0)}
                {' · '}
                {(settings.usage?.providers?.['z-ai']?.tokens ?? 0).toLocaleString()} tokens
              </dd>
            </div>
            <div>
              <dt>OpenRouter chunking</dt>
              <dd>
                {settings.usage?.providers?.openrouter?.tracked
                  ? `${formatCostUsd(settings.usage.providers.openrouter.cost_usd)} used · ${formatCostUsd(
                      settings.usage.providers.openrouter.remaining_credits,
                    )} left of ${formatCostUsd(settings.usage.providers.openrouter.total_credits)} credits`
                  : 'not tracked by rag-sync'}
              </dd>
            </div>
          </dl>
        </SectionBlock>
      </div>

      <SectionBlock
        title="Parser defaults"
        description={parserDefaultsSummary(settings.profiles)}
        id="parser-settings-title"
      >
        <div className="settings-profile-grid">
          {settings.profiles.map((profile) => (
            <article key={profile.name} className="settings-profile-card">
              <div className="settings-profile-topline">
                <strong>{profile.name}</strong>
                <span className="dataset-chip">{parserModeLabel(profile.parser_mode)}</span>
              </div>
              <div className="settings-profile-meta">
                <span>{profile.source_type ?? 'source'}</span>
                <span>{profile.file_types?.join(', ') || 'all files'}</span>
                <span>{profile.target_dataset}</span>
              </div>
            </article>
          ))}
        </div>
      </SectionBlock>

      <SectionBlock title="Dataset defaults" id="dataset-settings-title">
        <DataTableShell label="Dataset default parser settings">
          <div className="settings-table-wrap">
            <table className="settings-table">
              <thead>
                <tr>
                  <th scope="col">Dataset</th>
                  <th scope="col">Method</th>
                  <th scope="col">Tokens</th>
                  <th scope="col">Keywords</th>
                  <th scope="col">Questions</th>
                  <th scope="col">TOC</th>
                  <th scope="col">Parent-child</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(settings.dataset_defaults).map(([name, defaults]) => {
                  const parserConfig = defaults.parser_config ?? {};
                  return (
                    <tr key={name}>
                      <th scope="row">{name}</th>
                      <td>{defaults.chunk_method ?? 'naive'}</td>
                      <td>{parserConfig.chunk_token_num ?? '-'}</td>
                      <td>{parserConfig.auto_keywords ?? '-'}</td>
                      <td>{parserConfig.auto_questions ?? '-'}</td>
                      <td>{parserConfig.ext?.toc_extraction ? 'on' : 'off'}</td>
                      <td>{parserConfig.parent_child?.use_parent_child ? 'on' : 'off'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </DataTableShell>
      </SectionBlock>
    </div>
  );
}
