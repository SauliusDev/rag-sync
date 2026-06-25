import { useEffect, useState } from 'react';
import { fetchProfiles, fetchSettings, type AppSettings, type Profile } from './api';
import { StatusBadge } from './components/StatusBadge';
import { FileWorkbench } from './components/FileWorkbench';
import { loadJson, saveJson } from './storage';
import { RetrievalPanel } from './components/RetrievalPanel';
import { ThemeButton } from './theme';

const tabs = ['Files', 'Profiles', 'Jobs', 'Datasets', 'Retrieval Tests', 'Settings'] as const;
type Tab = (typeof tabs)[number];

export function App() {
  const [active, setActive] = useState<Tab>(() => loadJson<Tab>('rag-sync.active-tab', 'Files'));
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profilesError, setProfilesError] = useState('');
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [settingsError, setSettingsError] = useState('');

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
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">RAG Sync</div>
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

      <main className="main">
        <section className="panel" aria-labelledby="panel-title">
          <div className="panel-header">
            <h1 id="panel-title">{active}</h1>
          </div>
          {active === 'Files' ? (
            <FileWorkbench
              profiles={profiles}
              profilesError={profilesError}
              profilesLoading={profilesLoading}
            />
          ) : active === 'Retrieval Tests' ? (
            <RetrievalPanel />
          ) : active === 'Profiles' ? (
            <ProfilesPanel profiles={profiles} error={profilesError} />
          ) : active === 'Settings' ? (
            <SettingsPanel settings={settings} error={settingsError} />
          ) : (
            <p className="muted">Workbench content will be connected in the next task.</p>
          )}
        </section>
      </main>
    </div>
  );
}

function SettingsPanel({ settings, error }: { settings: AppSettings | null; error: string }) {
  if (error) {
    return <p className="muted">{error}</p>;
  }

  if (!settings) {
    return <p className="muted">Loading settings.</p>;
  }

  return (
    <div className="settings-view">
      <section className="settings-section" aria-labelledby="runtime-settings-title">
        <h2 id="runtime-settings-title">Runtime</h2>
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
      </section>

      <section className="settings-section" aria-labelledby="dataset-settings-title">
        <h2 id="dataset-settings-title">Dataset Defaults</h2>
        <div className="settings-table-wrap">
          <table className="settings-table">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Method</th>
                <th>Tokens</th>
                <th>Keywords</th>
                <th>Questions</th>
                <th>TOC</th>
                <th>Parent-child</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(settings.dataset_defaults).map(([name, defaults]) => {
                const parserConfig = defaults.parser_config ?? {};
                return (
                  <tr key={name}>
                    <td>{name}</td>
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
      </section>
    </div>
  );
}

function ProfilesPanel({ profiles, error }: { profiles: Profile[]; error: string }) {
  if (error) {
    return <p className="muted">{error}</p>;
  }

  if (profiles.length === 0) {
    return <p className="muted">No profiles found.</p>;
  }

  return (
    <div className="profile-grid">
      {profiles.map((profile) => (
        <article className="profile-card" key={profile.name}>
          <div>
            <h2>{profile.name}</h2>
            <p>{profile.parser_mode}</p>
          </div>
          <dl>
            <div>
              <dt>Dataset</dt>
              <dd>{profile.target_dataset}</dd>
            </div>
            <div>
              <dt>Sources</dt>
              <dd>{profile.source_paths.join(', ')}</dd>
            </div>
          </dl>
        </article>
      ))}
    </div>
  );
}
