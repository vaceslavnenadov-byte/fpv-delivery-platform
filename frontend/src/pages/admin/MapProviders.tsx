import { useState, type FormEvent } from "react";
import { api, errorText } from "../../api";
import { Alert, Card, Empty, Pending, Tone } from "../../components/ui";
import { fmtDate } from "../../labels";
import { useLive } from "../../realtime";
import { invalidateProviders } from "../../tiles";

// Онлайн-карты по API-ключу: администратор выбирает сервис (ESRI, Google, Яндекс, 2ГИС,
// OpenStreetMap или свой XYZ), вводит ключ и проверяет подключение. Ключ хранится на сервере
// и обратно не показывается — только последние символы.

interface Preset {
  code: string; title: string; key_required: boolean; projection: "3857" | "3395"; max_zoom: number;
  url_template: string; attribution: string; help: string;
}
interface ProviderRow {
  id: string; preset: string; name: string; projection: string; min_zoom: number; max_zoom: number; attribution: string;
  active: boolean; sort_order: number; url_template: string; api_key_masked: string; has_key: boolean;
  last_error: string | null; last_error_at: string | null;
}
type Msg = { tone: string; text: string } | null;

export function MapProvidersCard() {
  const list = useLive(() => api.get<{ items: ProviderRow[]; presets: Preset[] }>("/api/v1/admin/map-providers"), []);
  const [msg, setMsg] = useState<Msg>(null);
  const [form, setForm] = useState({ preset: "esri_imagery", name: "", api_key: "", url_template: "", attribution: "", projection: "" });
  const [busy, setBusy] = useState(false);
  const [keyEdit, setKeyEdit] = useState<Record<string, string>>({});
  const presets = list.data?.presets ?? [];
  const preset = presets.find((p) => p.code === form.preset);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "ok", text: ok });
      invalidateProviders();
      list.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }
  function add(e: FormEvent) {
    e.preventDefault();
    const body: Record<string, unknown> = { preset: form.preset };
    if (form.name.trim()) body.name = form.name.trim();
    if (form.api_key.trim()) body.api_key = form.api_key.trim();
    if (form.url_template.trim()) body.url_template = form.url_template.trim();
    if (form.attribution.trim()) body.attribution = form.attribution.trim();
    if (form.projection) body.projection = form.projection;
    void run(() => api.post("/api/v1/admin/map-providers", body), "Карта подключена — проверьте её кнопкой «Проверить»")
      .then(() => setForm((f) => ({ ...f, name: "", api_key: "", url_template: "" })));
  }
  async function test(r: ProviderRow) {
    setBusy(true);
    try {
      const res = await api.post<{ ok: boolean; message: string }>(`/api/v1/admin/map-providers/${r.id}/test`);
      setMsg({ tone: res.ok ? "ok" : "danger", text: `${r.name}: ${res.message}` });
      list.reload();
    } catch (err) {
      setMsg({ tone: "danger", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Онлайн-карты по API-ключу">
      <p className="muted small">Заказчик выбирает карту при оформлении заказа; оператор и координатор по умолчанию видят
        ту же карту и могут переключиться на другую. Плитки идут через сервер платформы: ключ не попадает в браузеры,
        но серверу нужен доступ в интернет к сервису карт. Условия использования (лицензия, лимиты, указание авторства)
        определяет договор вашей организации с сервисом.</p>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {!list.data ? <Pending /> : list.data.items.length === 0 ? <Empty>Онлайн-карты не подключены</Empty> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Карта</th><th>Ключ</th><th>Состояние</th><th>Порядок</th><th>Действия</th></tr></thead>
          <tbody>{list.data.items.map((r) => (
            <tr key={r.id}>
              <td><strong>{r.name}</strong>
                <div className="muted small">{presets.find((p) => p.code === r.preset)?.title ?? r.preset} · проекция EPSG:{r.projection} · масштаб {r.min_zoom}–{r.max_zoom}</div>
                {r.url_template && <div className="muted small mono map-template">{r.url_template.replace(/([?&](apikey|key|token)=)[^&]*/gi, "$1…")}</div>}</td>
              <td>
                <span className="mono">{r.has_key ? r.api_key_masked : "—"}</span>
                <form className="inline-form" onSubmit={(e) => { e.preventDefault();
                  void run(() => api.patch(`/api/v1/admin/map-providers/${r.id}`, { api_key: keyEdit[r.id] ?? "" }), "Ключ обновлён");
                  setKeyEdit({ ...keyEdit, [r.id]: "" }); }}>
                  <input type="password" autoComplete="off" placeholder="Новый ключ" value={keyEdit[r.id] ?? ""}
                         onChange={(e) => setKeyEdit({ ...keyEdit, [r.id]: e.target.value })} aria-label={`Новый ключ для ${r.name}`} />
                  <button className="btn small" disabled={busy || !(keyEdit[r.id] ?? "").trim()}>Заменить</button>
                </form>
              </td>
              <td>{!r.active ? <Tone tone="muted">отключена</Tone>
                : r.last_error ? <><Tone tone="danger">ошибка</Tone><div className="small">{r.last_error}</div>
                    <div className="muted small">{fmtDate(r.last_error_at)}</div></>
                : <Tone tone="ok">включена</Tone>}</td>
              <td><input type="number" min={0} max={10000} defaultValue={r.sort_order} style={{ width: 80 }} aria-label={`Порядок ${r.name}`}
                         onBlur={(e) => Number(e.target.value) !== r.sort_order &&
                           void run(() => api.patch(`/api/v1/admin/map-providers/${r.id}`, { sort_order: Number(e.target.value) }), "Порядок изменён")} /></td>
              <td className="row wrap">
                <button className="btn small" disabled={busy} onClick={() => void test(r)}>Проверить</button>
                <button className="btn small" disabled={busy}
                        onClick={() => void run(() => api.patch(`/api/v1/admin/map-providers/${r.id}`, { active: !r.active }),
                          r.active ? "Карта отключена" : "Карта включена")}>{r.active ? "Отключить" : "Включить"}</button>
                <button className="btn small danger" disabled={busy}
                        onClick={() => confirm(`Удалить «${r.name}»? В заказах с этой картой будет показана карта по умолчанию.`) &&
                          void run(() => api.del(`/api/v1/admin/map-providers/${r.id}`), "Карта удалена")}>Удалить</button>
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <form className="form grid2 provider-form" onSubmit={add}>
        <label>Сервис
          <select value={form.preset} onChange={(e) => setForm({ ...form, preset: e.target.value, url_template: "", projection: "" })}>
            {presets.map((p) => <option key={p.code} value={p.code}>{p.title}</option>)}
          </select>
        </label>
        <label>Название в списке карт
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={preset?.title} maxLength={120} />
        </label>
        <label>API-ключ{preset?.key_required ? " (обязателен)" : " (если нужен)"}
          <input type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                 required={preset?.key_required} />
        </label>
        <label>Проекция
          <select value={form.projection || preset?.projection || "3857"} onChange={(e) => setForm({ ...form, projection: e.target.value })}>
            <option value="3857">EPSG:3857 (Web Mercator — большинство сервисов)</option>
            <option value="3395">EPSG:3395 (Яндекс)</option>
          </select>
        </label>
        {preset && !preset.code.startsWith("google") && (
          <label className="span2">Адрес плиток{preset.code === "custom" ? " (обязателен)" : " (можно изменить)"}
            <input value={form.url_template} onChange={(e) => setForm({ ...form, url_template: e.target.value })}
                   placeholder={preset.url_template || "https://tiles.example.com/{z}/{x}/{y}.png?key={key}"}
                   required={preset.code === "custom"} className="mono" />
          </label>
        )}
        <label className="span2">Указание авторства (показывается на карте)
          <input value={form.attribution} onChange={(e) => setForm({ ...form, attribution: e.target.value })} placeholder={preset?.attribution} maxLength={300} />
        </label>
        {preset && <p className="span2 muted small">{preset.help}</p>}
        <div><button className="btn primary" disabled={busy}>Подключить карту</button></div>
      </form>
    </Card>
  );
}
