import type { DatasetSummary } from '../api';
import { DatasetsPanel } from './DatasetsPanel';
import { ScreenHeader } from './ui/ScreenHeader';

type DatasetsScreenProps = {
  datasets: DatasetSummary[];
  loading: boolean;
  error: string;
  remoteError: string;
};

export function DatasetsScreen({
  datasets,
  loading,
  error,
  remoteError,
}: DatasetsScreenProps) {
  return (
    <div className="datasets-screen">
      <ScreenHeader
        id="datasets-screen-title"
        title="Datasets"
        subtitle="Scan dataset coverage, spot drift, and inspect profile mapping without expanding every dataset."
      />
      <section className="screen-content" aria-labelledby="datasets-screen-title">
        <DatasetsPanel
          datasets={datasets}
          loading={loading}
          error={error}
          remoteError={remoteError}
        />
      </section>
    </div>
  );
}
