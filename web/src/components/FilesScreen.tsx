import type { Profile } from '../api';
import { FileWorkbench } from './FileWorkbench';
import { ScreenHeader } from './ui/ScreenHeader';

type FilesScreenProps = {
  profiles: Profile[];
  profilesError: string;
  profilesLoading: boolean;
};

export function FilesScreen({
  profiles,
  profilesError,
  profilesLoading,
}: FilesScreenProps) {
  return (
    <div className="files-screen">
      <ScreenHeader
        id="files-screen-title"
        title="Files"
        subtitle="Inspect source files, scan profiles, and send selected items back through sync."
      />
      <section className="screen-content" aria-labelledby="files-screen-title">
        <FileWorkbench
          profiles={profiles}
          profilesError={profilesError}
          profilesLoading={profilesLoading}
        />
      </section>
    </div>
  );
}
