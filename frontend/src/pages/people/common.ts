import { api } from "../../api";

export interface OrgUnit { id: string; parent_id: string | null; name: string; path: string; members: number }
export interface PersonRow {
  id: string; username: string; display_name: string; auth_source: "domain" | "local"; status: string; status_label: string;
  position_title: string | null; org_unit: { id: string; path: string } | null; roles: { role: string; source: string }[];
  profile_status: string; last_login_at: string | null;
}
export interface Brief { id: string; display_name: string; username: string }
export interface PersonCard {
  id: string; username: string; auth_source: "domain" | "local"; display_name: string; status: string; status_label: string;
  status_reason: string | null; status_changed_at: string | null; status_changed_by: Brief | null; directory_enabled: boolean;
  profile: { last_name: string | null; first_name: string | null; middle_name: string | null; position_title: string | null;
             profile_notes: string | null; phone: string | null; email: string | null; nickname: string | null;
             location: string | null };
  org_unit: { id: string; path: string } | null;
  roles: { role: string; source: string; granted_by: Brief | null; granted_at: string | null }[];
  password_changed_at: string | null; password_changed_by: Brief | null; registered_by: Brief | null;
  created_at: string | null; last_login_at: string | null; profile_status: string; profile_completed: boolean;
  operator: { status: string; license_number: string } | null;
}
export interface RoleRow { code: string; name: string; description: string }

export const STATUS_TONE: Record<string, string> = { active: "ok", suspended: "warn", banned: "danger", disabled: "muted" };

export function loadUnits() {
  return api.get<{ items: OrgUnit[] }>("/api/v1/org-units");
}

/** Список групп в порядке дерева (для выпадающих списков): родитель, затем его подгруппы. */
export function treeOrder(units: OrgUnit[]): (OrgUnit & { depth: number })[] {
  const byParent = new Map<string | null, OrgUnit[]>();
  for (const u of units) byParent.set(u.parent_id, [...(byParent.get(u.parent_id) ?? []), u]);
  const out: (OrgUnit & { depth: number })[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const u of (byParent.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name, "ru"))) {
      out.push({ ...u, depth });
      walk(u.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}
