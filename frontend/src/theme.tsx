import { useState } from "react";

// Тема оформления: светлая, тёмная или как в системе. Выбор хранится в браузере пользователя
// (общий для всех клиентов на этом сайте). Монитор полёта и видеостена всегда тёмные — так
// удобнее смотреть видео с телеметрией.

export type ThemePref = "light" | "dark" | "system";
const KEY = "fpv.theme";

export function getTheme(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(pref: ThemePref = getTheme()): void {
  const root = document.documentElement;
  if (pref === "system") {
    root.removeAttribute("data-theme");
    root.setAttribute("data-theme-auto", "");
  } else {
    root.setAttribute("data-theme", pref);
    root.removeAttribute("data-theme-auto");
  }
}

export function setTheme(pref: ThemePref): void {
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    /* хранилище недоступно — тема действует до перезагрузки */
  }
  applyTheme(pref);
}

const OPTIONS: { value: ThemePref; label: string; title: string }[] = [
  { value: "light", label: "☀", title: "Светлая тема" },
  { value: "dark", label: "☾", title: "Тёмная тема" },
  { value: "system", label: "A", title: "Как в системе" },
];

export function ThemeSwitch() {
  const [pref, setPref] = useState<ThemePref>(getTheme);
  return (
    <div className="theme-switch" role="group" aria-label="Тема оформления">
      {OPTIONS.map((o) => (
        <button key={o.value} type="button" title={o.title} aria-label={o.title} aria-pressed={pref === o.value}
                onClick={() => { setTheme(o.value); setPref(o.value); }}>{o.label}</button>
      ))}
    </div>
  );
}
