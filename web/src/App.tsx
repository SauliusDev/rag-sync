import { useEffect, useState } from 'react';
import { fetchProfiles, type Profile } from './api';
import { FileWorkbench } from './components/FileWorkbench';
import { ThemeButton } from './theme';

const tabs = ['Files', 'Profiles', 'Jobs', 'Datasets', 'Retrieval Tests', 'Settings'] as const;
type Tab = (typeof tabs)[number];

export function App() {
  const [active, setActive] = useState<Tab>('Files');
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profilesError, setProfilesError] = useState('');

  useEffect(() => {
    fetchProfiles()
      .then((nextProfiles) => {
        setProfiles(nextProfiles);
        setProfilesError('');
      })
      .catch((error: unknown) => {
        setProfiles([]);
        setProfilesError(error instanceof Error ? error.message : 'Failed to fetch profiles');
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
              onClick={() => setActive(item)}
            >
              {item}
            </button>
          ))}
        </nav>
        <ThemeButton />
      </header>

      <main className="main">
        <section className="panel" aria-labelledby="panel-title">
          <div className="panel-header">
            <h1 id="panel-title">{active}</h1>
          </div>
          {active === 'Files' ? (
            <FileWorkbench />
          ) : active === 'Profiles' ? (
            <ProfilesPanel profiles={profiles} error={profilesError} />
          ) : (
            <p className="muted">Workbench content will be connected in the next task.</p>
          )}
        </section>
      </main>
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
