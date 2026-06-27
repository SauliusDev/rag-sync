import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { ImportBatchPreviewResponse } from '../api';
import {
  buildImportBatchRequest,
  deriveBatchPreviewState,
  ImportBatchDialog,
} from './ImportBatchDialog';

const previewFixture: ImportBatchPreviewResponse = {
  batch_id: 'batch-1',
  profile: 'quant-books',
  parser: 'marker',
  parser_version: '1.10.2',
  files: [
    {
      source_relpath: 'books/ready.pdf',
      source_filename: 'ready.pdf',
      markdown_relpath: 'outputs/ready.md',
      status: 'ok',
      validation_status: 'match',
      local_source_sha256: 'abc',
      manifest_source_sha256: 'abc',
    },
    {
      source_relpath: 'books/mismatch.pdf',
      source_filename: 'mismatch.pdf',
      markdown_relpath: 'outputs/mismatch.md',
      status: 'ok',
      validation_status: 'hash_mismatch',
      local_source_sha256: 'def',
      manifest_source_sha256: 'abc',
    },
    {
      source_relpath: 'books/missing.pdf',
      source_filename: 'missing.pdf',
      markdown_relpath: 'outputs/missing.md',
      status: 'ok',
      validation_status: 'missing_source',
      local_source_sha256: null,
      manifest_source_sha256: 'zzz',
    },
  ],
  summary: {
    total: 3,
    importable: 1,
    match: 1,
    missing_source: 1,
    hash_mismatch: 1,
    missing_markdown: 0,
    failed_remote_conversion: 0,
  },
};

describe('ImportBatchDialog', () => {
  it('renders preview statuses and summary totals', () => {
    const markup = renderToStaticMarkup(
      createElement(ImportBatchDialog, {
        onClose: () => undefined,
        onImported: () => undefined,
        initialBatchDir: '/tmp/batch-1',
        initialPreview: previewFixture,
        initialSelectedRelpaths: ['books/ready.pdf', 'books/mismatch.pdf'],
      }),
    );

    expect(markup).toContain('Import batch');
    expect(markup).toContain('3 files');
    expect(markup).toContain('1 ready');
    expect(markup).toContain('Match');
    expect(markup).toContain('Hash Mismatch');
    expect(markup).toContain('Missing Source');
    expect(markup).toContain('outputs/mismatch.md');
  });

  it('blocks force import without a reason before building a request', () => {
    expect(
      buildImportBatchRequest({
        batchDir: '/tmp/batch-1',
        preview: previewFixture,
        previewBatchDir: '/tmp/batch-1',
        selectedRelpaths: ['books/mismatch.pdf'],
        force: true,
        reason: '   ',
      }),
    ).toEqual({
      error: 'Force import requires a reason',
    });
  });

  it('shapes force import payloads to selected hash mismatches only', () => {
    expect(
      buildImportBatchRequest({
        batchDir: '/tmp/batch-1',
        preview: previewFixture,
        previewBatchDir: '/tmp/batch-1',
        selectedRelpaths: ['books/ready.pdf', 'books/mismatch.pdf', 'books/missing.pdf'],
        force: true,
        reason: 'same source, local hash changed after rename',
      }),
    ).toEqual({
      request: {
        batch_dir: '/tmp/batch-1',
        force: true,
        reason: 'same source, local hash changed after rename',
        selected_relpaths: ['books/mismatch.pdf'],
      },
    });
  });

  it('invalidates stale preview state on batch dir change and failed re-preview', () => {
    expect(
      deriveBatchPreviewState({
        batchDir: '/tmp/batch-2',
        preview: previewFixture,
        previewBatchDir: '/tmp/batch-1',
        selectedRelpaths: ['books/ready.pdf', 'books/mismatch.pdf'],
        forceReason: 'previous override',
      }),
    ).toEqual({
      preview: null,
      previewBatchDir: '',
      selectedRelpaths: [],
      forceReason: '',
      canImport: false,
    });

    expect(
      deriveBatchPreviewState({
        batchDir: '/tmp/batch-1',
        preview: null,
        previewBatchDir: '',
        selectedRelpaths: [],
        forceReason: '',
      }),
    ).toEqual({
      preview: null,
      previewBatchDir: '',
      selectedRelpaths: [],
      forceReason: '',
      canImport: false,
    });
  });
});
