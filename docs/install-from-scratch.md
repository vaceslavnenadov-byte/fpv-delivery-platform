# Установка платформы FPV на Debian 13 с нуля

Пошаговая инструкция для одной машины (виртуальная машина VirtualBox или сервер): платформа,
база данных, веб-сайт, вход пользователей, парк дронов с симулятором и проведение заказа.
Все сторонние программы ставятся командами из этого гайда (шаг 1); учтены проблемы, встреченные
при первых запусках (таблица в шаге 13).

Результат: сайт `https://fpv.dc.fpv.local` открывается в браузере; техник создаёт парк с базой
на карте, заводит и подключает дрон (симулятор), проверяет видео; заказчик оформляет заказ
А (база) → Б (забор груза) → В (получатель); техник подтверждает готовность, оператор выполняет
заказ.

---

## 0. Правила, которые экономят время

| Правило | Почему |
|---|---|
| Системные команды (`apt`, `systemctl`, файлы в `/etc`, `/srv`) — **от root** | иначе «Permission denied» |
| Браузер, ярлыки, всё графическое — **от обычного пользователя** (`vboxuser`) | Firefox от root не запускается: «Running Firefox as root… is not supported» |
| Команды вставлять **блоками по одному шагу** и смотреть на результат | ошибка в середине блока иначе незаметна |
| Пути к скриптам — **полные** (`/opt/fpv-src/...`) | относительный путь работает только из нужной папки: «No such file or directory» |
| Файл `.desktop` не запускают через `./` | это не скрипт: «[Desktop: команда не найдена» |
| Вместо `sudo -u` — `runuser -u` | в Debian с паролем root `sudo` по умолчанию не установлен |
| Становиться root только через **`su -`** (с минусом) | `su` без минуса не загружает окружение root: в `PATH` нет `/usr/sbin`, и `runuser`, `usermod`, `update-ca-certificates` дают «команда не найдена» |

Как стать root и вернуться обратно:

```bash
su -          # ввести пароль root; приглашение станет root@...#  (обязательно с минусом)
exit          # вернуться в vboxuser
whoami        # проверить, кто вы сейчас
echo $PATH    # у root должен быть /usr/sbin; если нет — exit и снова su -
```

В инструкции перед каждым блоком указано: **[root]**, **[vboxuser]** или
**[хост Windows, PowerShell]**.

---

## 1. Сторонние программы

### 1.1. Что и зачем ставится

| Программа | Пакет / источник | Где | Зачем |
|---|---|---|---|
| Git, curl, GnuPG, OpenSSL, сертификаты УЦ | `git curl gnupg openssl ca-certificates` | ВМ | загрузка проекта, ключ подписи репозитория, сертификат сайта |
| Инструменты apt-репозитория | `dpkg-dev apt-utils` | ВМ | `dpkg-scanpackages`, `apt-ftparchive` для локального репозитория |
| Python 3.13 и venv | `python3 python3-venv` | ВМ | среда выполнения платформы и сервисного модуля дрона |
| PostgreSQL 17 | `postgresql` | ВМ | база данных платформы |
| nginx | `nginx` | ВМ | веб-сайт по HTTPS, прокси к API и WebSocket |
| FFmpeg | `ffmpeg` | ВМ | трансляция видео (перекодирование в H.264, кадры), видео сервисного модуля, H.264 в Firefox |
| Firefox ESR | `firefox-esr` | ВМ | браузер для работы с платформой |
| Утилиты рабочего стола | `xdg-utils xdg-user-dirs libglib2.0-bin` | ВМ | ярлыки клиентов (`fpv-shortcuts`) |
| Утилиты видеоустройств | `v4l-utils` | ВМ | проверка USB-камеры (`v4l2-ctl`) |
| Samba AD, Kerberos | `samba winbind krb5-user smbclient` | ВМ | только для варианта А входа — настоящий домен (шаг 6) |
| Сборка дополнений гостевой ОС | `build-essential dkms linux-headers-$(uname -r) bzip2` | ВМ | только для VirtualBox Guest Additions (буфер обмена, шаг 1.4) |
| VirtualBox Extension Pack | download.virtualbox.org | хост Windows | только если нужна USB-камера хоста в ВМ (шаг 1.5) |

### 1.2. Подготовка системы

**[root]**

```bash
cat /etc/debian_version                 # 13.x — нужен Debian 13 «trixie»
timedatectl set-ntp true                # точное время: иначе ошибки TLS и подписи apt
timedatectl | grep synchronized         # System clock synchronized: yes
apt update
```

Если `apt update` пишет об ошибках зеркала — проверьте доступ в интернет и файл
`/etc/apt/sources.list.d/debian.sources` (должны быть `trixie` и компоненты `main`).

### 1.3. Установка всех программ одной командой

**[root]**

```bash
apt install -y \
    git curl gnupg openssl ca-certificates \
    dpkg-dev apt-utils \
    python3 python3-venv \
    postgresql nginx ffmpeg \
    firefox-esr xdg-utils xdg-user-dirs libglib2.0-bin \
    v4l-utils

systemctl enable --now postgresql nginx
```

Проверка версий:

```bash
python3 --version                       # Python 3.13.x
pg_lsclusters                           # 17  main  5432  online
nginx -v                                # nginx version: nginx/1.26.x или новее
ffmpeg -hide_banner -version | head -1  # ffmpeg version 7.x
firefox-esr --version                   # Mozilla Firefox …esr
dpkg-scanpackages --version | head -1   # есть — значит dpkg-dev установлен
```

Пакеты Samba для варианта А ставятся в шаге 6 — на стенде без домена они не нужны.

### 1.4. Буфер обмена между Windows и ВМ (VirtualBox Guest Additions, по желанию)

Без дополнений команды из гайда нельзя вставить в ВМ из браузера на Windows.

**[root]** в ВМ:

```bash
apt install -y build-essential dkms linux-headers-$(uname -r) bzip2
```

В меню окна ВМ: **«Устройства» → «Подключить образ диска Дополнений гостевой ОС»**, затем:

```bash
mount /dev/cdrom /mnt
sh /mnt/VBoxLinuxAdditions.run          # «VirtualBox Guest Additions: ... modules» без ошибок
umount /mnt
reboot
```

После перезагрузки: **«Устройства» → «Общий буфер обмена» → «Двунаправленный»**.

### 1.5. USB-камера хоста в ВМ (по желанию)

Нужна, только если видео дрона будет идти с веб-камеры ноутбука/ПК (шаг 10.1). Для проверки без
камеры достаточно тестового ролика или тестовой таблицы.

**[хост Windows, PowerShell от администратора]** — Extension Pack той же версии, что VirtualBox:

```powershell
$vb  = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
$ver = (& $vb --version) -replace 'r.*',''                 # например 7.2.20
Invoke-WebRequest "https://download.virtualbox.org/virtualbox/$ver/Oracle_VirtualBox_Extension_Pack-$ver.vbox-extpack" `
    -OutFile "$env:TEMP\extpack.vbox-extpack"
& $vb extpack install --replace "$env:TEMP\extpack.vbox-extpack"   # согласиться с лицензией: y
& $vb list extpacks                                          # Extension Packs: 1, Version: тот же
& $vb list webcams                                           # .1 "Integrated Camera" ...
```

Для VirtualBox 7.0 и старше имя файла — `Oracle_VM_VirtualBox_Extension_Pack-$ver.vbox-extpack`.
Extension Pack бесплатен для личного и учебного использования (лицензия PUEL).

Если `list webcams` пуст — Windows не видит камеру или не даёт доступ к ней: «Параметры» →
«Конфиденциальность и защита» → «Камера» → включить доступ, в том числе для классических
приложений; закрыть Teams/Zoom/«Камеру». Проверка камеры в Windows:
`Get-PnpDevice -Class Camera,Image | Format-Table Status,FriendlyName`.

Подключение камеры к **работающей** ВМ (повторять после каждого запуска ВМ; имя ВМ — из
`& $vb list runningvms`):

```powershell
& $vb controlvm "Имя ВМ" webcam attach .1
```

**[root]** в ВМ:

```bash
v4l2-ctl --list-devices                 # VirtualBox Webcam: /dev/video0
ls -l /dev/video*
```

---

## 2. Исходные файлы проекта

**[root]** Оба репозитория — в `/opt`, ветка `claude/complex-system-development-ucubxt`
(в ней пакеты Debian и скрипты сборки репозитория).

```bash
cd /opt
git clone -b claude/complex-system-development-ucubxt \
    https://github.com/vaceslavnenadov-byte/fpv-delivery-platform fpv-src
git clone -b claude/complex-system-development-ucubxt \
    https://github.com/vaceslavnenadov-byte/fpv-delivery-packages fpv-packages

ls /opt/fpv-packages/debian/fpv/
# fpv-desktop_0.1.0-25_all.deb  fpv-drone-agent_0.1.0-25_all.deb
# fpv-platform_0.1.0-25_amd64.deb  SHA256SUMS
(cd /opt/fpv-packages/debian/fpv && sha256sum -c SHA256SUMS)     # все три: OK
```

Три пакета:

| Пакет | Куда ставить | Что внутри |
|---|---|---|
| `fpv-platform` | сервер | API, воркер, веб-интерфейс (отдельные клиенты по ролям), миграции, сайт nginx |
| `fpv-drone-agent` | компьютер дрона (здесь — та же ВМ, симулятор) | сервисный модуль дрона: телеметрия и видео (USB/RTSP/ролик) |
| `fpv-desktop` | рабочее место пользователя (здесь — та же ВМ) | команда `fpv-shortcuts` и `/etc/fpv-desktop.conf` |

Репозиторий `vaceslavnenadov-byte/dir` — старая общая копия до разделения, в ней нет текущих
изменений; используйте два репозитория выше.

Если репозиторий приватный и git спрашивает пароль — пароль GitHub не подойдёт, нужен токен:
GitHub → Settings → Developer settings → Personal access tokens → Fine-grained → доступ
Contents: Read к обоим репозиториям. При запросе пароля вставить токен.

---

## 3. Локальный подписанный apt-репозиторий `/srv/packages`

apt устанавливает пакеты только из репозитория, которому доверяет. Репозиторий — папка с `.deb`
и индексом (`Packages`, `Release`), индекс подписан вашим GPG-ключом; открытый ключ кладётся
в `/usr/share/keyrings/`, и apt проверяет по нему подпись.

**[root]**

```bash
# 3.1. Ключ подписи — один раз. Если ключ уже есть, второй создавать не нужно.
gpg --list-keys "FPV Local Repo" >/dev/null 2>&1 || \
  gpg --batch --passphrase '' --quick-gen-key "FPV Local Repo" rsa4096 sign 3y

# 3.2. Репозиторий: копирование .deb, индекс, подпись, открытый ключ
/opt/fpv-src/packaging/debian/make-apt-repo.sh /srv/packages --key "FPV Local Repo" \
    /opt/fpv-packages/debian/fpv/*.deb

ls /srv/packages
# InRelease  Packages  Packages.gz  Release  Release.gpg  fpv-local.gpg  *.deb

# 3.3. Доверие apt к ключу и источник пакетов
install -m 0644 /srv/packages/fpv-local.gpg /usr/share/keyrings/fpv-local.gpg
cp /opt/fpv-src/packaging/debian/fpv-local.sources /etc/apt/sources.list.d/fpv-local.sources
cat /etc/apt/sources.list.d/fpv-local.sources
# Types: deb
# URIs: file:/srv/packages
# Suites: ./
# Signed-By: /usr/share/keyrings/fpv-local.gpg

apt update                         # без «NO_PUBKEY» и «is not signed»
apt policy fpv-platform fpv-drone-agent fpv-desktop   # Кандидат у всех: 0.1.0-25, источник file:/srv/packages
```

---

## 4. Установка пакетов платформы

**[root]**

```bash
apt install -y fpv-platform fpv-drone-agent fpv-desktop
```

На отдельных машинах ставится только своё: сервер — `fpv-platform`, компьютер дрона —
`fpv-drone-agent` (плюс `ffmpeg`), рабочее место пользователя — `fpv-desktop` (плюс `firefox-esr`),
подключив на каждой машине тот же репозиторий из шага 3.3.

`fpv-platform` создаёт пользователя `fpv`, каталоги, виртуальное окружение Python, службы `fpv-api`
и `fpv-worker`. Предупреждение о том, что база данных не настроена, на этом шаге нормально.
`fpv-drone-agent` создаёт службу `fpv-agent`, папку тестовых роликов `/var/lib/fpv-agent/videos`
(с примером `example-test.mp4`) и файл RTSP-камер `/etc/fpv-agent/cameras.conf`.

---

## 5. База данных

Без этого шага `fpv-api` работает, но каждый запрос завершается ошибкой
(«connection to server at 127.0.0.1, port 5432 failed»), а страница входа показывает
`JSON.parse: unexpected character` или «Сервер приложения недоступен».

**[root]** (вошли через `su -`; проверка: `command -v runuser` → `/usr/sbin/runuser`)

```bash
command -v runuser || echo "ОСТАНОВИТЕСЬ: выполните exit и войдите как root через su -"
PW=$(openssl rand -hex 16)          # случайный пароль; хранится только в файле секрета
runuser -u postgres -- psql -c "CREATE ROLE fpv_app LOGIN PASSWORD '$PW'"
runuser -u postgres -- psql -c "CREATE DATABASE fpv OWNER fpv_app"

printf '%s' "postgresql+psycopg://fpv_app:$PW@127.0.0.1:5432/fpv" > /etc/fpv/secrets/database_url
chown root:fpv /etc/fpv/secrets/database_url
chmod 0640 /etc/fpv/secrets/database_url

fpv-migrate                         # Применены: 0001_core_schema.sql … 0007_fleets.sql
systemctl restart fpv-api fpv-worker
systemctl is-active fpv-api fpv-worker      # active  active
```

Если роль или база уже существуют (повторная установка) — `psql` сообщит «already exists»;
тогда задайте пароль заново: `runuser -u postgres -- psql -c "ALTER ROLE fpv_app PASSWORD '$PW'"`.

---

## 6. Вход пользователей: выберите вариант

Пользователи создаются **не в платформе**, а в службе каталогов (домене). Роль определяется
группой пользователя в домене; профиль в платформе создаётся при первом входе.

| Группа в домене | Роль в платформе |
|---|---|
| `FPV-Customers` | заказчик |
| `FPV-Operators` | оператор |
| `FPV-Coordinators` | наводчик / координатор |
| `FPV-Technicians` | техник — ведёт свой парк дронов |
| `FPV-Recruiters` | рекрутер — добавляет пользователей, выдаёт роли, проверяет профили |
| `FPV-Admins` | администратор |

### Вариант Б — быстрый просмотр без домена (рекомендуется для первого запуска)

Режим разработки со встроенным эмулятором каталога и готовыми пользователями.
В production не допускается.

**[root]**

```bash
sed -i 's/^FPV_ENVIRONMENT=.*/FPV_ENVIRONMENT=development/;
        s/^FPV_DIRECTORY_PROVIDER=.*/FPV_DIRECTORY_PROVIDER=static/' /etc/fpv/fpv.env
grep -q '^FPV_STATIC_DIRECTORY_FILE=' /etc/fpv/fpv.env || \
  echo 'FPV_STATIC_DIRECTORY_FILE=/var/lib/fpv/dev-directory.json' >> /etc/fpv/fpv.env

FPV_DEV_PASSWORD='Test12345!' fpv-cli dev-directory --out /var/lib/fpv/dev-directory.json
systemctl restart fpv-api fpv-worker
```

Пользователи (пароль у всех `Test12345!`): `admin`, `customer`, `operator`, `operator2`,
`coordinator`, `technician`, `recruiter`.

### Вариант А — настоящий домен Samba AD на этой же машине

Соответствует ТЗ. Меняет имя машины на `dc1`. Пароли замените своими (не короче 8 символов,
буквы разного регистра и цифры).

**[root]**

```bash
# А.1. Имя машины
hostnamectl set-hostname dc1
IP=$(hostname -I | awk '{print $1}')
sed -i '/^127\.0\.1\.1/d;/dc1/d' /etc/hosts
echo "$IP dc1.fpv.local dc1" >> /etc/hosts

# А.2. Установка Samba AD и Kerberos (сторонние пакеты варианта А)
DEBIAN_FRONTEND=noninteractive apt install -y samba winbind krb5-user smbclient
samba --version                          # Version 4.x
systemctl disable --now smbd nmbd winbind
mv /etc/samba/smb.conf /etc/samba/smb.conf.orig
samba-tool domain provision --realm=FPV.LOCAL --domain=FPV --server-role=dc \
  --dns-backend=SAMBA_INTERNAL --adminpass='Adm1n!Passw0rd'
cp /var/lib/samba/private/krb5.conf /etc/krb5.conf
systemctl unmask samba-ad-dc
systemctl enable --now samba-ad-dc

# А.3. Подразделения, группы ролей, служебная учётная запись платформы
samba-tool ou create "OU=FPV"
samba-tool ou create "OU=Service,OU=FPV"
samba-tool ou create "OU=Groups"
samba-tool ou create "OU=FPV,OU=Groups"
for g in FPV-Customers FPV-Operators FPV-Coordinators FPV-Technicians FPV-Recruiters FPV-Admins; do
  samba-tool group add "$g" --groupou="OU=FPV,OU=Groups"
done
samba-tool user create svc-fpv-ldap 'Svc!Passw0rd123' --userou="OU=Service,OU=FPV"
samba-tool user setexpiry svc-fpv-ldap --noexpiry

# А.4. Подключение платформы к домену
printf '%s' 'Svc!Passw0rd123' > /etc/fpv/secrets/ldap_bind_password
cp /var/lib/samba/private/tls/ca.pem /usr/local/share/ca-certificates/fpv-samba.crt
update-ca-certificates
systemctl restart fpv-api fpv-worker

# А.5. Пользователи (повторить для каждого; техник парка — в группе FPV-Technicians)
samba-tool user create ivanov 'Passw0rd!123' --given-name=Иван --surname=Иванов \
  --mail-address=ivanov@fpv.local
samba-tool group addmembers FPV-Admins ivanov
samba-tool user create petrov 'Passw0rd!123' --given-name=Пётр --surname=Петров
samba-tool group addmembers FPV-Technicians petrov
samba-tool user create sidorova 'Passw0rd!123' --given-name=Светлана --surname=Сидорова
samba-tool group addmembers FPV-Recruiters sidorova

# А.6. Новые сотрудники без доменной группы: только учётная запись в домене,
#      роли в платформе выдаёт рекрутер (раздел 9.0)
samba-tool user create novikov 'Passw0rd!123' --given-name=Иван --surname=Новиков
```

Управление: `samba-tool user list`, `samba-tool group listmembers FPV-Technicians`,
`samba-tool user disable ivanov` (доступ в платформу пропадает), `samba-tool user setpassword ivanov`.

Если до этого использовался вариант Б — верните в `/etc/fpv/fpv.env`
`FPV_ENVIRONMENT=production` и `FPV_DIRECTORY_PROVIDER=ldap`, затем
`systemctl restart fpv-api fpv-worker`.

### Проверка

```bash
curl -s http://127.0.0.1:8000/health/ready; echo
# {"status":"ok","checks":{"database":"ok","directory":"ok","storage":"ok"}}
```

---

## 7. Веб-сайт (nginx, HTTPS)

Веб-интерфейс лежит в `/usr/share/fpv-platform/frontend`, но открывать его как файл нельзя: страницы
работают только через nginx, который передаёт запросы API и WebSocket в `fpv-api`. Сайт отвечает на имя
`fpv.dc.fpv.local` и только по HTTPS (cookie входа — `Secure`); запросы с другим именем, в том
числе `http://localhost`, отклоняются.

**[root]**

```bash
# 7.1. Имя сайта указывает на эту машину
grep -q fpv.dc.fpv.local /etc/hosts || echo "127.0.0.1 fpv.dc.fpv.local" >> /etc/hosts

# 7.2. Самоподписанный сертификат (для стенда; в ЦОД — сертификат УЦ организации)
mkdir -p /etc/ssl/fpv
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -subj "/CN=fpv.dc.fpv.local" -addext "subjectAltName=DNS:fpv.dc.fpv.local" \
  -keyout /etc/ssl/private/fpv.key -out /etc/ssl/fpv/fpv.crt
chmod 600 /etc/ssl/private/fpv.key

# 7.3. Доверие к сертификату на этой машине (нужно симулятору дрона и curl)
cp /etc/ssl/fpv/fpv.crt /usr/local/share/ca-certificates/fpv-site.crt
update-ca-certificates

# 7.4. Включить сайт платформы, выключить стандартный сайт nginx
ln -sf /etc/nginx/sites-available/fpv-platform.conf /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# 7.5. Проверка
curl -sI https://fpv.dc.fpv.local | head -1          # HTTP/2 200
curl -s https://fpv.dc.fpv.local/health/ready; echo  # {"status":"ok",...}
grep -n "api/v1/ws" /etc/nginx/sites-available/fpv-platform.conf   # location ^~ /api/v1/ws
```

Firefox при первом открытии предупредит о самоподписанном сертификате:
«Дополнительно» → «Принять риск и продолжить».

---

## 8. Ярлыки клиентов на рабочем столе

Интерфейс разделён на самостоятельные клиенты по ролям (`docs/role-clients.md`) — у каждого
свой адрес, и в браузер загружается код только этой роли:

| Клиент | Адрес |
|---|---|
| Общий вход | `https://fpv.dc.fpv.local/` — после входа открывает раздел вашей роли |
| Заказчик | `https://fpv.dc.fpv.local/customer/` |
| Оператор | `https://fpv.dc.fpv.local/operator/` |
| Координатор | `https://fpv.dc.fpv.local/coordinator/` |
| Техник (парк дронов) | `https://fpv.dc.fpv.local/technician/` |
| Рекрутер | `https://fpv.dc.fpv.local/recruiter/` |
| Администратор | `https://fpv.dc.fpv.local/admin/` |

Команда `fpv-shortcuts` из пакета `fpv-desktop` (установлен в шаге 4) **одной командой**
создаёт ярлыки входа и всех клиентов на рабочем столе и в меню приложений.

**[root]** — проверить адрес сайта (общий для всех пользователей машины):

```bash
dpkg -L fpv-desktop | grep -E 'bin|etc'         # /usr/bin/fpv-shortcuts, /etc/fpv-desktop.conf
grep FPV_SITE_URL /etc/fpv-desktop.conf          # FPV_SITE_URL=https://fpv.dc.fpv.local
```

Если сайт на другом адресе — исправить строку в `/etc/fpv-desktop.conf` (файл не перезаписывается
при обновлении пакета).

**[vboxuser]** (от своего пользователя, не от root):

```bash
fpv-shortcuts                                   # все ярлыки, адрес из /etc/fpv-desktop.conf
```

Другие варианты:

```bash
fpv-shortcuts technician                        # только клиент техника
fpv-shortcuts --url https://fpv.dc.fpv.local    # указать адрес сайта
fpv-shortcuts --list                            # показать, что будет создано
fpv-shortcuts --remove                          # удалить ярлыки
runuser -u vboxuser -- fpv-shortcuts            # [root] создать ярлыки пользователю vboxuser
```

Запуск: двойной щелчок по значку (в GNOME при надписи «Не доверять» — правой кнопкой
→ «Разрешить запуск»). На общем входе достаточно одного ярлыка «FPV Доставка» — он сам откроет
нужный раздел после входа. Если у пользователя несколько ролей, переход между его клиентами —
блок «Другие разделы» в боковом меню.

---

## 9. Первые действия в интерфейсе: парк дронов

Имена — для варианта Б; в варианте А — ваши доменные пользователи из нужных групп.

Дроны заводит и подключает **техник** — администратор своего парка (`docs/fleets.md`). Заказчик
дроны не регистрирует: он выбирает дрон из парков, которые техники открыли для заказов.
Маршрут заказа: **А** — база парка → **Б** — где забрать груз → **В** — получатель.

### 9.0. Пользователи и роли (рекрутер)

Рекрутер (группа `FPV-Recruiters`, клиент `/recruiter/`) ведёт кадровую часть; у администратора
все эти права остаются.

* **recruiter** → «Пользователи и роли» → «Добавить пользователя»: доменный логин (например,
  `novikov` из шага А.6) и роли (заказчик, оператор, координатор, техник) → **«Добавить в систему»**.
  Учётная запись должна уже быть в домене; без роли в платформу войти нельзя.
* Там же — выдать роль («+ выдать роль…») или снять выданную вручную («×»). Роли из доменных
  групп меняются в домене (`samba-tool group addmembers/removemembers`). Роли администратора и
  рекрутера выдаёт только администратор.
* **recruiter** → «Проверка профилей»: пользователь заполнил профиль → **«✓ Подтвердить»** или
  **«Вернуть на доработку»** с причиной (пользователь видит её в профиле). Изменённый профиль снова
  уходит на проверку.

### 9.1. Допуск оператора

**operator** → «Профиль» → заполнить профиль и отправить **заявку на допуск оператора** (номер
свидетельства). **recruiter** → «Допуск операторов» → **«Допустить»** (или **admin** → «Пользователи
и роли» → «Заявки операторов»). До допуска оператор не видит очередь заказов. То же — для `operator2`.

### 9.2. Карта района

Карта нужна, чтобы техник поставил базу парка, а заказчик — точки Б и В перетаскиванием меток, а маршруты показывались на подложке.
Для стенда подойдёт тестовая карта-сетка.

**[vboxuser]** — тестовая карта (настоящая — снимок/схема района в PNG/JPEG/WebP):

```bash
ffmpeg -loglevel error -y -f lavfi \
  -i "color=c=0xf2efe9:s=1500x1430,drawgrid=width=100:height=100:thickness=1:color=0xc8c8c8,drawgrid=width=500:height=500:thickness=3:color=0x9aa5b1" \
  -frames:v 1 ~/test-map.png
ls -l ~/test-map.png
```

**admin** → «Карты» → загрузить `test-map.png` с границами: север `55.79`, юг `55.72`,
запад `37.55`, восток `37.68` (район примеров адресов в форме заказа). Подробно —
`docs/maps-and-live.md`.

### 9.3. Парк и база (точка А)

**technician** → «Мой парк» → «Создать парк»: название, **щелчок по карте** — там встанет метка **А**,
её можно **перетащить** (координаты подставятся сами, их можно уточнить вручную), адрес базы → «Создать парк».
База должна быть в зоне обслуживания. Перенести её позже — «📍 Изменить базу (точку А)»
(действует для новых заказов).

### 9.4. Дрон в парке

1. **technician** → «Мой парк» → «Завести дрон в парк»: серийный номер `SIM-0001`, учётный номер
   `RU-SIM-1`, производитель, модель, грузоподъёмность, дальность. Статус: «ожидает подтверждения».
2. **admin** → «Дроны» → **«Подтвердить»** регистрацию (в колонке «Парк» виден парк дрона).
3. **technician** → «Обслуживание» → дрон → технический осмотр: результат «Пройден», статус «Исправен».
4. **technician** → «Мой парк» → **«Выпустить ключ»** → скопировать ключ (показывается один раз) —
   он нужен в шаге 10.

### 9.5. Подключение дрона и открытие для заказов (после шага 10)

**technician** → «Мой парк» → «Подключение»:

* связь «На связи», телеметрия «поступает»;
* источник видео по умолчанию (тестовая таблица, ролик, USB или RTSP — §10.1) →
  «Сделать источником по умолчанию»;
* **«📹 Проверить видео»** — пробная трансляция с дрона (5 минут, не сохраняется), дождаться
  «В ЭФИРЕ»;
* **«Открыть для заказов»** — дрон появится у заказчиков (нужны подтверждённая регистрация,
  осмотр «Исправен» и выпущенный ключ).

---

## 10. Сервисный модуль дрона (симулятор)

Без него техник не увидит связь и видео, а заказ остановится на проверке готовности:
«Нет связи с сервисным модулем дрона (статус связи: offline)».

**[root]**

```bash
# вместо ВСТАВЬТЕ_КЛЮЧ — ключ из шага 9.4.4
printf '%s' 'ВСТАВЬТЕ_КЛЮЧ' > /etc/fpv-agent/key
chown fpv-agent:fpv-agent /etc/fpv-agent/key
chmod 600 /etc/fpv-agent/key            # иначе агент откажется читать ключ

systemctl enable --now fpv-agent
systemctl restart fpv-agent
journalctl -u fpv-agent -n 20 --no-pager # без 401 и ошибок SSL
```

Сценарий симулятора — `FPV_AGENT_SCENARIO` в `/etc/default/fpv-agent`:
`normal`, `fast`, `link-loss`, `link-blip`, `critical-error`, `no-video`, `faulty`;
после изменения — `systemctl restart fpv-agent`. Симулятор вылетает с базы парка (А), в точке Б
выполняет «Забор груза» (около 30 с) и летит к получателю (В).

Дальше — шаг 9.5 (подключение и проверка видео техником).

### 10.1. Видеопотоки дрона: USB, RTSP, тестовый ролик

Сервисный модуль сообщает платформе, какие источники видео у него есть. Источник по умолчанию
выбирает техник (шаг 9.5), во время заказа — оператор в мониторе полёта (кнопка **⚙**). Видео идёт
**как видео дрона**: пробная трансляция техника, главное окно монитора с телеметрией, трансляция
в карточке заказа, видеоподтверждение доставки.

| Источник | Откуда берётся |
|---|---|
| USB-камеры | камеры и платы захвата на машине с `fpv-agent` (`/dev/videoN`); в ВМ — проброшенная камера хоста (шаг 1.5) |
| RTSP-потоки | IP-камеры из `/etc/fpv-agent/cameras.conf`, строки `имя=rtsp://…`; в платформе видно только имя, адрес с паролем остаётся на модуле |
| Тестовые ролики | файлы `.mp4 .mkv .webm .mov .avi .ts .m4v` из `/var/lib/fpv-agent/videos`, воспроизводятся по кругу; при установке там создаётся `example-test.mp4` |
| Тестовая таблица | генерируется модулем; используется, если ничего не выбрано |

**[root]**

```bash
# Свой тестовый ролик (имя — латиница, цифры, точка, дефис, подчёркивание; без пробелов)
cp /home/vboxuser/Видео/flight.mp4 /var/lib/fpv-agent/videos/flight.mp4
chmod 644 /var/lib/fpv-agent/videos/flight.mp4

# RTSP-камера — ТОЛЬКО если она у вас есть; ЛОГИН, ПАРОЛЬ, АДРЕС, ПУТЬ — свои значения.
# Сначала проверка адреса — один кадр в файл (без ошибок появится /tmp/rtsp-test.jpg):
ffmpeg -rtsp_transport tcp -i 'rtsp://ЛОГИН:ПАРОЛЬ@АДРЕС:554/ПУТЬ' -frames:v 1 -y /tmp/rtsp-test.jpg
# Добавить один раз (повторный запуск строку не продублирует):
grep -q '^cam1=' /etc/fpv-agent/cameras.conf || \
  echo 'cam1=rtsp://ЛОГИН:ПАРОЛЬ@АДРЕС:554/ПУТЬ' >> /etc/fpv-agent/cameras.conf
grep -v '^#' /etc/fpv-agent/cameras.conf    # по одной строке на камеру, имена не повторяются

# USB-камера (шаг 1.5): проверка и источник по умолчанию
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --list-formats-ext | head -20       # поддерживаемые размеры
sed -i 's#^FPV_AGENT_VIDEO_DEVICE=.*#FPV_AGENT_VIDEO_DEVICE=/dev/video0#' /etc/default/fpv-agent

systemctl restart fpv-agent             # новые ролики и камеры подхватываются и сами в течение 30 с
journalctl -u fpv-agent -n 20 --no-pager # при видео: «видео: file:flight.mp4 (640x480, 15 к/с)»
```

Источники только читаются; команд дрону через них не передаётся. Камера открывается только на
время проверки видео или задания. Если в `/etc/default/fpv-agent` нет строк `FPV_AGENT_VIDEO_*`
(файл правили вручную до обновления), допишите их:

```
FPV_AGENT_VIDEO_DEVICE=
FPV_AGENT_VIDEO_DIR=/var/lib/fpv-agent/videos
FPV_AGENT_VIDEO_SIZE=640x480
FPV_AGENT_VIDEO_FPS=15
```

---

## 11. Проведение заказа

1. **customer** → «Новый заказ» → **дрон из открытых парков** (точка **А** — база его парка —
   подставляется сама и закреплена) → груз → на карте **перетащите метки Б** (где забрать груз) и
   **В** (куда доставить) или выберите точку кнопкой «Б»/«В» и щёлкните по карте; метку в фокусе можно
   сдвигать стрелками (Shift — крупный шаг). Координаты и адрес «Точка на карте (…)» подставятся сами —
   адрес можно уточнить, его ручной текст при перетаскивании сохраняется. Если по пути от Б к В
   нужно заехать ещё куда-то — **«+ Добавить точку»** (до 5 промежуточных точек 1, 2, …): метку
   перетащите на карте, укажите адрес и, при желании, что сделать; порядок меняется кнопками ↑/↓,
   ✕ — удалить. Расстояние, стоимость и проверки учитывают весь путь. Затем получатель, временное окно → «Проверить и рассчитать стоимость» (расстояние А→Б→В) →
   «Подтвердить заказ».
2. **operator** → очередь → принимает заказ.
3. **technician** → «Заказы на дронах парка» → заказ → проверяет подключение и видео
   («📹 Проверить видео») → **«✓ Подтвердить готовность дрона к полёту»**. Проверяется: регистрация,
   исправность и свежая диагностика, связь, телеметрия, заряд, грузоподъёмность, дальность
   (А→Б→В и возврат на базу), видео проверено за последние 12 ч. Без подтверждения техника
   оператор не пройдёт проверку готовности. До начала полёта техник может отозвать подтверждение
   (с причиной).
4. **operator** → «🛰 Открыть монитор полёта» (отдельное окно `/operator/orders/<id>/monitor`) →
   «Проверить готовность» → «▶ Начать задание». Дрон взлетает с базы А, в точке Б выполняет
   «Забор груза» (участникам приходит уведомление «груз забран»), затем летит к получателю В.
   Координатор назначается **автоматически** при принятии заказа — тот, у кого меньше всего
   сопровождаемых заказов (при равенстве — случайно), не больше 9 на одного; фоновая задача раз в
   30 с выравнивает нагрузку (разница между координаторами — не больше 1 заказа). Администратор
   может переназначить вручную.
   В мониторе — видео с телеметрией, карта, чат «💬 Связь по заказу» (заказчик, оператор,
   координатор), ⚙ — выбор видеопотока, «✓ Завершить доставку».
   **coordinator** → «Видеостена»: до 9 сопровождаемых заказов в одном окне (видео, фаза, высота,
   скорость, заряд, предупреждения); «⤢ Открыть» — монитор полёта этого заказа в новом окне.
5. **customer** — блок «📡 Трансляция с дрона» вверху карточки заказа: видео включается само,
   когда оператор начинает задание; в списке «Мои заказы» у такого заказа метка «● В ЭФИРЕ».
   Режим «Кадры» — для браузеров без H.264 и медленных каналов.
   Ниже — «💬 Связь по заказу»: заказчик переписывается с оператором и координатором с момента
   принятия заказа и до подтверждения получения.
   Переключатель темы (☀ светлая / ☾ тёмная / A как в системе) — в верхней панели любого клиента
   и на странице входа; выбор запоминается в браузере.
6. **Претензия.** Если заказчик нажал «Груз не получен» или «Открыть спор», заказ переходит в
   «Передан на рассмотрение спора». **coordinator** этого заказа → карточка заказа → блок
   «⚖ Претензия заказчика»:
   * **«Отклонить претензию»** (с причиной) — спор закрывается, заказ идёт дальше (на проверку
     администратора или в прежний итоговый статус);
   * **«Удовлетворить претензию»** (с обоснованием) — претензия передаётся администратору: ему
     приходит уведомление, в «Жалобы и споры» она стоит первой с пометкой решения координатора;
     окончательное решение и штраф по баллам — за **admin**.
7. После завершения: подтверждение получения заказчиком, баллы оператору (временное окно — 30,
   результат — 70, один раз).

---

## 12. Проверка состояния (шпаргалка)

**[root]**

```bash
systemctl is-active postgresql nginx fpv-api fpv-worker fpv-agent
curl -s http://127.0.0.1:8000/health/ready; echo
curl -sI https://fpv.dc.fpv.local | head -1
journalctl -u fpv-api -n 40 --no-pager
journalctl -u fpv-worker -n 40 --no-pager
journalctl -u fpv-agent -n 40 --no-pager
fpv-cli verify-audit                    # целостность журнала аудита
fpv-migrate --check                     # все миграции применены (до 0007_fleets.sql)
ls /var/lib/fpv-agent/videos            # тестовые ролики сервисного модуля
```

---

## 13. Известные проблемы и решения

| Симптом | Причина | Решение |
|---|---|---|
| `runuser: команда не найдена`, `fpv-cli: … exec: runuser: not found` | root получен командой `su` без минуса — нет `/usr/sbin` в `PATH` | `exit`, затем `su -` и повторить шаг целиком (шаг 5 — с первой строки; пакет 0.1.0-19+ вызывает `/usr/sbin/runuser` сам) |
| `sudo: команда не найдена` | в Debian с паролем root `sudo` не установлен | `su -` и команда от root, или `runuser -u ПОЛЬЗОВАТЕЛЬ -- команда`; либо `apt install sudo` |
| `make-apt-repo.sh: No such file or directory` | скрипт запущен по относительному пути, или клонирована ветка `main`, или репозиторий `dir` | шаг 2 (два репозитория, ветка `claude/complex-system-development-ucubxt`) и полный путь `/opt/fpv-src/...` |
| `cannot stat '/srv/packages/fpv-local.gpg'` | репозиторий не создан (предыдущая команда упала) | выполнить шаг 3.2 и проверить `ls /srv/packages` |
| `A key for "FPV Local Repo" already exists` | ключ уже создан | ничего делать не нужно, шаг 3.1 это учитывает |
| `dpkg-scanpackages: command not found` | нет `dpkg-dev` | шаг 1.3 |
| `apt update`: `NO_PUBKEY` / `is not signed` | ключ не скопирован в `/usr/share/keyrings` или репозиторий не подписан | шаг 3.3; после изменения пакетов — снова 3.2 |
| `Running Firefox as root … is not supported` | браузер запущен от root | `exit` в vboxuser; ярлык — шаг 8 от vboxuser |
| `./fpv-*.desktop: [Desktop: команда не найдена` | `.desktop` запущен как скрипт | двойной щелчок по значку или `gtk-launch fpv-technician` |
| `fpv-shortcuts: команда не найдена` | не установлен `fpv-desktop` | `apt install fpv-desktop` |
| Ярлыки открывают не тот адрес | в `/etc/fpv-desktop.conf` другой `FPV_SITE_URL` | исправить файл и снова `fpv-shortcuts`, или `fpv-shortcuts --url https://…` |
| `fpv-shortcuts: запустите от своего пользователя` | команда запущена от root | от своего пользователя или `runuser -u ПОЛЬЗОВАТЕЛЬ -- fpv-shortcuts` |
| Сайт не открывается / «соединение сброшено» | сайт nginx не включён, нет записи в `/etc/hosts`, нет сертификата, открыт `localhost` | шаг 7, адрес только `https://fpv.dc.fpv.local` |
| `nginx -t`: duplicate default server | включён стандартный сайт nginx | `rm /etc/nginx/sites-enabled/default` |
| Вход: `JSON.parse: unexpected character…` или «Сервер приложения недоступен» | `fpv-api` не может обратиться к БД | шаг 5; `journalctl -u fpv-api` |
| `/health/ready`: `directory_unavailable` | домен недоступен (вариант А не настроен) | вариант А или Б в шаге 6 |
| Вход: «Неверное имя пользователя или пароль» | нет такого пользователя в каталоге / неверный пароль | вариант Б: пароль из шага 6; вариант А: `samba-tool user list` |
| Вход проходит, но нет нужных разделов | пользователь не в группе роли | `samba-tool group addmembers FPV-… <имя>`, войти заново |
| Вверху долго «Переподключение…» | старый файл сайта nginx (WebSocket получал 404) | шаг 14, затем `nginx -t && systemctl reload nginx`; в файле сайта — `location ^~ /api/v1/ws` |
| Загрузка карты: «413 Request Entity Too Large» | старый файл сайта nginx | шаг 7.4 после обновления пакета; `nginx -t && systemctl reload nginx` |
| «Мой парк»: «Карт района нет» вместо карты | администратор не загрузил карту | шаг 9.2; или координаты базы вручную |
| Создание парка: «База парка должна быть в зоне обслуживания» | точка вне зоны обслуживания платформы | поставить базу ближе к району; зона — `FPV_SERVICE_AREA` в `/etc/fpv/fpv.env` |
| Карта не появляется под маршрутом | карта отключена или точки маршрута вне её границ | «Карты»: проверить границы (север > юг, восток > запад) и статус |
| Кнопки «Выпустить ключ» нет | дрон не подтверждён администратором | шаг 9.4.2 |
| «Новый заказ»: «Сейчас нет свободных дронов» | техник не открыл дрон для заказов или все дроны заняты | шаг 9.5; «Открыть для заказов» требует подтверждённой регистрации, осмотра «Исправен» и ключа |
| «Открыть для заказов»: «не выпущен ключ» / «не признан исправным» | не пройдены шаги 9.4.3–9.4.4 | осмотр «Исправен» и ключ |
| «Подключение»: «Нет связи», телеметрия не поступала | не запущен `fpv-agent` или неверный ключ | шаг 10; `journalctl -u fpv-agent` |
| «Проверить видео»: долго «Ожидание видео…» | модуль не на связи, сценарий `no-video`, источник недоступен | `journalctl -u fpv-agent`; сценарий `normal`; источник «Тестовая таблица» |
| Проверка готовности оператора: «Техник парка ещё не подтвердил готовность дрона» | техник не подтвердил готовность по этому заказу | **technician** → «Заказы на дронах парка» → заказ → «Подтвердить готовность дрона к полёту» |
| Подтверждение техника: «Видео с дрона не проверено» | не было пробной трансляции за последние 12 ч | там же: «📹 Проверить видео», дождаться «В ЭФИРЕ», повторить |
| «Дрон не входит в парк» | дрон зарегистрирован заказчиком до версии 0.1.0-17 | **admin** → «Дроны» → колонка «Парк» → выбрать парк; техник — шаги 9.4.3–9.5 |
| Оператор не видит очередь заказов | оператор не допущен администратором | шаг 9.1 |
| Трансляция: «Трансляция недоступна: не установлен ffmpeg» | нет ffmpeg на сервере | `apt install ffmpeg` |
| Видео не воспроизводится, кадры работают | браузер без H.264 | `apt install ffmpeg` на рабочем месте (Firefox берёт кодек из `libavcodec`) или режим «Кадры» |
| «Камера не найдена» при «Включить камеру» (заказчик) | браузер не видит камеру; в ВМ камера не проброшена | шаг 1.5; `v4l2-ctl --list-devices`; или камера телефона/ноутбука заказчика |
| «Доступ к камере запрещён» | браузер запретил камеру для сайта | значок слева от адреса → разрешить камеру → обновить страницу |
| `list webcams`: `Video Input Devices: 0` | Windows не даёт камеру VirtualBox | шаг 1.5: доступ к камере в параметрах Windows, закрыть другие программы |
| `VBoxManage.exe` «не распознано как имя командлета» | PowerShell открыт не в папке VirtualBox | полный путь, как в шаге 1.5 (`$vb = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"`) |
| В окне ⚙ нет USB-камеры | камера не подключена или не проброшена в ВМ | `v4l2-ctl --list-devices`; шаг 1.5; подождать 30 с или `systemctl restart fpv-agent` |
| В окне ⚙ или в «Подключении» нет списка источников, а видео идёт | до 0.1.0-20: модуль не отправлял список, если служба запущена в первые 5 минут после загрузки ОС; сервер отклонял весь список из‑за одной неверной записи (например, имя камеры дважды в `cameras.conf`) | обновить пакеты (шаг 14), `systemctl restart fpv-agent`; проверить `journalctl -u fpv-agent \| grep -i источник`; убрать повторы в `/etc/fpv-agent/cameras.conf` |
| В окне ⚙ нет ролика | неподходящее имя файла (пробелы, кириллица) или расширение | переименовать, например `flight-1.mp4` |
| Выбран RTSP, видео нет; агент: `видео rtsp:… остановилось` | камера недоступна, неверный адрес или пароль | проверка адреса командой `ffmpeg … -frames:v 1` из §10.1 |
| Агент: `видео usb:video0 остановилось: … Permission denied` / `Device or resource busy` | нет доступа к камере или её занял браузер | служба в группе `video` (пакет 0.1.0-14+); закрыть камеру в браузере на этой же машине |
| Агент: `видео usb:video0 остановилось: … Invalid argument` | камера не поддерживает заданный размер | размер из `v4l2-ctl --list-formats-ext` в `FPV_AGENT_VIDEO_SIZE` |
| Агент: `файл ключа доступен другим пользователям` | права ключа не 600 | `chmod 600 /etc/fpv-agent/key` |
| Агент: `CERTIFICATE_VERIFY_FAILED` | сертификат сайта не добавлен в доверенные | шаг 7.3 |
| Агент: `401` | ключ перевыпущен или вставлен с ошибкой | выпустить ключ заново, повторить шаг 10 |
| Ошибки TLS/подписи «not yet valid» | неверное время на машине | `timedatectl set-ntp true` |
| Guest Additions: «Please install the build and header files» | нет компилятора и заголовков ядра | шаг 1.4: `apt install build-essential dkms linux-headers-$(uname -r)` |

---

## 14. Обновление и повторная установка

Новая версия пакетов:

**[root]**

```bash
cd /opt/fpv-src && git pull             # скрипты сборки репозитория и документация
cd /opt/fpv-packages && git pull        # пакеты
/opt/fpv-src/packaging/debian/make-apt-repo.sh /srv/packages --key "FPV Local Repo" \
    /opt/fpv-packages/debian/fpv/*.deb
apt update && apt upgrade -y            # миграции БД применяются автоматически
apt install -y fpv-desktop              # если ставили до 0.1.0-10 (fpv-shortcuts переехала туда)
nginx -t && systemctl reload nginx      # если обновился файл сайта nginx
```

Если вы правили `/etc/nginx/sites-available/fpv-platform.conf` или `/etc/default/fpv-agent`,
dpkg спросит, какую версию оставить: `Y` — версия из пакета (ваши правки нужно будет повторить),
`N` — оставить свою.

**Переход на парки дронов (0.1.0-17 и новее).** Дроны, которые заказчики зарегистрировали раньше,
остаются без парка: заказ на них не оформить, оператор не пройдёт проверку готовности.
Техник создаёт парк (шаг 9.3), **admin** → «Дроны» → колонка «Парк» переводит в него дрон
(дрон станет закрытым для заказов), техник выполняет шаги 9.4.3–9.5. Ключ можно оставить
прежний или выпустить новый (тогда обновить `/etc/fpv-agent/key`).

Удаление с сохранением данных и секретов: `apt purge fpv-platform fpv-drone-agent fpv-desktop`
(ярлыки в домашних каталогах удаляются командой `fpv-shortcuts --remove` до удаления пакета).
База `fpv`, каталоги `/var/lib/fpv`, `/srv/fpv-evidence` и `/etc/fpv/secrets` не удаляются.

Сборка пакетов из исходного кода — `docs/debian-packages.md`. Парки дронов, маршрут А→Б→В и
права техника — `docs/fleets.md`.
