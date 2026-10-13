# Пользовательские команды, сценарии и «Ход работы»: контракт

Общий контракт для движка (Python, `src/aion/commands/`) и интерфейса (`frontend/`).
Если в реализации что-то меняется, сначала правится этот файл.

## 1. Хранение

- Папка: `<data_dir>/commands/` (по умолчанию `%LOCALAPPDATA%\Aion\commands`). Подпапки — это «папки команд»:
  их можно включать и выключать целиком.
- Один файл — одна команда или сценарий, формат `*.yaml` (UTF-8). Имя файла — это `id`; точнее, `id` —
  путь файла относительно `commands/` без `.yaml` и через `/`: `greet`, `work/standup`, `packs/media/rewind`
  (иначе одинаковые имена в разных папках не различить). Части пути: буквы (и кириллица), цифры, пробел,
  `_ - .`; не больше 4 уровней. Первые части `export`, `import`, `folders`, `record` заняты маршрутами REST.
- Паки: `<data_dir>/commands/packs/<pack>/`. В корне пака лежит `pack.yaml`: `{name, description, lang: ru|en|both}`.
  Встроенные паки поставляются в `src/aion/commands/packs/` и копируются в папку пользователя при первом включении.
- Состояние включения и выключения хранится в `<data_dir>/commands/state.json`: `{"disabled": ["folder/id", "folder/"]}`.
  Сами YAML-файлы при выключении не меняются.

## 2. Формат команды

```yaml
name: Протокол гостей            # отображаемое имя
triggers:                        # фразы в синтаксисе роутера: [необязательно], (а|б), {slot}
  - протокол гостей
  - гости пришли
lang: ru                         # ru | en | both
enabled: true
confirm: auto                    # auto | always | never; auto = спросить, если хоть одно действие опасное
description: Свернуть всё, убавить звук, открыть музыку
actions:
  - {type: minimize_all}
  - {type: volume, set: 20}
  - {type: wait, seconds: 0.5}
  - {type: open, target: "https://music.yandex.ru"}
  - {type: say, text: "Готово, гости могут заходить"}
```

Команда, в которой больше одного действия, и есть «сценарий». Отдельного типа для сценариев нет.

## 3. Действия (`type`)

| type | Поля | Опасное | Реализация |
|---|---|---|---|
| `open` | `target` (программа, папка, файл или URL) | нет | переиспользовать `plugins/builtin/apps` (поиск программ) |
| `search` | `query`, `engine?` | нет | как `apps.web_search` |
| `keys` | `combo` (`ctrl+shift+esc`) или `text` | нет | pynput |
| `mouse` | `action: move\|click\|right\|double\|scroll`, `x?`, `y?`, `amount?` | нет | pynput |
| `wait` | `seconds` (≤ 600) | нет | asyncio.sleep, отменяемый |
| `volume` | `set?`, `change?`, `mute?`, `app?` (громкость приложения) | нет | pycaw; `app` — по имени процесса |
| `window` | `action: minimize\|maximize\|close\|focus`, `name` | `close` — да | переиспользовать `apps` |
| `minimize_all` | — | нет | `apps` |
| `shell` | `command`, `shell: cmd\|powershell`, `timeout?` (≤ 120) | **да** | subprocess без окна, вывод в «Ход работы» |
| `power` | `action: shutdown\|restart\|sleep\|lock` | **да** (кроме `lock`) | переиспользовать `system` |
| `delete` | `path` | **да** | только в корзину (send2trash не тянуть, использовать `SHFileOperation`/`IFileOperation`) |
| `file` | `path`, `content` | нет (если файл существует — да) | создать текстовый файл |
| `dictate` | `prompt?` | нет | спросить фразу (`ctx.ask`) и напечатать её |
| `play` | `path` (`.wav`/`.mp3`) | нет | через плеер Aion (`audio/player.py`), не системный |
| `say` | `text` | нет | `ctx.say` |
| `run` | `command` (id другой команды) | как у вложенной | глубина ≤ 5, циклы запрещены |

Эмуляция клавиш и мыши: в UI и в описании пака показывать предупреждение, что игры с античитом могут
игнорировать такие нажатия.

## 4. Выполнение

- Движок регистрирует триггеры в роутере как команды виртуального плагина `commands`. Приоритет у них
  ниже встроенных точных шаблонов, при равном счёте выигрывает пользовательская команда.
- **Режим префикса** (`commands.require_wake_name: bool`, по умолчанию `false`): если включён, команды
  выполняются только из фраз, начатых с имени ассистента. Из текста в UI команды выполняются всегда.
- **Подтверждение.** Перед опасным действием задаётся голосовой вопрос через `ctx.confirm`: «Выполнить
  <имя>: <что>?». В UI тот же вопрос показывается в «Ходе работы» с кнопками «Да» и «Нет». Если ответа нет
  за 15 с, действие отменяется.
- **Отмена.** WS-сообщение `{"type":"interrupt"}` (кнопка «Стоп») отменяет текущий прогон. Действие,
  которое уже выполняется, получает `CancelledError`, процесс `shell` завершается (kill дерева процессов).

## 5. События «Хода работы» (шина → WS)

Добавляются в `core/events.py`:

```python
class RunStarted(Event):
    run_id: str
    title: str
    source: Source


class StepStarted(Event):
    run_id: str
    step_id: str
    kind: str
    title: str
    detail: str = ""


class StepFinished(Event):
    run_id: str
    step_id: str
    status: Literal["ok", "error", "cancelled", "skipped"]
    detail: str = ""


class ConfirmRequested(Event):
    run_id: str
    step_id: str
    question: str
    timeout: float


class RunFinished(Event):
    run_id: str
    status: Literal["done", "error", "interrupted"]
```

`kind` берётся из `StepKind` фронтенда (`frontend/src/components/worklog.ts`): `heard|listen|intent|tool|think|speak|reply|note`
и новые `action|confirm`. Шаги шлёт не только движок команд: `Brain` (вызовы инструментов и MCP) тоже
оборачивает каждый вызов в `StepStarted`/`StepFinished` с тем же `run_id` текущего хода.
WS от UI: `{"type":"confirm","run_id":..,"step_id":..,"answer":true|false}`.

## 6. REST (`ui/server.py`, все с `auth`)

| Метод | Путь | Тело / ответ |
|---|---|---|
| GET | `/api/commands` | `{commands: [{id, folder, name, triggers, enabled, dangerous, lang, description, error}], folders: [{path, enabled}]}` |
| GET | `/api/commands/{id}` | полная команда (как YAML, но в JSON) + `id, folder, enabled, dangerous, error`; для битого файла `{id, folder, error, raw}` |
| PUT | `/api/commands/{id}` | сохранить (валидация pydantic, 422 `{"error": ["действие 1, seconds: больше 0"]}`) → краткая запись, как в списке |
| DELETE | `/api/commands/{id}` | удалить файл |
| POST | `/api/commands/{id}/toggle` | `{enabled}` |
| POST | `/api/commands/folders/toggle` | `{path, enabled}` |
| POST | `/api/commands/{id}/test` | запустить «без голоса» → `{run_id}` (ход виден в «Ходе работы») |
| POST | `/api/commands/import` | multipart (поле `file`) `.yaml`/`.zip` → `{imported: [id], errors: ["путь: причина"]}` |
| GET | `/api/commands/export?ids=a,b` | `.zip` (без `ids` — все команды) |
| GET | `/api/commands/packs` / POST `/api/commands/packs/{name}` `{enabled, lang}` | паки |
| POST | `/api/commands/record/start` / `/stop` | запись клавиш и мыши → `{actions: [...]}` (не сохраняет, редактор показывает и сохраняет сам) |
| GET | `/api/history/search?q=&limit=` | поиск по диалогам (Brain/episodes и журнал UI) |

## 7. Конфиг (`config/schema.py`, вносит главный агент)

```python
class CommandsConfig(_Model):
    enabled: bool = True
    require_wake_name: bool = False  # режим префикса
    confirm_timeout: float = 15.0
    shell_timeout: float = 60.0
```

Плюс `Config.commands: CommandsConfig = CommandsConfig()`. Telegram настраивается в `plugins.settings.telegram`
(токен лежит в `.env` как `AION_TELEGRAM_TOKEN`, белый список — `allowed_chat_ids: list[int]`).

## 8. Уточнения реализации

Реализовано в `src/aion/commands/` (движок, хранилище, действия), `core/dialog.py` (прогоны), `ui/server.py`.

**Прогоны.** Каждый ход диалога — это прогон: `RunStarted` (`title` — фраза, `source`) и `RunFinished` шлёт
`DialogManager` для любой фразы пользователя, а не только для команд. Движок команд и `Brain` шлют внутри
него шаги с тем же `run_id`. `POST /api/commands/{id}/test` запускает отдельный прогон (`title` — имя
команды, `source: "ui"`); это обычный ход диалога, поэтому «Стоп» (`interrupt`) и новая фраза его отменяют.
`RunFinished.status`: `done`; `error`, если действие не удалось; `interrupted` при отмене. Реплики
ассистента по своей инициативе прогонами не считаются и шагов не шлют.

**Шаги.**

- Действие команды: `kind: "action"`, `title` — описание («Свернуть все окна», «Громкость 20%»,
  «CMD: echo 1»), `StepFinished.detail` — результат (для `shell` — хвост вывода, до 2000 символов).
- Подтверждение: отдельный шаг `kind: "confirm"` внутри шага действия; его `step_id` приходит в
  `ConfirmRequested`. Ответ: `StepFinished` со `status: "ok"` и `detail: "Да"`, иначе `status: "cancelled"` и
  `detail: "Нет"` или `"Нет ответа"`. Сам шаг действия после отказа тоже закрывается как `cancelled`.
- `run` (вложенная команда) — шаг, внутри которого идут шаги вложенной команды.
- `Brain`: `kind: "tool"`, `title` — «Инструмент calc.calculate» или «MCP github: search», `detail` — аргументы
  в JSON; `StepFinished.detail` — ответ инструмента (до 300 символов), `status: "error"`, если он вернул ошибку.

**Подтверждение.** Вопрос звучит голосом и одновременно приходит `ConfirmRequested`; засчитывается первый
ответ. Отказ или тишина останавливают весь прогон, ассистент говорит «Отменено.». `confirm: always`
спрашивает один раз перед первым действием («Выполнить «Имя»?»), `never` не спрашивает совсем. `file`
спрашивает, только если файл уже есть. Опасность `run` определяется по вложенной команде (поле `dangerous`
в списке учитывает вложенность).

**Совпадения.** Пользовательские команды регистрируются в роутере отдельной группой с приоритетом 50:
при равном счёте они выигрывают у встроенных (у тех приоритет не выше 10), а точная встроенная фраза
(счёт 100) выигрывает у нечёткого совпадения пользовательской. Слоты из фраз (`найди видео про {topic}`)
подставляются в текстовые поля действий (`query: "{topic} youtube"`).

**Режим префикса.** Голосовая фраза считается «с именем», если имя её открывало (или закрывало, как и
раньше), если имя прозвучало отдельно прямо перед ней (в течение 8 с) или была нажата клавиша push-to-talk.
Ответ на вопрос ассистента и фраза в окне продолжения разговора — без имени. Плагинам это видно как
`ctx.named`; `ctx.run_id` — id прогона.

**Языки пака.** У команды с `lang: both` язык каждой фразы определяется по алфавиту: кириллица — `ru`,
остальное — `en`. Выбор `lang` у пака отбирает нужные фразы. Обычные команды вне паков регистрируются со
всеми фразами.

**Паки и примеры.** `GET /api/commands/packs` → `[{name, title, description, lang, selected_lang, builtin,
installed, enabled, commands}]`. Встроенные `work`, `games`, `media` копируются в `commands/packs/<pack>/` при
первом включении; выключение пишет `packs/<pack>/` в `state.json`. При первом запуске (когда папки
`commands` ещё нет) в `commands/examples/` кладутся три сценария: «Протокол гостей», «Режим
концентрации», «Короткий перерыв».

**Импорт и экспорт.** Импорт не перезаписывает: при совпадении id добавляется `-2`, `-3`. В zip сохраняются
папки; `pack.yaml` принимается только как `packs/<pack>/pack.yaml`. Архив проверяется так же, как архивы
плагинов: без путей наружу, ссылок и zip-бомб. Вместо multipart можно прислать файл телом запроса и указать
имя в `?filename=`. В экспорт попадает и `pack.yaml` паков, команды которых выбраны. Импортированной
опасной команде с `confirm: never` ставится `confirm: auto`, об этом сообщает строка в `errors` (команда при
этом импортирована): чужой файл не должен выполнять `shell` без вопроса.

**Редактор.** В `GET /api/commands/{id}` `enabled` — итоговое состояние с учётом `state.json` и папки;
`PUT` с `enabled: true` снимает и выключение из `state.json`. Поля `id, folder, dangerous, error, raw` в теле
`PUT` игнорируются.

**Запись макроса.** `POST /api/commands/record/start` → `{ok: true}`; `/stop` → `{actions}`: набранный текст
— `{type: keys, text}`, сочетания — `{type: keys, combo: "ctrl+s"}`, щелчки — `mouse` с координатами
(два щелчка в одной точке — `double`), паузы от 0,3 с — `wait`. Запись сама останавливается через 10 минут
или 2000 событий.

**Поиск по истории.** `GET /api/history/search?q=&limit=30` → `[{kind: "message", role, text, source, ts}
| {kind: "episode", role: "diary", text, source: "", ts, ended}]`, новые первыми. Сообщения — из журнала
UI, записи дневника — из прошлых разговоров. Пустой `q` даёт `[]`.

**Действия.**

- `keys.combo`: модификаторы `ctrl shift alt win`, клавиши `esc enter tab space backspace delete insert home
  end pageup pagedown up down left right capslock printscreen menu pause f1…f20 volumeup volumedown
  volumemute playpause nexttrack prevtrack` или один символ; до 5 клавиш. `keys.text` печатается через
  pynput, а `dictate` — через вставку плагина `apps` (надёжнее в Блокноте Windows 11).
- `open`: адрес `http(s)://` открывается в браузере, существующий путь — как есть, остальное ищет плагин
  `apps` (программы, сайты, папки, его «Свои команды»). Если `apps` выключен, такие действия завершаются
  ошибкой.
- `window`: `minimize`, `close`, `focus` — через плагин `apps`; `maximize` — переключиться на окно и развернуть.
- `volume`: системная громкость — как в плагине `system`; `app` — громкость сессий программы в микшере
  Windows по имени процесса (`spotify`, `discord.exe`).
- `shell`: `cmd` запускается через `cmd /c`, `powershell` — через `-EncodedCommand` (без проблем с
  кавычками), окно не показывается. Таймаут — `timeout` действия или `commands.shell_timeout`. Код выхода не 0
  — ошибка шага. При отмене или таймауте дерево процессов завершается (`taskkill /T /F`).
- `delete`: только в корзину (`SHFileOperationW` с `FOF_ALLOWUNDO`). Корни дисков, папка пользователя целиком,
  `Windows`, `Program Files` и `ProgramData` не удаляются.
- Пути в `file`, `delete`, `play` — полные; `~` и `%USERPROFILE%` раскрываются.
- Команда без `say` выполняется молча: шаги видны в «Ходе работы», при ошибке звучит «Не получилось: …».
