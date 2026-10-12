#!/bin/bash
# Единая точка запуска playbook. Ansible вызывается только из venv комплекта по явному пути.
FPV_SCRIPT=run-playbook
source "$(dirname "$0")/lib.sh"
trap 'on_error $LINENO' ERR

usage() {
  cat <<USAGE
Использование: $0 --mode local|central --inventory PATH --playbook NAME [параметры]
  --mode                 local (только этот узел, подключение local) | central (узлы по SSH)
  --inventory PATH       файл inventory (обязательно)
  --playbook NAME        $FPV_ALLOWED_PLAYBOOKS
  --limit HOSTS          цели (обязательно в режиме central; в local всегда localhost)
  --check                режим проверки без изменений (--check --diff)
  --tags LIST            ограничить задачи тегами
  --vault-password-file  файл пароля Ansible Vault (права 600)
  --extra-vars-file      YAML-файл дополнительных переменных (секреты в нём не допускаются, используйте Vault)
  --venv DIR             виртуальное окружение (по умолчанию <комплект>/venv)
  --ask-become-pass      запросить пароль sudo интерактивно (не сохраняется)
USAGE
}

MODE="" INVENTORY="" PLAYBOOK="" LIMIT="" CHECK=0 TAGS="" VAULT_FILE="" EXTRA_FILE="" VENV="" ASK_BECOME=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --inventory) INVENTORY="${2:-}"; shift 2 ;;
    --playbook) PLAYBOOK="${2:-}"; shift 2 ;;
    --limit) LIMIT="${2:-}"; shift 2 ;;
    --check) CHECK=1; shift ;;
    --tags) TAGS="${2:-}"; shift 2 ;;
    --vault-password-file) VAULT_FILE="${2:-}"; shift 2 ;;
    --extra-vars-file) EXTRA_FILE="${2:-}"; shift 2 ;;
    --venv) VENV="${2:-}"; shift 2 ;;
    --ask-become-pass) ASK_BECOME=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "$E_USAGE" "неизвестный параметр: $1" ;;
  esac
done

ROOT="$(bundle_root)"
VENV="${VENV:-$ROOT/venv}"
in_list "$MODE" "$FPV_ALLOWED_MODES" || { usage >&2; die "$E_USAGE" "укажите --mode local|central"; }
in_list "$PLAYBOOK" "$FPV_ALLOWED_PLAYBOOKS" || { usage >&2; die "$E_USAGE" "недопустимый playbook: '$PLAYBOOK'"; }
[[ -n "$INVENTORY" && -f "$INVENTORY" ]] || die "$E_USAGE" "inventory не найден: '$INVENTORY'"
[[ -x "$VENV/bin/ansible-playbook" ]] || die "$E_DEPS" "нет $VENV/bin/ansible-playbook — сначала выполните install.sh"
[[ -z "$TAGS" || "$TAGS" =~ ^[A-Za-z0-9_,-]+$ ]] || die "$E_USAGE" "недопустимые теги"

if [[ "$MODE" == "local" ]]; then
  [[ -z "$LIMIT" || "$LIMIT" == "localhost" ]] || die "$E_USAGE" "в режиме local целью может быть только localhost"
  LIMIT="localhost"
  conn="$("$VENV/bin/ansible-inventory" -i "$INVENTORY" --host localhost 2>/dev/null | "$VENV/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("ansible_connection",""))' || true)"  # пустой результат проверяется ниже
  [[ "$conn" == "local" ]] || die "$E_USAGE" "inventory $INVENTORY не описывает localhost с ansible_connection=local (режим local)"
else
  [[ -n "$LIMIT" ]] || die "$E_USAGE" "в режиме central цели указываются явно: --limit <узлы|группы|all>"
  [[ "$LIMIT" =~ ^[A-Za-z0-9_.:,!\&*-]+$ ]] || die "$E_USAGE" "недопустимое значение --limit"
  [[ "$LIMIT" != "localhost" ]] || die "$E_USAGE" "для управления этим узлом используйте --mode local"
fi

ARGS=(-i "$INVENTORY" --limit "$LIMIT" "$ROOT/playbooks/$PLAYBOOK.yml")
(( CHECK )) && ARGS+=(--check --diff)
[[ -n "$TAGS" ]] && ARGS+=(--tags "$TAGS")
(( ASK_BECOME )) && ARGS+=(--ask-become-pass)
if [[ -n "$VAULT_FILE" ]]; then check_secret_file "$VAULT_FILE"; ARGS+=(--vault-password-file "$VAULT_FILE"); fi
if [[ -n "$EXTRA_FILE" ]]; then
  [[ -f "$EXTRA_FILE" ]] || die "$E_USAGE" "нет файла $EXTRA_FILE"
  if grep -Eiq '^\s*[a-z_]*(password|secret|token|key)\s*:\s*[^{ ]' "$EXTRA_FILE"; then
    die "$E_USAGE" "$EXTRA_FILE содержит секреты в открытом виде — используйте Ansible Vault"
  fi
  ARGS+=(-e "@$EXTRA_FILE")
fi

# Архив приложения и локальные пакеты комплекта (если есть).
if [[ -z "${FPV_APP_ARCHIVE:-}" ]]; then
  # shellcheck disable=SC2012
  archive="$(ls -1 "$ROOT"/app/fpv-app-*.tar.gz 2>/dev/null | tail -1 || true)"
  [[ -n "$archive" ]] && export FPV_APP_ARCHIVE="$archive"
fi
[[ -z "${FPV_PIP_WHEELHOUSE:-}" && -d "$ROOT/packages/python/wheels" ]] && export FPV_PIP_WHEELHOUSE="$ROOT/packages/python/wheels"

export ANSIBLE_CONFIG="$ROOT/ansible.cfg"
export ANSIBLE_COLLECTIONS_PATH="$ROOT/collections"
export ANSIBLE_LOG_PATH="${ANSIBLE_LOG_PATH:-$ROOT/logs/ansible.log}"
mkdir -p "$(dirname "$ANSIBLE_LOG_PATH")"
cd "$ROOT"
log "запуск: playbook=$PLAYBOOK mode=$MODE limit=$LIMIT check=$CHECK inventory=$INVENTORY"
trap - ERR      # код завершения ansible-playbook обрабатывается явно ниже
set +e
"$VENV/bin/ansible-playbook" "${ARGS[@]}"
rc=$?
set -e
if (( rc != 0 )); then die "$E_ANSIBLE" "playbook $PLAYBOOK завершился с кодом $rc"; fi
log "playbook $PLAYBOOK выполнен успешно"
