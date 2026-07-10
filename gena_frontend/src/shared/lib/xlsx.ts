import ExcelJS from 'exceljs';

function serializeCell(value: unknown): string | number | boolean | null {
  if (value == null) return null;
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'string') {
    return value;
  }
  return String(value);
}

/** Serialize rows to XLSX bytes and trigger a browser download. */
export async function downloadXlsx(
  filename: string,
  rows: Record<string, unknown>[],
  sheetName = 'Sheet1',
): Promise<void> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet(sheetName);

  const headers = rows.length ? Object.keys(rows[0]) : [];
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
