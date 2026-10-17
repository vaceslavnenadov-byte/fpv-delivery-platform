import { describe, expect, it } from "vitest";
import { reportDoc, reportFileName, reportOrder, type ReportDrone } from "./techReport";

const drone = (over: Partial<ReportDrone>): ReportDrone => ({
  id: over.registration_number ?? "x", serial_number: "S", registration_number: "R", manufacturer: "M", model: "X",
  adapter_kind: "simulator", max_payload_g: 1000, max_range_m: 10000, status: "active", health_status: "serviceable",
  link_status: "offline", last_seen_at: null, battery_pct: null, firmware_version: null, has_key: false, key_prefix: null,
  blocked_reason: null, owner_id: "o", fleet_name: "Парк", last_flight_at: null, ...over,
});

describe("отчёт о техсостоянии", () => {
  it("имя файла: группа_подгруппы_имя_фамилия_дата", () => {
    const name = reportFileName({ first_name: "Иван", last_name: "Петров", display_name: "Петров Иван",
      group: "ООО Дрон", subgroups: ["Техотдел", "Смена 1"] }, new Date(2026, 9, 17));
    expect(name).toBe("ООО-Дрон_Техотдел_Смена-1_Иван_Петров_17.10.2026.pdf");
  });
  it("без группы — только имя и дата; недопустимые символы убираются", () => {
    expect(reportFileName({ first_name: "A/B", last_name: "C", display_name: "C A", group: null, subgroups: [] },
      new Date(2026, 0, 2))).toBe("AB_C_02.01.2026.pdf");
  });
  it("сначала исправные, архивные — в конце", () => {
    const rows = reportOrder([
      drone({ registration_number: "A1", archived: true, archive_reason: "lost" }),
      drone({ registration_number: "B1", health_status: "grounded" }),
      drone({ registration_number: "C1" }),
    ]);
    expect(rows.map((d) => d.registration_number)).toEqual(["C1", "B1", "A1"]);
    const doc = reportDoc({ items: rows, generated_at: "", technician: { first_name: "И", last_name: "П", display_name: "П И",
      group: null, subgroups: [] } }, new Date(2026, 9, 17));
    const table = (doc.content[4] as { table: { body: unknown[][] } }).table.body;
    expect(table).toHaveLength(4);
    expect(table[3][5]).toBe("Утерян");
    expect(table[3][4]).toBe("В архиве");
  });
});
