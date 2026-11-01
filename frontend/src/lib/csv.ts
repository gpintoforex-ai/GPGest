/**
 * Descarrega uma folha CSV para o Excel em português: separador «;», BOM UTF-8 e proteção contra
 * fórmulas (células começadas por =, +, -, @ são prefixadas com apóstrofo).
 */
export function downloadCsv(filename: string, rows: readonly (readonly unknown[])[]) {
  const escape = (value: unknown) =>
    `"${String(value ?? "")
      .replace(/^[=+@\-\t\r]/, "'$&")
      .replaceAll('"', '""')}"`;
  const csv = rows.map((row) => row.map(escape).join(";")).join("\r\n");
  const url = URL.createObjectURL(new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
