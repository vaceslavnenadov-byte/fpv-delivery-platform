#!/bin/bash
# Сборка установочного комплекта для копирования в /tmp/ancible на целевой узел (или управляющий узел).
# Результат: dist/fpv-ancible-<версия>.tar.gz + .sha256. Секреты в комплект не включаются.
FPV_SCRIPT=build-bundle
source "$(dirname "$0")/lib.sh"
trap 'on_error $LINENO' ERR

usage() {
  cat <<USAGE
Использование: $0 --packages-dir PATH [--out DIR] [--build-frontend]
  --packages-dir   рабочая копия репозитория fpv-delivery-packages на теге, указанном в fpv_packages_ref
  --out            каталог результата (по умолчанию <repo>/build)
  --build-frontend собрать frontend (npm ci && npm run build) перед упаковкой
USAGE
}
PKG="" OUT="" BUILD_FE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --packages-dir) PKG="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --build-frontend) BUILD_FE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "$E_USAGE" "неизвестный параметр: $1" ;;
  esac
done
require_cmd tar sha256sum
ANS="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ANS/../.." && pwd)"
OUT="${OUT:-$REPO/build}"
[[ -d "$PKG" && -f "$PKG/VERSION" ]] || die "$E_USAGE" "укажите --packages-dir (каталог fpv-delivery-packages с файлом VERSION)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO/backend/fpv/__init__.py")"
PKG_VERSION="$(cat "$PKG/VERSION")"
EXPECTED_PKG="$(sed -n 's/^fpv_packages_ref: "\(.*\)"/\1/p' "$ANS/group_vars/all.yml")"
[[ "$PKG_VERSION" == "$EXPECTED_PKG" ]] || die "$E_INTEGRITY" "версия пакетов $PKG_VERSION не совпадает с fpv_packages_ref=$EXPECTED_PKG"
(cd "$PKG" && sha256sum --quiet --strict -c SHA256SUMS) || die "$E_INTEGRITY" "контрольные суммы fpv-delivery-packages не совпадают"

if (( BUILD_FE )); then (cd "$REPO/frontend" && npm ci && npm run build); fi
[[ -f "$REPO/frontend/dist/index.html" ]] || die "$E_DEPS" "frontend не собран (frontend/dist); используйте --build-frontend"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"; on_error $LINENO' ERR
trap 'rm -rf "$STAGE"' EXIT
B="$STAGE/ancible"
mkdir -p "$B/app" "$B/packages" "$B/logs"
log "упаковка приложения $VERSION"
tar -C "$REPO" --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
    --sort=name --owner=0 --group=0 --numeric-owner --mtime='@0' \
    -czf "$B/app/fpv-app-$VERSION.tar.gz" backend/fpv backend/requirements.lock database drone-agent frontend/dist
log "копирование Ansible"
cp -a "$ANS/ansible.cfg" "$ANS/requirements.txt" "$ANS/requirements.yml" "$ANS/playbooks" "$ANS/roles" \
      "$ANS/inventory" "$ANS/group_vars" "$ANS/host_vars" "$ANS/filter_plugins" "$B/"
cp -a "$ANS"/scripts/*.sh "$B/"
rm -f "$B/build-bundle.sh"
cp -a "$REPO/docs/deployment.md" "$B/INSTALL.md"
log "копирование артефактов fpv-delivery-packages $PKG_VERSION"
cp -a "$PKG/debian" "$PKG/ansible" "$PKG/VERSION" "$B/packages/"
[[ -d "$PKG/python" ]] && cp -a "$PKG/python" "$B/packages/"
find "$B" -name '*.vault' -o -name 'vault.yml' | grep -q . && die "$E_INTEGRITY" "в комплект попали файлы Vault — удалите их"
printf 'fpv-delivery-platform %s\nfpv-delivery-packages %s\nbuilt %s\n' "$VERSION" "$PKG_VERSION" "$(date -u +%FT%TZ)" > "$B/VERSION"
(cd "$B" && find . -type f ! -path './logs/*' -print0 | sort -z | xargs -0 sha256sum > "$STAGE/SHA256SUMS" && mv "$STAGE/SHA256SUMS" SHA256SUMS)
mkdir -p "$OUT"
NAME="fpv-ancible-$VERSION"
tar -C "$STAGE" --sort=name --owner=0 --group=0 --numeric-owner -czf "$OUT/$NAME.tar.gz" ancible
(cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
log "комплект: $OUT/$NAME.tar.gz"
log "копирование на узел: scp $NAME.tar.gz{,.sha256} узел:/tmp/ && ssh узел 'cd /tmp && sha256sum -c $NAME.tar.gz.sha256 && tar -xzf $NAME.tar.gz'  # → /tmp/ancible"
