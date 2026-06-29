import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import {
  INITIAL_VISIBLE_ROWS,
  FileWorkbench,
  StageCell,
  buildConversionStage,
  buildLibraryStatus,
  buildRagflowStage,
  buildSelectionSummary,
  growVisibleCount,
  isNearListEnd,
  resolveReloadSelection,
  resolveInspectorFile,
  shouldShowSelectionBar,
  toneForFileState,
} from './components/FileWorkbench';

describe('file workbench lazy rendering helpers', () => {
  it('starts with a fixed initial batch size', () => {
    expect(INITIAL_VISIBLE_ROWS).toBe(50);
  });

  it('exposes the import batch entrypoint and can render the dialog open', () => {
    const markup = renderToStaticMarkup(
      createElement(FileWorkbench, {
        profiles: [],
        profilesError: '',
        profilesLoading: false,
        initialImportBatchOpen: true,
      }),
    );

    expect(markup).toContain('Import batch');
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain('aria-modal="true"');
    expect(markup).toContain('aria-labelledby="import-batch-title"');
    expect(markup).toContain('tabindex="-1"');
    expect(markup).toContain('aria-label="Close import batch dialog"');
    expect(markup).toContain('Batch directory');
  });

  it('renders the files layout with grouped library controls and shell primitives', () => {
    const markup = renderToStaticMarkup(
      createElement(FileWorkbench, {
        profiles: [],
        profilesError: '',
        profilesLoading: false,
      }),
    );

    expect(markup).toContain('class="file-control-band"');
    expect(markup).toContain('aria-label="Library actions"');
    expect(markup).toContain('class="data-table-shell"');
    expect(markup).toContain('class="file-focus-bar"');
    expect(markup).toContain('class="file-filter-menu"');
    expect(markup).toContain('file-filter-summary');
    expect(markup).toContain('Filters');
    expect(markup).toContain('No extra filters');
    expect(markup).toContain('Reset</button>');
    expect(markup).toContain('aria-label="Filter by profile"');
    expect(markup).toContain('aria-label="Filter by source type"');
    expect(markup).toContain('aria-label="Filter by file state"');
    expect(markup).toContain('aria-label="Filter by parser"');
    expect(markup).toContain('aria-label="Filter by RAGFlow state"');
    expect(markup).not.toContain('file-filter-cluster-combined');
    expect(markup).not.toContain('file-filter-label">Source<');
    expect(markup).not.toContain('file-filter-label">Processing<');
    expect(markup).not.toContain('file-filter-label">RAGFlow<');
    expect(markup).not.toContain('class="file-selection-bar"');
    expect(markup).not.toContain('class="inspector-panel"');
    expect(markup).toContain('>Convert</button>');
    expect(markup).toContain('>Upload</button>');
    expect(markup).toContain('>Parse</button>');
    expect(markup).toContain('>Sync</button>');
    expect(markup).toContain('>Restart RAGFlow</button>');
    expect(markup).toContain('>Clear</button>');
  });

  it('grows visible rows in fixed batches without exceeding total rows', () => {
    expect(growVisibleCount(50, 200)).toBe(100);
    expect(growVisibleCount(175, 200)).toBe(200);
    expect(growVisibleCount(200, 200)).toBe(200);
  });

  it('loads more rows only when the list is near the bottom', () => {
    expect(isNearListEnd({ scrollTop: 100, clientHeight: 500, scrollHeight: 1000 })).toBe(false);
    expect(isNearListEnd({ scrollTop: 280, clientHeight: 500, scrollHeight: 1000 })).toBe(true);
  });

  it('does not restore a stale remembered inspector selection when nothing is actively selected', () => {
    const nextFiles = [
      {
        id: 9,
        profile_name: 'quant-articles',
        source_path: '/articles/a.md',
        source_type: 'article',
        extension: 'md',
        state: 'parsed',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
      },
    ];

    expect(resolveReloadSelection(nextFiles, null)).toBeNull();
  });

  it('shows the standalone selection bar only for bulk selections', () => {
    expect(shouldShowSelectionBar(0)).toBe(false);
    expect(shouldShowSelectionBar(1)).toBe(false);
    expect(shouldShowSelectionBar(2)).toBe(true);
  });

  it('builds a bulk selection summary for multiple files', () => {
    const summary = buildSelectionSummary([
      {
        id: 1,
        profile_name: 'quant-books',
        source_path: '/books/A.pdf',
        source_type: 'book',
        extension: 'pdf',
        state: 'parsed',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
      },
      {
        id: 2,
        profile_name: 'quant-books',
        source_path: '/books/B.pdf',
        source_type: 'book',
        extension: 'pdf',
        state: 'converted',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
      },
      {
        id: 3,
        profile_name: 'quant-articles',
        source_path: '/articles/C.md',
        source_type: 'article',
        extension: 'md',
        state: 'unchanged',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
      },
    ]);

    expect(summary.title).toBe('3 files selected');
    expect(summary.selectionLabel).toBe('Bulk selection active');
    expect(summary.profileSummary).toBe('quant-books (2), quant-articles (1)');
    expect(summary.typeSummary).toBe('book (2), article (1)');
    expect(summary.stateSummary).toBe('converted (1), parsed (1), unchanged (1)');
  });

  it('derives a running conversion stage from an active sync job', () => {
    expect(
      buildConversionStage({
        id: 1,
        profile_name: 'quant-books',
        source_path: '/books/A.pdf',
        source_type: 'book',
        extension: 'pdf',
        state: 'new',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
        job: { kind: 'sync_file', status: 'running', progress: 0.35, error_summary: '' },
      }),
    ).toMatchObject({
      label: 'Marker running',
      tone: 'running',
      progress: 35,
    });
  });

  it('does not invent conversion progress when the backend has not reported one', () => {
    expect(
      buildConversionStage({
        id: 1,
        profile_name: 'quant-books',
        source_path: '/books/A.pdf',
        source_type: 'book',
        extension: 'pdf',
        state: 'new',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
        job: { kind: 'sync_file', status: 'running', progress: undefined as never, error_summary: '' },
      }),
    ).toMatchObject({
      label: 'Marker running',
      detail: 'Converting',
      progress: null,
      tone: 'running',
    });
  });

  it('keeps failed and missing fallback tones truthful when no stage-specific status exists', () => {
    expect(toneForFileState('failed')).toBe('failed');
    expect(toneForFileState('missing')).toBe('missing');
    expect(toneForFileState('changed')).toBe('changed');

    expect(
      buildConversionStage({
        id: 1,
        profile_name: 'quant-books',
        source_path: '/books/A.pdf',
        source_type: 'book',
        extension: 'pdf',
        state: 'failed',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
      }),
    ).toMatchObject({
      label: 'failed',
      tone: 'failed',
    });
  });

  it('derives parsed ragflow stage from an indexed document', () => {
    expect(
      buildRagflowStage({
        id: 1,
        profile_name: 'quant-books',
        source_path: '/books/A.pdf',
        source_type: 'book',
        extension: 'pdf',
        state: 'parsed',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
        ragflow: {
          dataset_name: 'quant-books',
          document_id: 'doc-1',
          document_name: 'A.md',
          upload_status: 'uploaded',
          parse_status: 'parsed',
          chunk_count: 42,
          token_count: 1000,
        },
      }),
    ).toMatchObject({
      label: 'Parsed',
      progress: 100,
      detail: '42 chunks',
    });
  });

  it('does not invent progress for uploaded files waiting to parse', () => {
    expect(
      buildRagflowStage({
        id: 1,
        profile_name: 'quant-papers',
        source_path: '/papers/A.pdf',
        source_type: 'paper',
        extension: 'pdf',
        state: 'uploaded',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
        ragflow: {
          dataset_name: 'quant-papers',
          document_id: 'doc-1',
          document_name: 'A.md',
          upload_status: 'uploaded',
          parse_status: 'not_started',
          chunk_count: null,
          token_count: null,
        },
      }),
    ).toMatchObject({
      label: 'Uploaded',
      detail: 'Ready to parse',
      progress: 0,
      tone: 'queued',
    });
  });

  it('does not invent ragflow progress when upload or parse is running without backend progress', () => {
    expect(
      buildRagflowStage({
        id: 1,
        profile_name: 'quant-papers',
        source_path: '/papers/A.pdf',
        source_type: 'paper',
        extension: 'pdf',
        state: 'uploaded',
        included: 1,
        tags: '',
        note: '',
        updated_at: '2026-06-26 00:00:00',
        job: { kind: 'upload', status: 'running', progress: undefined as never, error_summary: '' },
      }),
    ).toMatchObject({
      label: 'Uploading',
      detail: 'Sending markdown',
      progress: null,
      tone: 'running',
    });
  });

  it('renders indeterminate stages without fake percent text or fake progress width', () => {
    const markup = renderToStaticMarkup(
      createElement(StageCell, {
        stage: {
          label: 'Parsing',
          detail: 'RAGFlow ingest',
          progress: null,
          tone: 'running',
        },
      }),
    );

    expect(markup).toContain('class="mini-progress is-indeterminate"');
    expect(markup).not.toContain('stage-percent');
    expect(markup).not.toContain('width:');
  });

  it('resolves the inspector to the single checkbox-selected file', () => {
    const oldClickedFile = {
      id: 1,
      profile_name: 'quant-books',
      source_path: '/books/A.pdf',
      source_type: 'book',
      extension: 'pdf',
      state: 'parsed',
      included: 1,
      tags: '',
      note: '',
      updated_at: '2026-06-26 00:00:00',
    };
    const selectedFile = {
      id: 2,
      profile_name: 'quant-books',
      source_path: '/books/B.pdf',
      source_type: 'book',
      extension: 'pdf',
      state: 'converted',
      included: 1,
      tags: '',
      note: '',
      updated_at: '2026-06-26 00:00:00',
    };

    expect(resolveInspectorFile(oldClickedFile, [selectedFile])).toBe(selectedFile);
    expect(resolveInspectorFile(oldClickedFile, [oldClickedFile, selectedFile])).toBe(oldClickedFile);
  });

  it('keeps user-initiated library activity labels ahead of the follow-up reload label', () => {
    expect(
      buildLibraryStatus({
        loading: true,
        working: true,
        bulkQueueAction: '',
        filteredCount: 12,
      }),
    ).toBe('Scanning configured profiles');

    expect(
      buildLibraryStatus({
        loading: true,
        working: false,
        bulkQueueAction: 'sync_filtered',
        filteredCount: 12,
      }),
    ).toBe('Queueing filtered files');
  });
});
