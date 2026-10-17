import { ARCHIVE_REASONS, HEALTH_STATUS, DRONE_STATUS } from "../../labels";
import type { Drone } from "../../types";

// Отчёт о техническом состоянии дронов техника (PDF). Формирование документа — здесь,
// сама библиотека pdfmake загружается только при нажатии «Сформировать PDF».

export interface ReportDrone extends Drone {
  fleet_name: string;
  last_flight_at: string | null;
}

export interface ReportIdentity {
  first_name: string | null;
  last_name: string | null;
  display_name: string;
  group: string | null;
  subgroups: string[];
  position_title?: string | null;
}

export interface TechReport {
  items: ReportDrone[];
  technician: ReportIdentity;
  generated_at: string;
}

const pad = (n: number) => String(n).padStart(2, "0");
export const ruDate = (d: Date) => `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;

/** Часть имени файла: без пробелов и символов, недопустимых в именах файлов. */
const part = (s: string | null | undefined) => (s ?? "").trim().replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "").replace(/\s+/g, "-");

/** Имя файла: группа_подгруппа1_подгруппа2_Имя_Фамилия_дата.pdf (пустые части пропускаются). */
export function reportFileName(t: ReportIdentity, at: Date): string {
  const [first, last] = t.first_name || t.last_name ? [t.first_name, t.last_name]
    : [t.display_name.split(/\s+/)[1] ?? t.display_name, t.display_name.split(/\s+/)[0]];
  const parts = [t.group, ...t.subgroups, first, last, ruDate(at)].map(part).filter(Boolean);
  return `${parts.join("_")}.pdf`;
}

export function droneStatusLabel(d: Drone): string {
  if (d.archived) return "В архиве";
  if (d.status !== "active") return DRONE_STATUS[d.status]?.label ?? d.status;
  return HEALTH_STATUS[d.health_status]?.label ?? d.health_status;
}

export function archiveReasonText(d: Drone): string {
  if (!d.archived || !d.archive_reason) return "—";
  const r = ARCHIVE_REASONS[d.archive_reason] ?? d.archive_reason;
  return d.archive_comment ? `${r}: ${d.archive_comment}` : r;
}

/** Порядок строк: сначала работоспособные (исправные), затем остальные, архивные — в конце. */
export function reportOrder(items: ReportDrone[]): ReportDrone[] {
  const rank = (d: Drone) => (d.archived ? 3 : d.status === "active" && d.health_status === "serviceable" ? 0
    : d.health_status === "requires_check" ? 1 : 2);
  return [...items].sort((a, b) => rank(a) - rank(b) || a.registration_number.localeCompare(b.registration_number, "ru"));
}

export function reportDoc(r: TechReport, at: Date) {
  const t = r.technician;
  const rows = reportOrder(r.items).map((d) => [
    `${d.manufacturer} ${d.model}`, d.registration_number, d.serial_number,
    d.last_flight_at ? ruDate(new Date(d.last_flight_at)) : "—", droneStatusLabel(d), archiveReasonText(d),
  ]);
  const head = ["Модель", "Учётный номер", "Серийный номер", "Последний полёт", "Статус", "Причина архивации"]
    .map((text) => ({ text, bold: true, fillColor: "#e8edf3" }));
  return {
    pageOrientation: "landscape" as const,
    pageMargins: [32, 36, 32, 36] as [number, number, number, number],
    info: { title: "Отчёт о техническом состоянии БПЛА" },
    content: [
      { text: "Отчёт о техническом состоянии БПЛА", fontSize: 16, bold: true, margin: [0, 0, 0, 6] },
      { text: `Техник: ${[t.last_name, t.first_name].filter(Boolean).join(" ") || t.display_name}`
          + (t.position_title ? `, ${t.position_title}` : ""), margin: [0, 0, 0, 2] },
      { text: `Группа: ${[t.group, ...t.subgroups].filter(Boolean).join(" / ") || "—"}`, margin: [0, 0, 0, 2] },
      { text: `Дата: ${ruDate(at)}. Дронов: ${r.items.length}, из них в архиве: ${r.items.filter((d) => d.archived).length}.`,
        margin: [0, 0, 0, 10] },
      rows.length === 0 ? { text: "Дронов нет.", italics: true } : {
        table: { headerRows: 1, widths: ["*", "auto", "auto", "auto", "auto", "*"], body: [head, ...rows] },
        layout: "lightHorizontalLines",
      },
    ],
    defaultStyle: { fontSize: 10 },
  };
}

/** Формирует PDF и сохраняет его через браузер (в папку загрузок — ~/Загрузки). */
export async function downloadReport(r: TechReport): Promise<string> {
  const [{ default: pdfMake }, { default: vfs }] = await Promise.all([
    import("pdfmake/build/pdfmake"), import("pdfmake/build/vfs_fonts"),
  ]);
  const at = new Date();
  const name = reportFileName(r.technician, at);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (pdfMake as any).vfs = (vfs as any).pdfMake?.vfs ?? vfs;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const blob = await new Promise<Blob>((ok) => pdfMake.createPdf(reportDoc(r, at) as any).getBlob(ok));
  saveFile(blob, name);
  return name;
}

/** Сохранение файла под заданным именем (ссылка с атрибутом download). */
export function saveFile(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
