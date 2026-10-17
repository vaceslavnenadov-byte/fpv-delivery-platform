import type { UserBrief } from "../../types";

export interface Ticket {
  id: number; reason: string; reason_label: string; subject: string; status: string; status_label: string;
  author: UserBrief | null; order: { id: string; number: number } | null; created_at: string; last_message_at: string;
  status_changed_at: string | null; status_changed_by: UserBrief | null; unread: boolean;
}
export interface Attachment { id: number; content_type: string; size_bytes: number; filename: string; url: string }
export interface Message { id: number; author: UserBrief | null; body: string; created_at: string; system: boolean; attachments: Attachment[] }
export interface TicketDetail extends Ticket {
  participants: (UserBrief & { roles: string[]; added_by: UserBrief | null; added_at: string })[];
  messages: Message[];
}

export const REASONS: [string, string][] = [
  ["order", "По заказу"], ["account", "По аккаунту"], ["group", "По группе"],
  ["service", "По работе сервиса"], ["suggestion", "Предложения"], ["other", "Другое"],
];
export const STATUS_TONE: Record<string, string> = { new: "warn", in_progress: "info", rejected: "danger", done: "ok" };
export const ACCEPT = "image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm";
