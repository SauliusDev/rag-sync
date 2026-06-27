import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import {
  INITIAL_VISIBLE_ROWS,
  FileWorkbench,
  buildConversionStage,
  buildRagflowStage,
  buildSelectionSummary,
  growVisibleCount,
  isNearListEnd,
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
    expect(markup).toContain('Batch directory');
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
});
