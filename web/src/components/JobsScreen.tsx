import { JobsPanel } from './JobsPanel';
import { ScreenHeader } from './ui/ScreenHeader';

export function JobsScreen() {
  return (
    <div className="jobs-screen">
      <ScreenHeader
        id="jobs-screen-title"
        title="Jobs"
        subtitle="Monitor queue activity, control workers, and inspect timing without losing the job list."
      />
      <section className="screen-content" aria-labelledby="jobs-screen-title">
        <JobsPanel />
      </section>
    </div>
  );
}
