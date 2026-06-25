export type Profile = {
  name: string;
  parser_mode: string;
  target_dataset: string;
  source_paths: string[];
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
};

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

export async function scanProfile(profileName: string): Promise<void> {
  const response = await fetch(`/api/scan/${encodeURIComponent(profileName)}`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Failed to scan ${profileName}: ${response.status}`);
  }
}
