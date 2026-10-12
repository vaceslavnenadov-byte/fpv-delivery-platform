#!/bin/bash
# Итоговая проверка после установки: playbook verify.yml и сводка отчёта.
FPV_SCRIPT=postflight
source "$(dirname "$0")/lib.sh"
trap 'on_error $LINENO' ERR

usage() { echo "Использование: $0 --mode local|central --inventory PATH [--limit HOSTS] [--vault-password-file F]"; }
MODE="" INVENTORY="" LIMIT="" VAULT_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --inventory) INVENTORY="${2:-}"; shift 2 ;;
    --limit) LIMIT="${2:-}"; shift 2 ;;
    --vault-password-file) VAULT_FILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "$E_USAGE" "неизвестный параметр: $1" ;;
  esac
done
in_list "$MODE" "$FPV_ALLOWED_MODES" || { usage >&2; die "$E_USAGE" "укажите --mode"; }
[[ -f "$INVENTORY" ]] || die "$E_USAGE" "inventory не найден: '$INVENTORY'"
args=(--mode "$MODE" --inventory "$INVENTORY" --playbook verify)
[[ -n "$LIMIT" ]] && args+=(--limit "$LIMIT")
[[ -n "$VAULT_FILE" ]] && args+=(--vault-password-file "$VAULT_FILE")
"$(dirname "$0")/run-playbook.sh" "${args[@]}"
if [[ "$MODE" == "local" && -r /var/log/fpv/verify-report.json ]]; then
  python3 - /var/log/fpv/verify-report.json <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
print("узел:", r["host"], "группы:", ", ".join(r["groups"]) or "—")
for c in r["results"]:
    print(("  ✓ " if c["ok"] else "  ✕ ") + c["check"])
PY
fi
log "итоговая проверка пройдена"
