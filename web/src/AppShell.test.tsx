import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { App, SettingsPanel } from './App';

function installLocalStorage() {
  const store = new Map<string, string>();
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

describe('App shell', () => {
  it('renders a screen-owned frame instead of the legacy bordered panel shell', () => {
    installLocalStorage();

    const markup = renderToStaticMarkup(<App />);

    expect(markup).toContain('href="#main-content"');
    expect(markup).toContain('id="main-content"');
    expect(markup).toContain('class="screen-frame"');
    expect(markup).toContain('class="screen-header"');
    expect(markup).toContain('class="screen-content"');
    expect(markup).not.toContain('class="panel"');
    expect(markup).toContain('Files</h1>');
    expect(markup).toContain('>Files</button>');
    expect(markup).toContain('>Jobs</button>');
    expect(markup).toContain('>Datasets</button>');
    expect(markup).toContain('>Settings</button>');
  });

  it('renders settings with parser defaults surfaced, including glm ocr for pdf profiles', () => {
    const markup = renderToStaticMarkup(
      <SettingsPanel
        error=""
        settings={{
          profile_path: '/srv/rag-sync/config/profiles.toml',
          ragflow_base_url: 'http://127.0.0.1:9380',
          protected_datasets: ['quant-books'],
          dataset_defaults: {
            'quant-books': {
              chunk_method: 'naive',
              parser_config: { chunk_token_num: 1000 },
            },
          },
          profiles: [
            {
              name: 'quant-books',
              parser_mode: 'glm-ocr',
              target_dataset: 'quant-books',
              source_paths: ['/books'],
              source_type: 'book',
              file_types: ['pdf'],
            },
            {
              name: 'quant-articles',
              parser_mode: 'passthrough',
              target_dataset: 'quant-articles',
              source_paths: ['/articles'],
              source_type: 'article',
              file_types: ['md'],
            },
          ],
          usage: {
            total_tokens: 100,
            total_cost_usd: 1.25,
            providers: {},
            items: [],
          },
        }}
      />,
    );

    expect(markup).toContain('Parser defaults');
    expect(markup).toContain('aria-label="Dataset default parser settings"');
    expect(markup).toContain('<th scope="col">Dataset</th>');
    expect(markup).toContain('<th scope="row">quant-books</th>');
    expect(markup).toContain('PDF profiles default to GLM OCR');
    expect(markup).toContain('quant-books');
    expect(markup).toContain('GLM OCR');
    expect(markup).toContain('quant-articles');
    expect(markup).toContain('Passthrough');
  });
});
