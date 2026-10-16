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
# fpv-desktop_0.1.0-18_all.deb  fpv-drone-agent_0.1.0-18_all.deb
# fpv-platform_0.1.0-18_amd64.deb  SHA256SUMS
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
apt policy fpv-platform fpv-drone-agent fpv-desktop   # Кандидат у всех: 0.1.0-18, источник file:/srv/packages
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

Дроны заводит и подключает **техник** — администратор своего парка дронов. Заказчик дроны
не регистрирует: он выбирает дрон из парков, которые техники открыли для заказов.

1. **technician** → «Мой парк» → **создать парк**: название и база — щелчок по карте района
   (карту загружает администратор, шаг 9.8) или координаты, плюс адрес.
   База — точка **А** маршрута: дрон вылетает с неё, забирает груз в точке **Б** и доставляет в **В**.
2. **technician** → «Мой парк» → «Завести дрон в парк» (серийный номер, например `SIM-0001`;
   учётный номер `RU-SIM-1`). Статус: «ожидает подтверждения».
3. **admin** → «Дроны» → **подтвердить** регистрацию. Статус дрона: «активен».
4. **technician** → «Обслуживание» → дрон → технический осмотр с результатом «Исправен».
5. **technician** → «Мой парк» → **«Выпустить ключ»** → скопировать ключ (показывается один раз) —
   он нужен в шаге 10.
6. После шага 10 (симулятор на связи): **technician** → «Мой парк» → «Подключение» —
   связь «На связи», телеметрия «поступает»; **«📹 Проверить видео»** — пробная трансляция с дрона
   (источник по умолчанию — тестовая таблица, ролик, USB или RTSP, §10.1). Затем
   **«Открыть для заказов»** — дрон появится у заказчиков.
7. **operator** → «Профиль» → заполнить профиль и отправить **заявку на допуск оператора**
   (номер свидетельства). **admin** → «Пользователи и роли» → «Заявки операторов» → **«Допустить»**. До допуска оператор
   не видит очередь заказов. То же — для `operator2`.
8. **admin** → «Карты» → загрузить карту района (изображение и координаты краёв) — она появится
   под маршрутами. Подробно — `docs/maps-and-live.md`. Без карты маршрут показывается схемой.

---

## 10. Симулятор дрона (сервисный модуль)

Без него заказ останавливается на проверке готовности:
«Не готов: Нет связи с сервисным модулем дрона (статус связи: offline)».

**[root]**

```bash
# вместо ВСТАВЬТЕ_КЛЮЧ — ключ из шага 9.5
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

### 10.1. Видеопотоки дрона: USB, RTSP, тестовый ролик

Сервисный модуль сообщает платформе, какие источники видео у него есть, а оператор выбирает
один из них в мониторе полёта — кнопка **⚙** вверху справа → «Видеопоток дрона» → «Применить».
Модуль переключает видео за несколько секунд; оно идёт **как видео дрона**: главное окно
монитора с телеметрией поверх, трансляция в карточке заказа, видеоподтверждение доставки.

| Источник | Откуда берётся |
|---|---|
| USB-камеры | камеры и платы захвата, подключённые к машине с `fpv-agent` (`/dev/videoN`; в ВМ — проброшенная веб-камера: VirtualBox «Устройства» → «Веб-камеры», нужен Extension Pack) |
| RTSP-потоки | IP-камеры из `/etc/fpv-agent/cameras.conf`, строки `имя=rtsp://…`; оператор видит только имя, адрес с паролем остаётся на модуле |
| Тестовые ролики | файлы `.mp4 .mkv .webm .mov .avi .ts .m4v` из `/var/lib/fpv-agent/videos`, воспроизводятся по кругу; при установке там создаётся `example-test.mp4` |
| Тестовая таблица | генерируется модулем; используется, если ничего не выбрано |

**[root]**

```bash
# Свой тестовый ролик (имя — латиница, цифры, точка, дефис, подчёркивание; без пробелов)
cp /home/vboxuser/Видео/flight.mp4 /var/lib/fpv-agent/videos/flight.mp4
chmod 644 /var/lib/fpv-agent/videos/flight.mp4

# RTSP-камера
echo 'nose=rtsp://user:password@192.168.1.10:554/stream1' >> /etc/fpv-agent/cameras.conf

# Источник по умолчанию (необязательно): USB-камера вместо тестовой таблицы
sed -i 's#^FPV_AGENT_VIDEO_DEVICE=.*#FPV_AGENT_VIDEO_DEVICE=/dev/video0#' /etc/default/fpv-agent

systemctl restart fpv-agent             # новые ролики и камеры подхватываются и сами в течение 30 с
journalctl -u fpv-agent -n 20 --no-pager # при выполнении задания: «видео: file:flight.mp4 (640x480, 15 к/с)»
```

Выбирает источник только назначенный оператор, пока заказ у него в работе; выбор
записывается в журнал аудита. Координатор и администратор видят список и активный источник.
Источник открывается только на время задания; модуль видео только читает — команд дрону через
него не передаётся. Если в `/etc/default/fpv-agent` нет строк `FPV_AGENT_VIDEO_*` (файл правили вручную до
обновления), допишите их:

```
FPV_AGENT_VIDEO_DEVICE=
FPV_AGENT_VIDEO_DIR=/var/lib/fpv-agent/videos
FPV_AGENT_VIDEO_SIZE=640x480
FPV_AGENT_VIDEO_FPS=15
```

Проверка: в карточке дрона статус связи `online`; у администратора — «Мониторинг» →
«Подключённые дроны».

---

## 11. Проведение заказа

1. **customer** → «Новый заказ» → дрон из открытых парков (точка А — база его парка подставляется
   сама), груз, точка **Б** — где забрать груз, точка **В** — куда доставить, получатель, временное
   окно → «Проверить и рассчитать стоимость» (расстояние А→Б→В) → «Подтвердить заказ».
2. Заказ попадает к операторам; **operator** принимает его.
3. **technician** → «Заказы на дронах парка» → заказ → проверяет подключение дрона и видео
   («📹 Проверить видео») → **«✓ Подтвердить готовность дрона к полёту»**. Проверяется: регистрация,
   исправность и свежая диагностика, связь, телеметрия, заряд, грузоподъёмность, дальность
   (А→Б→В и возврат на базу), видео проверено за последние 12 ч. Без подтверждения техника
   оператор не пройдёт проверку готовности.
4. **operator** — проверка готовности и начало задания. Дрон (симулятор) взлетает с базы А,
   в точке Б выполняет «Забор груза» (участникам приходит уведомление «груз забран»), затем летит
   к получателю В. **operator** ведёт задание, **coordinator** сопровождает маршрут, **technician** отвечает за
   исправность дрона; ход полёта — по телеметрии симулятора, видео — в карточке заказа.
   Оператор открывает **«🛰 Открыть монитор полёта»** — отдельное окно
   (`/operator/orders/<id>/monitor`) с видео, телеметрией, проверкой готовности, началом и
   завершением полёта. Если сессия истекла, окно отправит на вход и после входа вернётся
   в монитор.
5. Пока заказ выполняется, в его карточке доступна **«Прямая трансляция»** с камеры дрона
   (заказчику, назначенным оператору и координатору, администратору). У заказчика блок
   **«📡 Трансляция с дрона»** — вверху карточки с момента принятия заказа: видео включается само,
   когда оператор начинает задание; в списке «Мои заказы» у такого заказа метка «● В ЭФИРЕ». Режим «Кадры» — для
   браузеров без H.264 и медленных каналов.
6. После завершения начисляются баллы (временное окно — 30, результат — 70, один раз).

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
| Оператор не видит очередь заказов | оператор не допущен администратором | шаг 9.7 |
| Кнопки «Выпустить ключ» нет | дрон не подтверждён администратором | шаг 9.3 |
| «Новый заказ»: «Сейчас нет свободных дронов» | техник не открыл дрон для заказов или все дроны заняты | шаг 9.6; «Открыть для заказов» требует подтверждённой регистрации, осмотра «Исправен» и ключа |
| Проверка готовности оператора: «Техник парка ещё не подтвердил готовность дрона» | техник не подтвердил готовность по этому заказу | **technician** → «Заказы на дронах парка» → заказ → «Подтвердить готовность дрона к полёту» (§11) |
| Подтверждение техника: «Видео с дрона не проверено» | не было пробной трансляции за последние 12 ч | там же: «📹 Проверить видео», дождаться «В ЭФИРЕ», повторить подтверждение |
| «Дрон не входит в парк» | дрон зарегистрирован заказчиком до версии 0.1.0-17 | **admin** → «Дроны» → колонка «Парк» → выбрать парк; затем техник открывает дрон для заказов |
| Заказ: «Нет связи с сервисным модулем дрона (offline)» | не запущен симулятор или неверный ключ | шаг 10; `journalctl -u fpv-agent` |
| В окне ⚙ нет USB-камеры | камера не подключена или не проброшена в ВМ | `ls /dev/video*`; VirtualBox — «Устройства» → «Веб-камеры»; подождать 30 с или `systemctl restart fpv-agent` |
| В окне ⚙ нет ролика | неподходящее имя файла (пробелы, кириллица) или расширение | переименовать, например `flight-1.mp4`; расширения `.mp4 .mkv .webm .mov .avi .ts .m4v` |
| В окне ⚙ «модуль ещё не сообщил список источников» | `fpv-agent` не запущен или старой версии | `systemctl status fpv-agent`; обновить пакеты до 0.1.0-15 или новее |
| Выбран RTSP, видео нет; агент: `видео rtsp:… остановилось` | камера недоступна, неверный адрес или пароль | проверить адрес: `ffmpeg -rtsp_transport tcp -i 'rtsp://…' -frames:v 1 /tmp/x.jpg` |
| Агент: `видео usb:video0 остановилось: … Permission denied` / `Device or resource busy` | нет доступа к камере или её занял браузер | пакет 0.1.0-14+ (служба в группе `video`); закрыть «Включить камеру» в браузере на этой же машине |
| Агент: `видео usb:video0 остановилось: … Invalid argument` | камера не поддерживает заданный размер | `FPV_AGENT_VIDEO_SIZE=640x480` или `1280x720` в `/etc/default/fpv-agent` |
| Агент: `файл ключа доступен другим пользователям` | права ключа не 600 | `chmod 600 /etc/fpv-agent/key` |
| Агент: `CERTIFICATE_VERIFY_FAILED` | сертификат сайта не добавлен в доверенные | шаг 7.3 |
| Агент: `401` | ключ перевыпущен или вставлен с ошибкой | выпустить ключ заново, повторить шаг 10 |
| Ошибки TLS/подписи «not yet valid» | неверное время на машине | `timedatectl set-ntp true` |

---

## 14. Обновление и повторная установка

При обновлении до 0.1.0-17 и новее (парки дронов, `docs/fleets.md`): дроны, которые заказчики
зарегистрировали раньше, остаются без парка. Техник создаёт парк (§9.1), администратор →
«Дроны» → колонка «Парк» переводит в него дрон, техник выполняет §9.4–9.6. Ключ дрона
можно оставить прежний или выпустить новый (тогда обновить `/etc/fpv-agent/key`).

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
