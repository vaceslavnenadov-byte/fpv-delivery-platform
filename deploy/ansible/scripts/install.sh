#!/bin/bash
# Bootstrap установочного комплекта (/tmp/ancible): проверка целостности и окружения,
# создание Python venv, установка закреплённых зависимостей и коллекций, запуск playbook.
# /tmp используется только как staging: постоянные файлы размещаются ролями в /opt, /etc, /var.
FPV_SCRIPT=install
source "$(dirname "$0")/lib.sh"

usage() {
  cat <<USAGE
Использование: $0 --mode local|central --inventory PATH --playbook NAME [параметры]
  --mode local|central       режим запуска (обязательно, неявное смешение режимов запрещено)
  --inventory PATH           inventory (обязательно)
  --playbook NAME            $FPV_ALLOWED_PLAYBOOKS
  --limit HOSTS              цели для режима central (обязательно в central)
  --check                    проверка без изменений
  --offline                  установка без внешней сети: wheels и collections из packages/ комплекта
  --install-system-deps      установить минимальные пакеты (python3-venv, python3-pip) через apt перед созданием venv
  --venv DIR                 каталог venv (по умолчанию <комплект>/venv)
  --vault-password-file F    файл пароля Ansible Vault (права 600)
  --extra-vars-file F        YAML с переменными (без секретов)
  --prepare-only             только подготовить venv и коллекции, playbook не запускать
USAGE
}

MODE="" INVENTORY="" PLAYBOOK="" LIMIT="" CHECK=0 OFFLINE=0 SYSDEPS=0 VENV="" VAULT_FILE="" EXTRA_FILE="" PREPARE_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --inventory) INVENTORY="${2:-}"; shift 2 ;;
    --playbook) PLAYBOOK="${2:-}"; shift 2 ;;
    --limit) LIMIT="${2:-}"; shift 2 ;;
    --check) CHECK=1; shift ;;
    --offline) OFFLINE=1; shift ;;
    --install-system-deps) SYSDEPS=1; shift ;;
    --venv) VENV="${2:-}"; shift 2 ;;
    --vault-password-file) VAULT_FILE="${2:-}"; shift 2 ;;
    --extra-vars-file) EXTRA_FILE="${2:-}"; shift 2 ;;
    --prepare-only) PREPARE_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "$E_USAGE" "неизвестный параметр: $1" ;;
  esac
done

ROOT="$(bundle_root)"
VENV="${VENV:-$ROOT/venv}"
in_list "$MODE" "$FPV_ALLOWED_MODES" || { usage >&2; die "$E_USAGE" "укажите --mode local|central"; }
if (( ! PREPARE_ONLY )); then
  in_list "$PLAYBOOK" "$FPV_ALLOWED_PLAYBOOKS" || { usage >&2; die "$E_USAGE" "недопустимый playbook: '$PLAYBOOK'"; }
  [[ -n "$INVENTORY" ]] || die "$E_USAGE" "укажите --inventory"
fi
[[ "$VENV" == /* ]] || die "$E_USAGE" "--venv должен быть абсолютным путём"

start_log "$ROOT/logs" install
trap 'on_error $LINENO' ERR
log "комплект: $ROOT, режим: $MODE, venv: $VENV, offline: $OFFLINE"

# 1. Целостность комплекта
if [[ -f "$ROOT/SHA256SUMS" ]]; then
  log "этап 1/8: проверка контрольных сумм комплекта"
  (cd "$ROOT" && sha256sum --quiet --strict -c SHA256SUMS) || die "$E_INTEGRITY" "контрольные суммы не совпадают — комплект повреждён или изменён"
else
  [[ "$ROOT" == /tmp/ancible* ]] && die "$E_INTEGRITY" "нет SHA256SUMS в комплекте $ROOT"
  warn "SHA256SUMS отсутствует (запуск из рабочей копии репозитория)"
fi

# 2. Предварительные проверки ОС, архитектуры, диска, прав
log "этап 2/8: предварительные проверки"
pf=(--mode "$MODE")
(( OFFLINE )) && pf+=(--offline)
[[ "$MODE" == "central" && -n "$INVENTORY" ]] && pf+=(--inventory "$INVENTORY")
"$(dirname "$0")/preflight.sh" "${pf[@]}" || die "$E_PREFLIGHT" "предварительная проверка не пройдена"

# 3. Python, venv, pip (минимальные системные пакеты — отдельным явным шагом)
log "этап 3/8: проверка Python и venv"
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  if (( SYSDEPS )); then
    log "установка минимальных системных пакетов: python3-venv python3-pip"
    if [[ "$(id -u)" -eq 0 ]]; then apt-get install -y --no-install-recommends python3-venv python3-pip
    else sudo apt-get install -y --no-install-recommends python3-venv python3-pip; fi
  else
    die "$E_DEPS" "нет python3-venv; повторите с --install-system-deps или установите пакет из локального зеркала"
  fi
fi

# 4. Виртуальное окружение
log "этап 4/8: виртуальное окружение $VENV"
if [[ ! -x "$VENV/bin/python" ]]; then python3 -m venv "$VENV"; else log "venv уже существует — используется повторно"; fi
PIP=("$VENV/bin/python" -m pip --disable-pip-version-check --no-input)

# 5. Python-зависимости (закреплённые версии с хешами)
log "этап 5/8: установка Python-зависимостей"
if (( OFFLINE )); then
  [[ -d "$ROOT/packages/python/wheels" ]] || die "$E_DEPS" "офлайн-режим: нет packages/python/wheels"
  "${PIP[@]}" install --require-hashes --no-index --find-links "$ROOT/packages/python/wheels" -r "$ROOT/requirements.txt"
else
  "${PIP[@]}" install --require-hashes -r "$ROOT/requirements.txt"
fi

# 6. Ansible collections
log "этап 6/8: Ansible collections"
COLL="$ROOT/collections/ansible_collections"
mkdir -p "$COLL"
if (( OFFLINE )); then
  CDIR="$ROOT/packages/ansible/collections"
  [[ -f "$CDIR/SHA256SUMS" ]] || die "$E_DEPS" "нет $CDIR/SHA256SUMS"
  (cd "$CDIR" && sha256sum --quiet --strict -c SHA256SUMS) || die "$E_INTEGRITY" "контрольные суммы коллекций не совпадают"
  for tarball in "$CDIR"/*.tar.gz; do
    name="$(basename "$tarball" .tar.gz)"           # ansible-posix-1.5.4
    ns="${name%%-*}"; rest="${name#*-}"; coll="${rest%-*}"; ver="${rest##*-}"
    dest="$COLL/$ns/$coll"
    if [[ -f "$dest/MANIFEST.json" ]] && grep -q "\"version\": \"$ver\"" "$dest/MANIFEST.json"; then
      log "коллекция $ns.$coll $ver уже установлена"
      continue
    fi
    rm -rf "$dest"; mkdir -p "$dest"
    tar -xzf "$tarball" -C "$dest" --no-same-owner
    log "установлена коллекция $ns.$coll $ver (офлайн)"
  done
else
  "$VENV/bin/ansible-galaxy" collection install -r "$ROOT/requirements.yml" -p "$ROOT/collections"
fi

# 7. Проверка версий
log "этап 7/8: проверка версий"
"$VENV/bin/python" - <<'PY'
import sys
import ansible
from ansible.release import __version__
assert sys.version_info >= (3, 12), "Python >= 3.12"
assert __version__.startswith("2.21."), "ожидается ansible-core 2.21.x, найдено " + __version__
print("python", sys.version.split()[0], "ansible-core", __version__)
PY
ANSIBLE_COLLECTIONS_PATH="$ROOT/collections" "$VENV/bin/ansible-galaxy" collection list ansible.posix -p "$ROOT/collections" >/dev/null \
  || die "$E_DEPS" "коллекция ansible.posix недоступна"
ANSIBLE_COLLECTIONS_PATH="$ROOT/collections" "$VENV/bin/ansible-galaxy" collection list community.postgresql -p "$ROOT/collections" >/dev/null \
  || die "$E_DEPS" "коллекция community.postgresql недоступна"

if (( PREPARE_ONLY )); then
  log "подготовка завершена (--prepare-only), playbook не запускался"
  exit 0
fi

# 8. Запуск playbook
log "этап 8/8: запуск playbook $PLAYBOOK"
rp=(--mode "$MODE" --inventory "$INVENTORY" --playbook "$PLAYBOOK" --venv "$VENV")
[[ -n "$LIMIT" ]] && rp+=(--limit "$LIMIT")
(( CHECK )) && rp+=(--check)
[[ -n "$VAULT_FILE" ]] && rp+=(--vault-password-file "$VAULT_FILE")
[[ -n "$EXTRA_FILE" ]] && rp+=(--extra-vars-file "$EXTRA_FILE")
ANSIBLE_LOG_PATH="$ROOT/logs/ansible.log" "$(dirname "$0")/run-playbook.sh" "${rp[@]}"
if [[ "$(id -u)" -eq 0 ]]; then
  install -d -m 0750 /var/log/fpv/ansible && install -m 0600 "$FPV_LOG_FILE" /var/log/fpv/ansible/
  log "журнал сохранён в /var/log/fpv/ansible/"
fi
log "установка завершена успешно"
