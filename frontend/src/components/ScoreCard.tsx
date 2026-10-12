import { SCORE_ITEMS, fmtDate } from "../labels";
import type { Score } from "../types";

export function ScoreCard({ score }: { score: Score }) {
  const total = score.preliminary ? score.total : score.total_with_adjustments ?? score.final_points;
  return (
    <div className="score">
      <div className="score-total">
        <span className="score-value">{total ?? 0}</span>
        <span className="muted">/ 100</span>
        <span className={`badge ${score.preliminary ? "tone-warn" : "tone-ok"}`}>
          {score.preliminary ? "Предварительно" : "Окончательно"}
        </span>
      </div>
      <table className="table compact">
        <tbody>
          <tr>
            <td>Соблюдение временного окна</td>
            <td className="num">{score.time_window.points} / {score.time_window.max ?? 30}</td>
          </tr>
          {score.time_window.deviation_minutes != null && score.time_window.deviation_minutes > 0 && (
            <tr><td className="muted small" colSpan={2}>Отклонение от окна: {score.time_window.deviation_minutes} мин</td></tr>
          )}
          {Object.entries(score.result.items).map(([k, v]) => (
            <tr key={k}>
              <td>{SCORE_ITEMS[k] ?? k}</td>
              <td className="num">{v}</td>
            </tr>
          ))}
          {score.result.penalty > 0 && (
            <tr><td>Обоснованные претензии ({score.result.upheld_complaints})</td><td className="num">−{score.result.penalty}</td></tr>
          )}
          {(score.adjustments ?? []).map((a, i) => (
            <tr key={i}><td>Корректировка: {a.reason} <span className="muted small">{fmtDate(a.created_at)}</span></td>
              <td className="num">{a.points > 0 ? "+" : ""}{a.points}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        Скорость полёта не учитывается. Баллы начисляются только после проверки администратором
        {score.rule_version ? `; правила v${score.rule_version}` : ""}.
      </p>
    </div>
  );
}
