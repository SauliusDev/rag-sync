export { loadJson, saveJson } from './storage';

export type Profile = {
  name: string;
  parser_mode: string;
  target_dataset: string;
  source_paths: string[];
  source_type?: string;
  file_types?: string[];
  max_convert_workers?: number;
  max_upload_workers?: number;
  max_parse_workers?: number;
};

export type SourceFile = {
  id: number;
  profile_name: string;
  source_path: string;
  source_type: string;
  extension: string;
  state: string;
  included: number;
  tags: string;
  note: string;
  updated_at: string;
  artifact?: {
    parser: string;
    output_path: string;
    quality_status: string;
    warnings_json: string;
  } | null;
  ragflow?: {
    dataset_name: string;
    document_id: string;
    document_name: string;
    upload_status: string;
    parse_status: string;
    chunk_count?: number | null;
    token_count?: number | null;
  } | null;
  job?: {
    kind: string;
    status: string;
    progress: number;
    error_summary: string;
  } | null;
};

export type QueueStatus = {
  label: string;
  queue: {
    queued?: number;
    running?: number;
    failed?: number;
    completed?: number;
    paused?: boolean;
  };
  active?: JobRecord | null;
  system?: Record<string, SystemMetric>;
};

export type JobStage = {
  key: string;
  label: string;
  status: string;
  progress: number;
};

export type JobRecord = {
  id: number;
  kind: string;
  status: string;
  profile_name?: string | null;
  source_file_id?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  progress: number;
  error_summary: string;
  source_path?: string;
  file_name?: string;
  source_type?: string;
  queue_position?: number;
  stage?: JobStage;
};

export type SystemMetric = {
  label: string;
  value?: number | null;
  detail?: string;
};

export type RetrievalQuery = {
  id: string;
  question: string;
};

export type AppSettings = {
  profile_path: string;
  ragflow_base_url: string;
  protected_datasets: string[];
  dataset_defaults: Record<
    string,
    {
      description?: string;
      chunk_method?: string;
      parser_config?: {
        auto_keywords?: number;
        auto_questions?: number;
        chunk_token_num?: number;
        ext?: {
          toc_extraction?: boolean;
        };
        parent_child?: {
          use_parent_child?: boolean;
        };
      };
    }
  >;
  profiles: Profile[];
};

export type ImportBatchValidationStatus =
  | 'match'
  | 'missing_source'
  | 'hash_mismatch'
  | 'missing_markdown'
  | 'failed_remote_conversion';

export type ImportBatchPreviewFile = {
  source_relpath: string;
  source_filename: string;
  markdown_relpath: string;
  status: string;
  validation_status: ImportBatchValidationStatus;
  local_source_sha256: string | null;
  manifest_source_sha256: string;
};

export type ImportBatchPreviewResponse = {
  batch_id: string;
  profile: string;
  parser: string;
  parser_version: string;
  files: ImportBatchPreviewFile[];
  summary: {
    total: number;
    importable: number;
    match: number;
    missing_source: number;
    hash_mismatch: number;
    missing_markdown: number;
    failed_remote_conversion: number;
  };
};

export type ImportBatchPreviewRequest = {
  batch_dir: string;
  selected_relpaths?: string[] | null;
};

export type ImportBatchRequest = {
  batch_dir: string;
  force?: boolean;
  reason?: string;
  selected_relpaths?: string[] | null;
};

export type ImportBatchResponse = {
  batch_id: string;
  files: number;
  imported: number;
};

async function readErrorDetail(response: Response, fallback: string) {
  try {
    const payload = (await response.json()) as { detail?: string };
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    // Ignore non-JSON error bodies.
  }
  return `${fallback}: ${response.status}`;
}

export async function fetchProfiles(): Promise<Profile[]> {
  const response = await fetch('/api/profiles');
  if (!response.ok) {
    throw new Error(`Failed to fetch profiles: ${response.status}`);
  }

  const data = (await response.json()) as { profiles?: Profile[] };
  return data.profiles ?? [];
}

export async function fetchFiles(): Promise<SourceFile[]> {
  const response = await fetch('/api/files');
  if (!response.ok) {
    throw new Error(`Failed to fetch files: ${response.status}`);
  }

  const data = (await response.json()) as { files?: SourceFile[] };
  return data.files ?? [];
}

export async function fetchSettings(): Promise<AppSettings> {
  const response = await fetch('/api/settings');
  if (!response.ok) {
    throw new Error(`Failed to fetch settings: ${response.status}`);
  }

  return (await response.json()) as AppSettings;
}

export async function fetchStatus(): Promise<QueueStatus> {
  const response = await fetch('/api/status');
  if (!response.ok) {
    throw new Error(`Failed to fetch status: ${response.status}`);
  }
  return (await response.json()) as QueueStatus;
}

export async function fetchJobs(): Promise<JobRecord[]> {
  const response = await fetch('/api/jobs');
  if (!response.ok) {
    throw new Error(`Failed to fetch jobs: ${response.status}`);
  }
  const data = (await response.json()) as { jobs?: JobRecord[] };
  return data.jobs ?? [];
}

export async function pauseQueue(): Promise<{ paused: boolean }> {
  const response = await fetch('/api/queue/pause', { method: 'POST' });
  if (!response.ok) {
    throw new Error(`Failed to pause queue: ${response.status}`);
  }
  return (await response.json()) as { paused: boolean };
}

export async function resumeQueue(): Promise<{ paused: boolean }> {
  const response = await fetch('/api/queue/resume', { method: 'POST' });
  if (!response.ok) {
    throw new Error(`Failed to resume queue: ${response.status}`);
  }
  return (await response.json()) as { paused: boolean };
}

export async function killQueue(): Promise<{
  paused: boolean;
  canceled_running_job: boolean;
  terminated_processes: number;
}> {
  const response = await fetch('/api/queue/kill', { method: 'POST' });
  if (!response.ok) {
    throw new Error(`Failed to kill queue: ${response.status}`);
  }
  return (await response.json()) as {
    paused: boolean;
    canceled_running_job: boolean;
    terminated_processes: number;
  };
}

export async function scanProfile(profileName: string): Promise<void> {
  const response = await fetch(`/api/scan/${encodeURIComponent(profileName)}`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Failed to scan ${profileName}: ${response.status}`);
  }
}

export async function convertFile(
  sourceFileId: number,
  parser?: string,
): Promise<{ output_path: string }> {
  const response = await fetch(`/api/files/${sourceFileId}/convert`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ parser }),
  });
  if (!response.ok) {
    throw new Error(`Failed to convert file ${sourceFileId}: ${response.status}`);
  }

  return (await response.json()) as { output_path: string };
}

export async function uploadFile(sourceFileId: number): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/files/${sourceFileId}/upload`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Failed to upload file ${sourceFileId}: ${response.status}`);
  }

  return (await response.json()) as Record<string, unknown>;
}

export async function parseFile(sourceFileId: number): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/files/${sourceFileId}/parse`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Failed to parse file ${sourceFileId}: ${response.status}`);
  }

  return (await response.json()) as Record<string, unknown>;
}

export type EnqueueJobRequest = {
  kind:
    | 'scan'
    | 'convert'
    | 'upload'
    | 'parse'
    | 'stop_ragflow'
    | 'delete_ragflow'
    | 'restart_ragflow'
    | 'sync_file'
    | 'sync_filtered';
  source_file_id?: number | null;
  profile_name?: string;
};

export type BulkEnqueueJobRequest = {
  kind: 'sync_file' | 'sync_filtered' | 'restart_ragflow' | 'delete_ragflow' | 'stop_ragflow';
  source_file_ids?: number[];
  filters?: {
    query?: string;
    profile?: string;
    sourceType?: string;
    state?: string;
    parser?: string;
    ragflow?: string;
  };
};

export async function enqueueJob(request: EnqueueJobRequest): Promise<{ job_id: number }> {
  const response = await fetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(`Failed to enqueue job: ${response.status}`);
  }
  return (await response.json()) as { job_id: number };
}

export async function bulkEnqueueJobs(
  request: BulkEnqueueJobRequest,
): Promise<{ count: number; job_ids: number[]; source_file_ids?: number[] }> {
  const response = await fetch('/api/jobs/bulk', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(`Failed to bulk enqueue jobs: ${response.status}`);
  }
  return (await response.json()) as { count: number; job_ids: number[]; source_file_ids?: number[] };
}

export async function fetchQuerySet(name: string): Promise<RetrievalQuery[]> {
  const response = await fetch(`/api/retrieval/query-sets/${encodeURIComponent(name)}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch query set ${name}: ${response.status}`);
  }

  const data = (await response.json()) as { queries?: RetrievalQuery[] };
  return data.queries ?? [];
}

export async function previewImportBatch(
  request: ImportBatchPreviewRequest,
): Promise<ImportBatchPreviewResponse> {
  const response = await fetch('/api/import-batches/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, 'Failed to preview import batch'));
  }
  return (await response.json()) as ImportBatchPreviewResponse;
}

export async function importBatch(request: ImportBatchRequest): Promise<ImportBatchResponse> {
  const response = await fetch('/api/import-batches/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response, 'Failed to import batch'));
  }
  return (await response.json()) as ImportBatchResponse;
}
