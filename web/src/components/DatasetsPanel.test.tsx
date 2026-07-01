import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { DatasetsPanel } from './DatasetsPanel';
import { App } from '../App';

function installLocalStorage(initialValues: Record<string, string> = {}) {
  const store = new Map<string, string>(Object.entries(initialValues));
  const localStorageMock = {
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
    removeItem(key: string) {
      store.delete(key);
    },
    clear() {
      store.clear();
    },
  };

  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: localStorageMock,
  });
}

describe('DatasetsPanel', () => {
  it('renders a master-detail datasets layout with selected detail', () => {
    const markup = renderToStaticMarkup(
      createElement(DatasetsPanel, {
        loading: false,
        error: '',
        remoteError: '',
        datasets: [
          {
            name: 'quant-research',
            exists: false,
            protected: true,
            coverage: {
              file_count: 4,
              indexed_documents: 2,
              parsed_documents: 2,
              stuck_documents: 1,
              failed_documents: 0,
              chunk_count: 16,
            },
            profiles: [],
            drift: [],
            remote: null,
          },
          {
            name: 'quant-books',
            exists: true,
            protected: false,
            coverage: {
              file_count: 2,
              indexed_documents: 2,
              parsed_documents: 1,
              stuck_documents: 0,
              failed_documents: 1,
              chunk_count: 11,
            },
            profiles: [
              {
                name: 'books-marker',
                parser_mode: 'marker',
                source_type: 'book',
                source_paths: ['/atlas/books'],
                file_count: 2,
              },
            ],
            drift: [
              { field: 'chunk_method', label: 'Chunk method', expected: 'naive', actual: 'qa' },
            ],
            remote: { id: 'books-id', document_count: 2 },
          },
        ],
      }),
    );

    expect(markup).toContain('class="datasets-layout"');
    expect(markup).toContain('class="datasets-selection-grid"');
    expect(markup).not.toContain('role="list"');
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain('aria-controls="dataset-detail-panel"');
    expect(markup).toContain('class="datasets-detail-grid"');
    expect(markup).toContain('id="dataset-detail-panel"');
    expect(markup).toContain('quant-research');
    expect(markup).toContain('quant-books');
    expect(markup).toContain('Missing in RAGFlow');
    expect(markup).toContain('Protected');
    expect(markup).toContain('No profiles target this dataset.');
    expect(markup).toContain('Matches configured defaults.');
    expect(markup).not.toContain('Chunk method');
    expect(markup).not.toContain('books-marker');
    expect(markup).not.toContain('class="inspector-panel"');
  });

  it('renders the app datasets path with one datasets header', () => {
    installLocalStorage({ 'rag-sync.active-tab': JSON.stringify('Datasets') });

    const markup = renderToStaticMarkup(createElement(App));

    expect(markup.match(/<h1/g)?.length).toBe(5);
    expect(markup).toContain('<section class="screen-panel" aria-hidden="false"><div class="datasets-screen">');
    expect(markup).toContain('id="datasets-screen-title"');
    expect(markup).toContain('class="datasets-screen"');
  });

  it('renders drift and profile coverage in the selected dataset detail', () => {
    const markup = renderToStaticMarkup(
      createElement(DatasetsPanel, {
        loading: false,
        error: '',
        remoteError: '',
        datasets: [
          {
            name: 'quant-books',
            exists: true,
            protected: false,
            coverage: {
              file_count: 2,
              indexed_documents: 2,
              parsed_documents: 1,
              stuck_documents: 0,
              failed_documents: 1,
              chunk_count: 11,
            },
            profiles: [
              {
                name: 'books-marker',
                parser_mode: 'marker',
                source_type: 'book',
                source_paths: ['/atlas/books'],
                file_count: 2,
              },
            ],
            drift: [
              { field: 'chunk_method', label: 'Chunk method', expected: 'naive', actual: 'qa' },
            ],
            remote: { id: 'books-id', document_count: 2 },
          },
        ],
      }),
    );

    expect(markup).toContain('Chunk method');
    expect(markup).toContain('<th scope="col">Field</th>');
    expect(markup).toContain('<th scope="row">Chunk method</th>');
    expect(markup).toContain('books-marker');
    expect(markup).toContain('2 files');
    expect(markup).toContain('1 failed');
  });
});
