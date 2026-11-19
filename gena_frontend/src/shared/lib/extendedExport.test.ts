import { describe, expect, it } from 'vitest';
import { EXTENDED_EXPORT_COLUMNS, buildExtendedExportRows, csvCell, csvHeaders } from './extendedExport';

describe('csvHeaders', () => {
  it('takes the union of keys, not just the first row', () => {
    const rows = [{ a: 1 }, { a: 2, b: 3 }, { c: 4 }];
    expect(csvHeaders(rows)).toEqual(['a', 'b', 'c']);
  });

  it('keeps first-seen order and does not duplicate', () => {
    expect(csvHeaders([{ z: 1, a: 2 }, { a: 3, z: 4 }])).toEqual(['z', 'a']);
  });

  it('is empty for no rows', () => {
    expect(csvHeaders([])).toEqual([]);
  });
});

describe('csvCell', () => {
  it('renders objects as JSON instead of [object Object]', () => {
    expect(csvCell({ option_1: 'Москва' })).toBe('{"option_1":"Москва"}');
    expect(csvCell(['a', 'b'])).toBe('["a","b"]');
  });

  it('neutralizes formulas the way the xlsx export does', () => {
    expect(csvCell('=SUM(A1:A2)')).toBe("'=SUM(A1:A2)");
    expect(csvCell('-1+1')).toBe("'-1+1");
    expect(csvCell('@cmd')).toBe("'@cmd");
    expect(csvCell('+7 495')).toBe("'+7 495");
  });

  it('leaves ordinary text and empty values alone', () => {
    expect(csvCell('Какие меры были приняты?')).toBe('Какие меры были приняты?');
    expect(csvCell(null)).toBe('');
    expect(csvCell(undefined)).toBe('');
    expect(csvCell(0)).toBe('0');
  });
});

describe('buildExtendedExportRows block metadata', () => {
  it('exports the chunker block type and page range next to chunk_id', () => {
    const cols = EXTENDED_EXPORT_COLUMNS as readonly string[];
    expect(cols.slice(0, 4)).toEqual(['chunk_id', 'block_type', 'page_start', 'page_end']);
    const rows = buildExtendedExportRows([], [
      {
        chunk_index: 7,
        chunk_text: 'Врезка про Госуслуги.',
        fragment_data: { block_type: 'inset', page_start: 85, page_end: 85 },
        gate_passed: true,
      },
      { chunk_index: 8, chunk_text: 'Старый чанк без метаданных.' },
    ]);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ chunk_id: 7, block_type: 'inset', page_start: 85, page_end: 85 });
    expect(rows[1]).toMatchObject({ chunk_id: 8, block_type: null, page_start: null, page_end: null });
  });
});

describe('validation model column', () => {
  it("uses the question's validator, else the set's", () => {
    const rows = buildExtendedExportRows(
      [
        { question_id: 'a', chunk_id: 1, validation_model_id: 'gemma' },
        { question_id: 'b', chunk_id: 1 },
      ],
      [],
      'qwen',
    );
    expect(rows.map((r) => r.validation_model_id)).toEqual(['gemma', 'qwen']);
    expect(Object.keys(rows[0])).toContain('validation_model_id');
  });
});
