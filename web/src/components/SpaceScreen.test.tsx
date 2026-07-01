import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { SpaceResponse } from '../api';
import { SpaceScreen } from './SpaceScreen';

const space: SpaceResponse = {
  summary: { datasets: 1, documents: 1, chunks: 1 },
  datasets: [{ id: 'dataset-1', name: 'quant-books', chunk_count: 1 }],
  documents: [
    {
      id: 'document-1',
      dataset_id: 'dataset-1',
      dataset_name: 'quant-books',
      name: 'Book.md',
      source_path: '/books/Book.md',
      chunk_count: 1,
    },
  ],
  chunks: [
    {
      id: 'chunk-1',
      document_id: 'document-1',
      dataset_id: 'dataset-1',
      dataset_name: 'quant-books',
      document_name: 'Book.md',
      source_path: '/books/Book.md',
      content_preview: 'Volatility clustering.',
      keywords: ['volatility'],
      position: { x: 0.1, y: 0.2, z: 0.3 },
    },
  ],
  errors: [],
};

describe('SpaceScreen', () => {
  it('renders a top-right orientation compass for the 3d space', () => {
    const markup = renderToStaticMarkup(<SpaceScreen space={space} loading={false} error="" />);

    expect(markup).toContain('aria-label="3D space orientation"');
    expect(markup).toContain('class="space-orientation"');
    expect(markup).toContain('data-axis="x"');
    expect(markup).toContain('data-axis="y"');
    expect(markup).toContain('data-axis="z"');
  });

  it('dedupes repeated fetch issues without a blank label prefix', () => {
    const markup = renderToStaticMarkup(
      <SpaceScreen
        space={{
          ...space,
          errors: [
            { document_id: '', document_name: '', message: 'RuntimeError: RAGFLOW_API_KEY not found' },
            { document_id: '', document_name: '', message: 'RuntimeError: RAGFLOW_API_KEY not found' },
          ],
        }}
        loading={false}
        error=""
      />,
    );

    expect(markup.match(/RuntimeError: RAGFLOW_API_KEY not found/g)).toHaveLength(1);
    expect(markup).not.toContain(': RuntimeError');
  });
});
