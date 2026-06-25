export type Profile = {
  name: string;
  parser_mode: string;
  target_dataset: string;
  source_paths: string[];
};

export async function fetchProfiles(): Promise<Profile[]> {
  const response = await fetch('/api/profiles');
  if (!response.ok) {
    throw new Error(`Failed to fetch profiles: ${response.status}`);
  }

  const data = (await response.json()) as { profiles?: Profile[] };
  return data.profiles ?? [];
}
