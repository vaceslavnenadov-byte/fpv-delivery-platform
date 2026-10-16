# fpv-agent — сервисный модуль дрона

Клиент контура телеметрии платформы. Состоит из:

* `adapters/base.py` — интерфейс адаптера оборудования (только чтение: телеметрия,
  диагностика, видео; **без команд управления полётом**);
* `adapters/simulator.py` — симулятор (сценарии `normal`, `fast`, `link-loss`, `link-blip`,
  `critical-error`, `no-video`, `faulty`);
* `agent.py` — цикл передачи данных с локальной очередью и переподключением.

Зависимости: только стандартная библиотека Python 3.11+; для реалистичного видео — `ffmpeg`.

```bash
export FPV_DRONE_KEY_FILE=/etc/fpv-agent/key   # права 600, ключ выпускает владелец дрона в кабинете
python -m fpv_agent --server https://fpv.dc.local --scenario normal --ca-file /etc/fpv-agent/ca.pem
```

Видео дрона (`fpv_agent/video.py`): источники — USB-камеры V4L2 (`/dev/videoN`), RTSP-потоки
из `--cameras-file` (`/etc/fpv-agent/cameras.conf`, строки `имя=rtsp://…`), тестовые ролики из
`--video-dir` (по умолчанию `<state-dir>/videos`) и тестовая таблица. Модуль сообщает список
серверу (`POST /drone-link/v1/video/sources`), выбор оператора приходит в справке о задании
(`video_source`). `--video-device /dev/video0` — источник по умолчанию, `--video-size`, `--video-fps`.
Источники только читаются (ffmpeg) и открываются лишь на время задания.

Подключение реального оборудования — отдельный этап, см. `docs/hardware-integration-plan.md`.
