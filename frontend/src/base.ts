// Базовый путь текущего клиента (у каждой роли свой: /customer/, /operator/ и т. д.).
// Нужен для переходов, которые идут мимо маршрутизатора (window.open, <a href>) — там
// basename роутера не подставляется автоматически.
let base = "/";

export function setAppBase(b: string): void {
  base = b.endsWith("/") ? b : `${b}/`;
}

export function appBase(): string {
  return base;
}

/** Абсолютный URL внутри текущего клиента: appUrl("/orders/1") → "/operator/orders/1". */
export function appUrl(path: string): string {
  return base.replace(/\/$/, "") + (path.startsWith("/") ? path : `/${path}`);
}
