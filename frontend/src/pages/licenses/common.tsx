import type { ReactNode } from "react";
import { KV } from "../../components/ui";
import { ScanViewer } from "../../components/ScanViewer";

// Лицензии операторов на типы БПЛА: общие типы и карточка заявки (данные слева, скан справа).

export interface License {
  id: number;
  user_id: string;
  full_name: string;
  short_name: string;
  username: string | null;
  drone_model_id: string | null;
  model: string;
  manufacturer: string;
  link_type: string;
  construction: string;
  engine_type: string;
  license_number: string;
  issued_on: string;
  issued_by: string;
  scan_type: string;
  scan_name: string;
  scan_url: string;
  status: "pending" | "fix" | "approved" | "rejected";
  status_label: string;
  review_comment: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export const LICENSE_STATUS: Record<string, string> = {
  pending: "На согласовании", fix: "Исправить", approved: "Подтверждена", rejected: "Отклонена/Аннулирована",
};

export function LicenseStatus({ s }: { s: string }) {
  return <span className={`lic-status lic-${s}`}>{LICENSE_STATUS[s] ?? s}</span>;
}

export const fmtDay = (iso: string) => {
  const [y, m, d] = iso.split("-");
  return d && m && y ? `${d}.${m}.${y}` : iso;
};

/** Карточка заявки: слева — данные, справа — скан лицензии с увеличением. */
export function LicenseCardBody({ l, extra }: { l: License; extra?: ReactNode }) {
  return (
    <div className="license-card">
      <div className="license-data">
        <KV items={[
          ["Фамилия Имя Отчество", l.full_name],
          ["Модель", l.model],
          ["Производитель", l.manufacturer],
          ["Тип связи", l.link_type || "—"],
          ["Конструкция", l.construction || "—"],
          ["Тип двигателя", l.engine_type || "—"],
          ["Номер лицензии", l.license_number],
          ["Когда получена", fmtDay(l.issued_on)],
          ["Кем выдана", l.issued_by],
          ["Лицензия", <LicenseStatus s={l.status} />],
          ...(l.review_comment ? [["Комментарий проверяющего", l.review_comment] as [string, ReactNode]] : []),
          ...(l.reviewed_by ? [["Решение", `${l.reviewed_by}${l.reviewed_at ? `, ${new Date(l.reviewed_at).toLocaleString("ru-RU")}` : ""}`] as [string, ReactNode]] : []),
        ]} />
        {extra}
      </div>
      <div className="license-scan">
        <strong className="small">Скан лицензии{l.scan_name ? `: ${l.scan_name}` : ""}</strong>
        <ScanViewer url={l.scan_url} type={l.scan_type} title={`Лицензия ${l.license_number}`} />
      </div>
    </div>
  );
}
