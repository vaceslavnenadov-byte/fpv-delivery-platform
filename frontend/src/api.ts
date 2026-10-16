// Клиент REST API. Сессия — HttpOnly cookie; для изменяющих запросов передаётся CSRF-токен.
// Критические операции отправляются с Idempotency-Key: повтор после обрыва связи не создаёт дубликатов.

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function readCookie(name: string): string {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : "";
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

let unauthorizedHandler: (() => void) | null = null;
export function onUnauthorized(fn: () => void) {
  unauthorizedHandler = fn;
}

interface RequestOptions {
  idempotencyKey?: string;
  retries?: number;
  headers?: Record<string, string>;   // дополнительные заголовки (например, тип и номер видеосегмента)
}

async function request<T>(method: string, url: string, body?: unknown, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-CSRF-Token"] = readCookie("fpv_csrf");
  const raw = typeof Blob !== "undefined" && body instanceof Blob;   // файл передаётся телом запроса как есть
  if (body !== undefined) headers["Content-Type"] = raw ? "application/octet-stream" : "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  Object.assign(headers, opts.headers ?? {});
  let attempt = 0;
  const retries = opts.retries ?? (method === "GET" || opts.idempotencyKey ? 2 : 0);
  for (;;) {
    let resp: Response;
    try {
      resp = await fetch(url, {
        method,
        headers,
        credentials: "same-origin",
        body: raw ? (body as Blob) : body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      // Сетевой сбой: безопасно повторяем только чтение и операции с ключом идемпотентности.
      if (attempt < retries) {
        attempt += 1;
        await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
        continue;
      }
      throw new ApiError(0, "network", "Нет связи с сервером. Проверьте подключение и повторите.");
    }
    if (resp.status === 401 && unauthorizedHandler && !url.endsWith("/auth/login")) unauthorizedHandler();
    const text = await resp.text();
    let data;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      // Ответ не от API (например, страница ошибки nginx 502, когда служба fpv-api остановлена).
      throw new ApiError(resp.status, "bad_gateway", resp.status >= 500
        ? `Сервер приложения недоступен (HTTP ${resp.status}). Обратитесь к администратору.`
        : `Неожиданный ответ сервера (HTTP ${resp.status}).`);
    }
    if (!resp.ok) {
      const err = data.error ?? {};
      throw new ApiError(resp.status, err.code ?? "error", err.message ?? `Ошибка ${resp.status}`, err.details ?? {});
    }
    return data as T;
  }
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown, opts?: RequestOptions) => request<T>("POST", url, body ?? {}, opts),
  put: <T>(url: string, body?: unknown) => request<T>("PUT", url, body ?? {}),
  patch: <T>(url: string, body?: unknown) => request<T>("PATCH", url, body ?? {}),
  upload: <T>(url: string, file: Blob, headers?: Record<string, string>) =>
    request<T>("POST", url, file, { headers, retries: 0 }),
  del: <T>(url: string) => request<T>("DELETE", url),
};

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const errs = (e.details?.errors as { loc: string[]; msg: string }[] | undefined) ?? [];
    if (errs.length) return `${e.message}: ${errs.map((x) => `${x.loc.slice(1).join(".")} — ${x.msg}`).join("; ")}`;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
