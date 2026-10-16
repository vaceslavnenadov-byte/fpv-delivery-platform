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

Камера дрона: `--video-device /dev/video0` (или `FPV_AGENT_VIDEO_DEVICE`) — видео с камеры
вместо тестовой таблицы, `--video-size 640x480`, `--video-fps 15`. Камера только читается
(ffmpeg, V4L2) и открывается лишь на время задания; см. `fpv_agent/camera.py`.

Подключение реального оборудования — отдельный этап, см. `docs/hardware-integration-plan.md`.
