#!/bin/bash
# Создание (обновление) плоского apt-репозитория из .deb-файлов, например в /srv/packages.
#
#   packaging/debian/make-apt-repo.sh /srv/packages --key "FPV Local Repo" [пакеты.deb ...]
#   packaging/debian/make-apt-repo.sh /srv/packages --unsigned [пакеты.deb ...]   # только для стенда
#
# Формирует Packages, Packages.gz, Release, InRelease, Release.gpg и открытый ключ
# fpv-local.gpg (его копируют на клиенты в /usr/share/keyrings/fpv-local.gpg).
set -Eeuo pipefail
die() { echo "make-apt-repo: ОШИБКА: $*" >&2; exit 1; }

REPO_DIR="${1:-}"; shift || true
[[ -n "$REPO_DIR" ]] || die "укажите каталог репозитория, например /srv/packages"
KEY="" UNSIGNED=0 DEBS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --key) KEY="${2:-}"; shift 2 ;;
    --unsigned) UNSIGNED=1; shift ;;
    *.deb) DEBS+=("$1"); shift ;;
    *) die "неизвестный параметр: $1" ;;
  esac
done
[[ -n "$KEY" || $UNSIGNED -eq 1 ]] || die "укажите --key <ключ GPG> или явно --unsigned"
command -v dpkg-scanpackages >/dev/null || die "нет dpkg-scanpackages (apt install dpkg-dev)"

mkdir -p "$REPO_DIR"
for d in "${DEBS[@]}"; do
  dpkg-deb --info "$d" >/dev/null || die "повреждённый пакет: $d"
  cp -f "$d" "$REPO_DIR/"
done
cd "$REPO_DIR"
ls ./*.deb >/dev/null 2>&1 || die "в $REPO_DIR нет .deb-файлов"

dpkg-scanpackages --multiversion . /dev/null 2>/dev/null > Packages
gzip -9nkf Packages
{
  echo "Origin: FPV"
  echo "Label: FPV local packages"
  echo "Suite: fpv"
  echo "Codename: fpv"
  echo "Date: $(LC_ALL=C date -Ru)"
  echo "Architectures: amd64 all"
  echo "Description: Локальный репозиторий пакетов платформы FPV-доставки (Debian 13)"
  for algo in MD5Sum:md5sum SHA256:sha256sum; do
    echo "${algo%%:*}:"
    for f in Packages Packages.gz; do
      printf ' %s %s %s\n' "$("${algo##*:}" "$f" | cut -d' ' -f1)" "$(stat -c %s "$f")" "$f"
    done
  done
} > Release

rm -f InRelease Release.gpg
if [[ -n "$KEY" ]]; then
  gpg --batch --yes --local-user "$KEY" --clearsign -o InRelease Release
  gpg --batch --yes --local-user "$KEY" --armor --detach-sign -o Release.gpg Release
  gpg --batch --yes --export "$KEY" > fpv-local.gpg
  echo "подписано ключом: $KEY; открытый ключ: $REPO_DIR/fpv-local.gpg"
else
  echo "ВНИМАНИЕ: репозиторий не подписан (клиентам нужен параметр Trusted: yes)"
fi
# apt читает file:-репозитории от имени пользователя _apt
chmod -R a+rX "$REPO_DIR"
echo "пакетов в репозитории: $(grep -c '^Package:' Packages)"
