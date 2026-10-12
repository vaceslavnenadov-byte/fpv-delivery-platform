#!/bin/bash
# Общие функции скриптов установки. Подключается через `source`.
# Принципы: строгий режим, явные коды завершения, секреты не передаются аргументами и не пишутся в журнал.

set -Eeuo pipefail
umask 027

# shellcheck disable=SC2034  # используются подключающими скриптами
FPV_ALLOWED_PLAYBOOKS="site base domain database storage application workstation verify"
# shellcheck disable=SC2034
FPV_ALLOWED_MODES="local central"

# Коды завершения
# shellcheck disable=SC2034
readonly E_USAGE=2 E_PREFLIGHT=3 E_INTEGRITY=4 E_DEPS=5 E_ANSIBLE=6 E_ROLLBACK=7

log()  { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${FPV_SCRIPT:-fpv}" "$*"; }
warn() { log "ПРЕДУПРЕЖДЕНИЕ: $*" >&2; }
die()  { local code="$1"; shift; log "ОШИБКА: $*" >&2; exit "$code"; }

require_cmd() {
  local c
  for c in "$@"; do command -v "$c" >/dev/null 2>&1 || die "$E_DEPS" "не найдена команда: $c"; done
}

in_list() {  # in_list значение "a b c"
  local v="$1" item
  for item in $2; do [[ "$item" == "$v" ]] && return 0; done
  return 1
}

# Каталог комплекта: каталог, где лежит вызываемый скрипт (корень /tmp/ancible или deploy/ansible/scripts/..).
bundle_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
  if [[ -f "$here/ansible.cfg" ]]; then echo "$here"; else (cd "$here/.." && pwd); fi
}

check_secret_file() {  # файл с секретом должен принадлежать текущему пользователю и иметь права 600/400
  local f="$1" mode owner
  [[ -f "$f" ]] || die "$E_USAGE" "файл не найден: $f"
  mode="$(stat -c %a "$f")"; owner="$(stat -c %u "$f")"
  [[ "$mode" == "600" || "$mode" == "400" ]] || die "$E_USAGE" "небезопасные права $mode на $f (нужно 600 или 400)"
  [[ "$owner" == "$(id -u)" ]] || die "$E_USAGE" "файл $f принадлежит другому пользователю"
}

start_log() {  # start_log <каталог> <префикс>; весь вывод дублируется в журнал с правами 600
  local dir="$1" prefix="$2"
  mkdir -p "$dir"
  chmod 750 "$dir"
  FPV_LOG_FILE="$dir/${prefix}-$(date -u +%Y%m%dT%H%M%SZ).log"
  ( umask 077; : > "$FPV_LOG_FILE" )
  exec > >(tee -a "$FPV_LOG_FILE") 2>&1
  log "журнал: $FPV_LOG_FILE"
}

on_error() {
  local code=$? line="${1:-?}"
  log "ОШИБКА: этап завершился с кодом $code (строка $line). Дальнейшие шаги не выполняются." >&2
  [[ -n "${FPV_LOG_FILE:-}" ]] && log "подробности: $FPV_LOG_FILE" >&2
  exit "$code"
}
