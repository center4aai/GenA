
function serializeCell(value: unknown): string | number | boolean | null {
  if (value == null) return null;
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'number' || typeof value === 'boolean') {
    return value;
  }
  const text = String(value);
  if (/^[=+\-@\t\r]/.test(text)) {
    return `'${text}`;
  }
  return text;
}

/** Serialize rows to XLSX bytes and trigger a browser download.
 *
 * ExcelJS is imported on demand: it is the single largest dependency and only
 * matters when someone actually downloads a spreadsheet.
 */
export async function downloadXlsx(
  filename: string,
  rows: Record<string, unknown>[],
  sheetName = 'Sheet1',
): Promise<void> {
  const { default: ExcelJS } = await import('exceljs');
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet(sheetName);

  // Union of all keys: taking them from the first row dropped columns that row
  // happened not to have.
  const headers: string[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        headers.push(key);
      }
    }
  }
  sheet.columns = headers.map((h) => ({ header: h, key: h, width: Math.min(60, Math.max(12, h.length + 2)) }));

  for (const row of rows) {
    const serialized: Record<string, unknown> = {};
    for (const h of headers) serialized[h] = serializeCell(row[h]);
    sheet.addRow(serialized);
  }

  sheet.getRow(1).font = { bold: true };

  const buffer = await workbook.xlsx.writeBuffer();
  const blob = new Blob([buffer], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
