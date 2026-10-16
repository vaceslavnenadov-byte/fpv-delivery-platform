import { describe, expect, it } from "vitest";
import { accessibleApps, roleHome, safeNext } from "./config";

const perms = (...p: string[]) => (x: string) => p.includes(x);
const operator = perms("order.queue.read");
const customerOperator = perms("order.read_own", "order.queue.read");

describe("roleHome", () => {
  it("ведёт в клиент роли по старшинству", () => {
    expect(roleHome(perms("order.read_own"))).toBe("/customer/");
    expect(roleHome(customerOperator)).toBe("/operator/");
    expect(roleHome(perms("report.read", "order.queue.read"))).toBe("/admin/");
    expect(roleHome(perms("user.recruit", "order.read_own"))).toBe("/recruiter/");
  });
  it("без роли — null (остаётся на общем входе с профилем)", () => {
    expect(roleHome(perms())).toBeNull();
  });
});

describe("accessibleApps", () => {
  it("перечисляет все клиенты пользователя с несколькими ролями", () => {
    expect(accessibleApps(customerOperator).map((a) => a.key)).toEqual(["operator", "customer"]);
  });
});

describe("safeNext", () => {
  it("возвращает в доступный клиент, включая окно монитора", () => {
    expect(safeNext("/operator/orders/5/monitor", operator)).toBe("/operator/orders/5/monitor");
    expect(safeNext("/operator", operator)).toBe("/operator/");
  });
  it("не ведёт в чужой клиент", () => {
    expect(safeNext("/admin/users", operator)).toBeNull();
    expect(safeNext("/recruiter/profiles", operator)).toBeNull();
    expect(safeNext("/recruiter/profiles", perms("user.recruit"))).toBe("/recruiter/profiles");
  });
  it.each([
    "https://evil.example/", "//evil.example/operator/", "/\\evil.example", "javascript:alert(1)",
    "/operator/\nX", "/elsewhere/", "", null,
  ])("отклоняет небезопасный адрес %s", (next) => {
    expect(safeNext(next as string | null, operator)).toBeNull();
  });
});
