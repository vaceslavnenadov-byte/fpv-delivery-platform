# Установка платформы FPV на Debian 13 с нуля

Пошаговая инструкция для одной машины (виртуальная машина или сервер): платформа, база данных,
веб-сайт, вход пользователей и симулятор дрона. Учитывает все проблемы, встреченные при первом
запуске (таблица в конце).

Результат: сайт `https://fpv.dc.fpv.local` открывается в браузере, можно войти, зарегистрировать
дрон, подключить его симулятор и провести заказ.

---

## 0. Правила, которые экономят время

| Правило | Почему |
|---|---|
| Системные команды (`apt`, `systemctl`, файлы в `/etc`, `/srv`) — **от root** | иначе «Permission denied» |
| Браузер, ярлыки, всё графическое — **от обычного пользователя** (`vboxuser`) | Firefox от root не запускается: «Running Firefox as root… is not supported» |
| Команды вставлять **блоками по одному шагу** и смотреть на результат | ошибка в середине блока иначе незаметна |
| Пути к скриптам — **полные** (`/opt/fpv-src/...`) | относительный путь работает только из нужной папки: «No such file or directory» |
| Файл `.desktop` не запускают через `./` | это не скрипт: «[Desktop: команда не найдена» |

Как стать root и вернуться обратно:

```bash
su -          # ввести пароль root; приглашение станет root@...#
exit          # вернуться в vboxuser
whoami        # проверить, кто вы сейчас
```

В инструкции перед каждым блоком указано: **[root]** или **[vboxuser]**.

---

## 1. Подготовка системы

**[root]**

```bash
cat /etc/debian_version                 # 13.x — нужен Debian 13 «trixie»
timedatectl set-ntp true                # точное время: иначе ошибки TLS и подписи apt
apt update
apt install -y git curl ca-certificates gnupg openssl dpkg-dev apt-utils \
               postgresql nginx ffmpeg
systemctl enable --now postgresql nginx
pg_lsclusters                           # 17  main  5432  online
```

Если `apt update` пишет об ошибках зеркала — проверьте доступ в интернет и файл
`/etc/apt/sources.list.d/debian.sources`.

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
# fpv-desktop_0.1.0-12_all.deb  fpv-drone-agent_0.1.0-12_all.deb
# fpv-platform_0.1.0-12_amd64.deb  SHA256SUMS
(cd /opt/fpv-packages/debian/fpv && sha256sum -c SHA256SUMS)     # все три: OK
```

Три пакета:

| Пакет | Куда ставить | Что внутри |
|---|---|---|
| `fpv-platform` | сервер | API, воркер, веб-интерфейс (отдельные клиенты по ролям), миграции, сайт nginx |
| `fpv-drone-agent` | компьютер дрона (здесь — та же ВМ, симулятор) | сервисный модуль дрона |
| `fpv-desktop` | рабочее место пользователя (здесь — та же ВМ) | команда `fpv-shortcuts` и `/etc/fpv-desktop.conf` |

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
apt policy fpv-platform fpv-drone-agent fpv-desktop   # Кандидат у всех: 0.1.0-12, источник file:/srv/packages
```

---

## 4. Установка пакетов

**[root]**

```bash
apt install -y fpv-platform fpv-drone-agent fpv-desktop
```

На отдельных машинах ставится только своё: сервер — `fpv-platform`, компьютер дрона —
`fpv-drone-agent`, рабочее место пользователя — `fpv-desktop` (подключив на нём тот же
репозиторий из шага 3.3).

`fpv-platform` создаёт пользователя `fpv`, каталоги, виртуальное окружение Python, службы `fpv-api`
и `fpv-worker`. Предупреждение о том, что база данных не настроена, на этом шаге нормально.

---

## 5. База данных

Без этого шага `fpv-api` работает, но каждый запрос завершается ошибкой
(«connection to server at 127.0.0.1, port 5432 failed»), а страница входа показывает
`JSON.parse: unexpected character` или «Сервер приложения недоступен».

**[root]**

```bash
PW=$(openssl rand -hex 16)          # случайный пароль; хранится только в файле секрета
runuser -u postgres -- psql -c "CREATE ROLE fpv_app LOGIN PASSWORD '$PW'"
runuser -u postgres -- psql -c "CREATE DATABASE fpv OWNER fpv_app"

printf '%s' "postgresql+psycopg://fpv_app:$PW@127.0.0.1:5432/fpv" > /etc/fpv/secrets/database_url
chown root:fpv /etc/fpv/secrets/database_url
chmod 0640 /etc/fpv/secrets/database_url

fpv-migrate                         # Применены: 0001_core_schema.sql, 0002_reference_data.sql
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
| `FPV-Technicians` | техник |
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
`coordinator`, `technician`.

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

# А.2. Samba AD
DEBIAN_FRONTEND=noninteractive apt install -y samba winbind krb5-user smbclient
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
for g in FPV-Customers FPV-Operators FPV-Coordinators FPV-Technicians FPV-Admins; do
  samba-tool group add "$g" --groupou="OU=FPV,OU=Groups"
done
samba-tool user create svc-fpv-ldap 'Svc!Passw0rd123' --userou="OU=Service,OU=FPV"
samba-tool user setexpiry svc-fpv-ldap --noexpiry

# А.4. Подключение платформы к домену
printf '%s' 'Svc!Passw0rd123' > /etc/fpv/secrets/ldap_bind_password
cp /var/lib/samba/private/tls/ca.pem /usr/local/share/ca-certificates/fpv-samba.crt
update-ca-certificates
systemctl restart fpv-api fpv-worker

# А.5. Пользователи (повторить для каждого)
samba-tool user create ivanov 'Passw0rd!123' --given-name=Иван --surname=Иванов \
  --mail-address=ivanov@fpv.local
samba-tool group addmembers FPV-Admins ivanov
```

Управление: `samba-tool user list`, `samba-tool group listmembers FPV-Operators`,
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
работают только через nginx, который передаёт запросы API в `fpv-api`. Сайт отвечает на имя
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
| Техник | `https://fpv.dc.fpv.local/technician/` |
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
fpv-shortcuts operator                          # только клиент оператора
fpv-shortcuts --url https://fpv.dc.fpv.local    # указать адрес сайта
fpv-shortcuts --list                            # показать, что будет создано
fpv-shortcuts --remove                          # удалить ярлыки
sudo -u vboxuser fpv-shortcuts                  # создать ярлыки этому пользователю из-под root
```

Запуск: двойной щелчок по значку (в GNOME при надписи «Не доверять» — правой кнопкой
→ «Разрешить запуск»). Каждый пользователь видит в своём клиенте только свою роль; на общем
входе достаточно одного ярлыка «FPV Доставка» — он сам откроет нужный раздел после входа.
Если у пользователя несколько ролей, переход между его клиентами — блок «Другие разделы»
в боковом меню. Клиент чужой роли покажет «не доступен для вашей роли».

---

## 9. Первые действия в интерфейсе

Имена — для варианта Б; в варианте А — ваши доменные пользователи из нужных групп.

1. **customer** → заполнить профиль → «Мои дроны» → зарегистрировать дрон
   (серийный номер, например `SIM-0001`; адаптер — симулятор). Статус: «ожидает подтверждения».
2. **admin** → «Дроны» → **подтвердить** регистрацию. Статус дрона: «активен».
3. **customer** → «Мои дроны» → **«Выпустить ключ»** → скопировать ключ (показывается один раз).
4. **operator** → «Профиль» → заполнить профиль и отправить **заявку на допуск оператора**
   (номер свидетельства). **admin** → «Пользователи и роли» → «Заявки операторов» → **«Допустить»**. До допуска оператор
   не видит очередь заказов. То же — для `operator2`.
5. **admin** → «Карты» → загрузить карту района (изображение и координаты краёв) — она появится
   под маршрутами. Подробно — `docs/maps-and-live.md`. Без карты маршрут показывается схемой.

---

## 10. Симулятор дрона (сервисный модуль)

Без него заказ останавливается на проверке готовности:
«Не готов: Нет связи с сервисным модулем дрона (статус связи: offline)».

**[root]**

```bash
# вместо ВСТАВЬТЕ_КЛЮЧ — ключ из шага 9.3
printf '%s' 'ВСТАВЬТЕ_КЛЮЧ' > /etc/fpv-agent/key
chown fpv-agent:fpv-agent /etc/fpv-agent/key
chmod 600 /etc/fpv-agent/key            # иначе агент откажется читать ключ

systemctl enable --now fpv-agent
systemctl restart fpv-agent
journalctl -u fpv-agent -n 20 --no-pager # без 401 и ошибок SSL
```

Сценарий симулятора — `FPV_AGENT_SCENARIO` в `/etc/default/fpv-agent`:
`normal`, `fast`, `link-loss`, `link-blip`, `critical-error`, `no-video`, `faulty`;
после изменения — `systemctl restart fpv-agent`.

Проверка: в карточке дрона статус связи `online`; у администратора — «Мониторинг» →
«Подключённые дроны».

---

## 11. Проведение заказа

1. **customer** → «Новый заказ» → груз, точки А и Б, получатель, временное окно →
   «Проверить и рассчитать стоимость» → «Подтвердить заказ».
2. Заказ проходит проверку готовности (дрон на связи, исправен) и попадает к операторам.
3. **operator** берёт заказ, **coordinator** сопровождает маршрут, **technician** отвечает за
   исправность дрона; ход полёта — по телеметрии симулятора, видео — в карточке заказа.
   Оператор открывает **«🛰 Открыть монитор полёта»** — отдельное окно
   (`/operator/orders/<id>/monitor`) с видео, телеметрией, проверкой готовности, началом и
   завершением полёта. Если сессия истекла, окно отправит на вход и после входа вернётся
   в монитор.
4. Пока заказ выполняется, в его карточке доступна **«Прямая трансляция»** с камеры дрона
   (заказчику, назначенным оператору и координатору, администратору). Режим «Кадры» — для
   браузеров без H.264 и медленных каналов.
5. После завершения начисляются баллы (временное окно — 30, результат — 70, один раз).

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
fpv-migrate --check                     # все миграции применены
```

---

## 13. Известные проблемы и решения

| Симптом | Причина | Решение |
|---|---|---|
| `make-apt-repo.sh: No such file or directory` | скрипт запущен по относительному пути не из папки репозитория, или клонирована ветка `main` | шаг 2 (ветка `claude/complex-system-development-ucubxt`) и полный путь `/opt/fpv-src/...` |
| `cannot stat '/srv/packages/fpv-local.gpg'` | репозиторий не создан (предыдущая команда упала) | выполнить шаг 3.2 и проверить `ls /srv/packages` |
| `A key for "FPV Local Repo" already exists` | ключ уже создан | ничего делать не нужно, шаг 3.1 это учитывает |
| `dpkg-scanpackages: command not found` | нет `dpkg-dev` | шаг 1 |
| `apt update`: `NO_PUBKEY` / `is not signed` | ключ не скопирован в `/usr/share/keyrings` или репозиторий не подписан | шаг 3.3; после изменения пакетов — снова 3.2 |
| `Running Firefox as root … is not supported` | браузер запущен от root | `exit` в vboxuser; ярлык — шаг 8 от vboxuser |
| `./fpv-*.desktop: [Desktop: команда не найдена` | `.desktop` запущен как скрипт | двойной щелчок по значку или `gtk-launch fpv-operator` |
| `fpv-shortcuts: команда не найдена` | не установлен `fpv-desktop` (до версии 0.1.0-10 команда была в `fpv-platform`) | `apt install fpv-desktop` |
| Ярлыки открывают не тот адрес | в `/etc/fpv-desktop.conf` другой `FPV_SITE_URL` | исправить файл и снова `fpv-shortcuts`, или `fpv-shortcuts --url https://…` |
| `fpv-shortcuts: недопустимый адрес` | в адресе пробелы, кавычки или спецсимволы | только `https://имя[:порт][/путь]` |
| `fpv-shortcuts: запустите от своего пользователя` | команда запущена от root | выполнить от своего пользователя или `sudo -u ПОЛЬЗОВАТЕЛЬ fpv-shortcuts` |
| Сайт не открывается / «соединение сброшено» | сайт nginx не включён, нет записи в `/etc/hosts`, нет сертификата, открыт `localhost` | шаг 7, адрес только `https://fpv.dc.fpv.local` |
| `nginx -t`: duplicate default server | включён стандартный сайт nginx | `rm /etc/nginx/sites-enabled/default` |
| Вход: `JSON.parse: unexpected character…` или «Сервер приложения недоступен» | `fpv-api` не может обратиться к БД (пустой `database_url`, PostgreSQL не запущен) | шаг 5; `journalctl -u fpv-api` |
| `/health/ready`: `directory_unavailable` | домен недоступен (вариант А не настроен) | вариант А или Б в шаге 6 |
| Вход: «Неверное имя пользователя или пароль» | нет такого пользователя в каталоге / неверный пароль | вариант Б: пароль из шага 6; вариант А: `samba-tool user list` |
| Вход проходит, но нет нужных разделов | пользователь не в группе роли | `samba-tool group addmembers FPV-… <имя>`, войти заново |
| Карта не появляется под маршрутом | карта отключена или точки маршрута вне её границ | «Карты»: проверить границы (север > юг, восток > запад) и статус |
| Загрузка карты: «413 Request Entity Too Large» | старый файл сайта nginx | шаг 7.4 после обновления пакета; `nginx -t && systemctl reload nginx` |
| Трансляция: «Ожидание видеопотока…» | сегменты не поступают (нет связи, сценарий `no-video`) | `journalctl -u fpv-agent`; сценарий `normal` |
| Трансляция: «Трансляция недоступна: не установлен ffmpeg» | нет ffmpeg на сервере | `apt install ffmpeg` |
| «Камера не найдена» (раньше — `The object can not be found here.`) при «Включить камеру» | браузер не видит ни одной камеры; в ВМ камера хоста не проброшена | VirtualBox: «Устройства» → «Веб-камеры» → камера хоста (нужен Extension Pack); проверить `ls /dev/video*` в гостевой системе; обновить страницу. Или включать камеру с телефона/ноутбука заказчика |
| «Доступ к камере запрещён» | браузер запретил камеру для сайта | значок слева от адреса → разрешить камеру → обновить страницу |
| «Камера занята другой программой» | камеру держит другое приложение | закрыть его и повторить |
| Вверху долго «Переподключение…», в `journalctl -u fpv-api`: `GET /api/v1/ws HTTP/1.0" 404` | сайт nginx из пакета до 0.1.0-12: блок WebSocket перехватывался общим блоком `/api/` | обновить пакеты (шаг 14), затем `nginx -t && systemctl reload nginx`; в файле сайта должно быть `location ^~ /api/v1/ws` |
| Видео не воспроизводится, кадры работают | браузер без H.264 (Firefox без `libavcodec`, Chromium без кодеков) | `apt install ffmpeg` на рабочем месте или режим «Кадры» |
| «Этот раздел … не доступен для вашей роли» | открыт клиент чужой роли (ярлык или закладка) | «Перейти в свой раздел» или общий вход `/` |
| Старая закладка `/orders/…` открывает общий вход, а не заказ | адреса прежнего единого интерфейса | адреса клиентов — шаг 8 |
| Оператор не видит очередь заказов | оператор не допущен администратором | шаг 9.4 |
| Кнопки «Выпустить ключ» нет | дрон не подтверждён администратором | шаг 9.2 |
| Заказ: «Нет связи с сервисным модулем дрона (offline)» | не запущен симулятор или неверный ключ | шаг 10; `journalctl -u fpv-agent` |
| Агент: `файл ключа доступен другим пользователям` | права ключа не 600 | `chmod 600 /etc/fpv-agent/key` |
| Агент: `CERTIFICATE_VERIFY_FAILED` | сертификат сайта не добавлен в доверенные | шаг 7.3 |
| Агент: `401` | ключ перевыпущен или вставлен с ошибкой | выпустить ключ заново, повторить шаг 10 |
| Ошибки TLS/подписи «not yet valid» | неверное время на машине | `timedatectl set-ntp true` |

---

## 14. Обновление и повторная установка

Новая версия пакетов:

```bash
cd /opt/fpv-packages && git pull
/opt/fpv-src/packaging/debian/make-apt-repo.sh /srv/packages --key "FPV Local Repo" \
    /opt/fpv-packages/debian/fpv/*.deb
apt update && apt upgrade -y            # миграции применяются автоматически
apt install -y fpv-desktop              # при обновлении с 0.1.0-9 и раньше: туда переехала fpv-shortcuts
```

`apt upgrade` не ставит новые пакеты сам, поэтому `fpv-desktop` при обновлении со старых версий
устанавливается отдельной командой; файл `fpv-shortcuts` переходит из `fpv-platform` в
`fpv-desktop` без конфликтов. Созданные ранее ярлыки продолжают работать; чтобы получить
ярлыки новых клиентов — `fpv-shortcuts` от своего пользователя.

Перед сборкой репозитория стоит обновить и исходники (там `make-apt-repo.sh`):
`cd /opt/fpv-src && git pull`.

Удаление с сохранением данных и секретов: `apt purge fpv-platform fpv-drone-agent fpv-desktop`
(ярлыки в домашних каталогах удаляются командой `fpv-shortcuts --remove` до удаления пакета).
База `fpv`, каталоги `/var/lib/fpv`, `/srv/fpv-evidence` и `/etc/fpv/secrets` не удаляются.

Сборка пакетов из исходного кода — `docs/debian-packages.md`.
