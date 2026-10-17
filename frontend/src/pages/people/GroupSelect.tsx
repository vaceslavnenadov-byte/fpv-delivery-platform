import type { OrgUnit } from "./common";
import { treeOrder } from "./common";

/** Выбор группы/подгруппы из дерева (отступ показывает вложенность). */
export function GroupSelect({ units, value, onChange, empty = "— без группы —", label }: {
  units: OrgUnit[]; value: string; onChange: (v: string) => void; empty?: string; label?: string;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label ?? "Группа"}>
      <option value="">{empty}</option>
      {treeOrder(units).map((u) => (
        <option key={u.id} value={u.id}>{"  ".repeat(u.depth)}{u.depth ? "└ " : ""}{u.name}</option>
      ))}
    </select>
  );
}
