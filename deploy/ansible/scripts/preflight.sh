#!/bin/bash
# Предварительная проверка: ОС, архитектура, права, диск, Python, DNS, доступность репозиториев.
# Ничего не изменяет в системе. Код 0 — проверки пройдены, 3 — найдены проблемы, 2 — ошибка аргументов.
FPV_SCRIPT=preflight
source "$(dirname "$0")/lib.sh"

usage() {
  cat <<USAGE
Использование: $0 --mode local|central [--inventory PATH] [--offline] [--min-disk-mb N] [--url URL ...]
  --mode       local — проверка целевого узла; central — проверка управляющего узла и разрешения имён целей
  --inventory  inventory для проверки DNS имён узлов (режим central)
  --offline    не проверять доступность внешних репозиториев (используется локальный комплект пакетов)
  --url        дополнительный адрес репозитория/зеркала для проверки доступности (можно несколько)
USAGE
}

MODE="" INVENTORY="" OFFLINE=0 MIN_DISK_MB=2048 URLS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --inventory) INVENTORY="${2:-}"; shift 2 ;;
    --offline) OFFLINE=1; shift ;;
    --min-disk-mb) MIN_DISK_MB="${2:-}"; shift 2 ;;
    --url) URLS+=("${2:-}"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "$E_USAGE" "неизвестный параметр: $1" ;;
  esac
done
in_list "$MODE" "$FPV_ALLOWED_MODES" || { usage >&2; die "$E_USAGE" "укажите --mode local|central"; }
[[ "$MIN_DISK_MB" =~ ^[0-9]+$ ]] || die "$E_USAGE" "--min-disk-mb должно быть числом"

ROOT="$(bundle_root)"
FAILED=()
fail() { FAILED+=("$1"); log "  ✕ $1"; }
ok()   { log "  ✓ $1"; }

log "проверка ОС"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" == "debian" && "${VERSION_ID:-}" == "13" ]]; then ok "Debian 13 (${PRETTY_NAME:-})"
  elif [[ "$MODE" == "central" ]]; then warn "управляющий узел: ${PRETTY_NAME:-неизвестно} (рекомендуется Debian 13)"
  else fail "требуется Debian 13, обнаружено: ${PRETTY_NAME:-неизвестно}"; fi
else
  fail "не удалось определить ОС (/etc/os-release)"
fi

ARCH="$(uname -m)"
case "$ARCH" in x86_64|aarch64) ok "архитектура $ARCH" ;; *) fail "неподдерживаемая архитектура $ARCH" ;; esac

if [[ "$MODE" == "local" && "$(id -u)" -ne 0 ]]; then
  if command -v sudo >/dev/null && sudo -n true 2>/dev/null; then ok "права: sudo без запроса пароля"
  else warn "запуск не от root: задачи с become потребуют sudo (пароль запрашивается интерактивно, не сохраняется)"; fi
else
  ok "права пользователя $(id -un)"
fi

for path in "$ROOT" /opt /var; do
  [[ -d "$path" ]] || continue
  free_mb=$(( $(df -Pk "$path" | awk 'NR==2 {print $4}') / 1024 ))
  if (( free_mb >= MIN_DISK_MB )); then ok "свободно в $path: ${free_mb} МБ"; else fail "мало места в $path: ${free_mb} МБ (нужно ${MIN_DISK_MB})"; fi
done

if command -v python3 >/dev/null; then
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
  else fail "требуется python3 >= 3.12 (ansible-core 2.21 на управляющем узле)"; fi
  if python3 -c 'import venv, ensurepip' 2>/dev/null; then ok "модуль venv/ensurepip"; else fail "нет модуля venv/ensurepip (пакет python3-venv)"; fi
else
  fail "python3 не установлен"
fi

if [[ "$MODE" == "central" && -n "$INVENTORY" ]]; then
  [[ -f "$INVENTORY" ]] || die "$E_USAGE" "inventory не найден: $INVENTORY"
  log "разрешение имён узлов inventory"
  while read -r host; do
    [[ -z "$host" || "$host" == "localhost" ]] && continue
    if getent hosts "$host" >/dev/null; then ok "DNS $host"; else fail "имя не разрешается: $host"; fi
  done < <(grep -E '^\s{4,}[A-Za-z0-9.-]+\.[A-Za-z]{2,}:\s*$' "$INVENTORY" | tr -d ' :' | sort -u)
fi

if (( OFFLINE == 0 )); then
  log "доступность репозиториев"
  [[ ${#URLS[@]} -eq 0 ]] && URLS=("https://pypi.org/simple/ansible-core/")
  for u in "${URLS[@]}"; do
    if curl -fsS -o /dev/null --max-time 10 "$u"; then ok "доступен $u"; else fail "недоступен $u (используйте --offline и локальный комплект пакетов)"; fi
  done
else
  if [[ -d "$ROOT/packages" ]]; then ok "офлайн-режим: найден каталог packages/"; else fail "офлайн-режим: нет каталога packages/ в комплекте"; fi
fi

if (( ${#FAILED[@]} > 0 )); then
  log "предварительная проверка НЕ пройдена (${#FAILED[@]}):"
  printf '  - %s\n' "${FAILED[@]}"
  exit "$E_PREFLIGHT"
fi
log "предварительная проверка пройдена"
