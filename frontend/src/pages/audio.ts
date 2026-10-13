import { api } from "../api";
import { field, h, range, select, toast, toggle } from "../dom";
import { store } from "../store";
import type { Config, Device } from "../types";
import { failure, loadConfig, pageShell, panel, patchConfig } from "./common";
import { cudaPanel } from "./maintenance";

export function audioPage(root: HTMLElement): () => void {
  const body = pageShell(
    root,
    "Распознавание и микрофон",
    "Устройства, чувствительность, способ активации и модель распознавания речи.",
  );
  const meter = h("i");
  const lastHeard = h("div", { class: "muted mono", style: "font-size:13px;min-height:20px" });
  const [cuda, unsubCuda] = cudaPanel();
  const unsubs = [
    unsubCuda,
    store.on("audio_level", (e) => {
      if (e.channel === "input") meter.style.width = `${Math.round((e.level as number) * 100)}%`;
    }),
    store.on("speech_recognized", (e) => {
      if (e.source === "voice") lastHeard.textContent = `Распознано: «${e.text}»`;
    }),
    store.on("wake_detected", (e) => {
      lastHeard.textContent = `Имя услышано: ${e.word}`;
    }),
  ];

  Promise.all([loadConfig(), api.get<Device[]>("/api/devices").catch(() => [] as Device[])]).then(
    ([cfg, devices]) => render(cfg, devices),
    (err) => failure(body, err),
  );

  function render(cfg: Config, devices: Device[]): void {
    const save = async (patch: Record<string, unknown>, restart = true): Promise<void> => {
      const next = await patchConfig(patch, true);
      if (next) {
        toast(
          "Сохранено",
          restart ? "Применится после перезапуска голосового режима" : "",
          "success",
        );
        render(next, devices);
      }
    };
    const deviceOptions = (kind: "inputs" | "outputs"): [string, string][] => [
      ["", "Системное по умолчанию"],
      ...devices
        .filter((d) => d[kind] > 0)
        .map((d): [string, string] => [String(d.index), `${d.name} · ${d.hostapi}`]),
    ];

    const parts: (HTMLElement | null)[] = [
      store.voiceEnabled
        ? null
        : h(
            "div",
            { class: "banner" },
            "Голосовой режим не запущен (aion text?). Запустите: aion run",
          ),
      panel(
        "Устройства",
        h(
          "div",
          { class: "grid" },
          field(
            "Микрофон",
            select(
              deviceOptions("inputs"),
              String(cfg.audio.input_device ?? ""),
              (v) => void save({ audio: { input_device: v === "" ? null : Number(v) } }),
            ),
          ),
          field(
            "Вывод звука",
            select(
              deviceOptions("outputs"),
              String(cfg.audio.output_device ?? ""),
              (v) => void save({ audio: { output_device: v === "" ? null : Number(v) } }),
            ),
          ),
          field(
            "Уровень микрофона",
            h("div", { class: "meter" }, meter),
            "Скажите что-нибудь — полоска должна двигаться",
            true,
          ),
        ),
        lastHeard,
      ),
      panel(
        "Активация по имени",
        h(
          "div",
          { class: "grid" },
          field(
            "Только кнопка или горячая клавиша",
            toggle(
              cfg.wakeword.backend === "none",
              (v) => void save({ wakeword: { backend: v ? "none" : "stt" } }),
            ),
            "Имя не слушается совсем: ассистент реагирует только на «Нажми и говори». Тише и экономнее.",
            true,
          ),
          field(
            "Способ",
            select(
              [
                ["stt", "Распознаванием (любое имя, точно)"],
                ["vosk", "Vosk (легче, имя должно быть в словаре)"],
                ["openwakeword", "openWakeWord (обученная модель)"],
                ["none", "Без имени — только кнопка/горячая клавиша"],
              ],
              cfg.wakeword.backend,
              (v) => void save({ wakeword: { backend: v } }),
            ),
          ),
          field(
            "Имя в конце фразы",
            toggle(cfg.wakeword.tail_name, (v) => void save({ wakeword: { tail_name: v } }, false)),
            "Принимать имя и после команды: «который час, Аион» (только для известных команд)",
          ),
          field(
            "Чувствительность",
            range(
              cfg.wakeword.sensitivity,
              0,
              1,
              0.05,
              (v) => void save({ wakeword: { sensitivity: v } }, false),
              (v) => `${Math.round(v * 100)}%`,
            ),
            "Выше — реагирует на похожие слова, но чаще ошибается",
          ),
          field(
            "Перебивание",
            select(
              [
                ["wake", "Назвать имя"],
                ["speech", "Любой речью (в наушниках)"],
                ["off", "Не перебивать"],
              ],
              cfg.audio.barge_in,
              (v) => void save({ audio: { barge_in: v } }, false),
            ),
          ),
          field(
            "Продолжение диалога",
            range(
              cfg.assistant.follow_up_seconds,
              0,
              15,
              1,
              (v) => void save({ assistant: { follow_up_seconds: v } }, false),
              (v) => (v ? `${v} с` : "выкл."),
            ),
            "Сколько слушать без имени после ответа",
          ),
        ),
      ),
      panel(
        "Детектор речи",
        h(
          "div",
          { class: "grid" },
          field(
            "Алгоритм",
            select(
              [
                ["silero", "Silero VAD (точный)"],
                ["webrtc", "WebRTC VAD"],
                ["energy", "По громкости"],
              ],
              cfg.audio.vad.backend,
              (v) => void save({ audio: { vad: { backend: v } } }),
            ),
          ),
          field(
            "Порог речи",
            range(
              cfg.audio.vad.threshold,
              0.1,
              0.9,
              0.05,
              (v) => void save({ audio: { vad: { threshold: v } } }, false),
              (v) => v.toFixed(2),
            ),
            "Выше — меньше ложных срабатываний на шум. Применяется сразу",
          ),
          field(
            "Пауза конца фразы",
            range(
              cfg.audio.vad.min_silence_ms,
              200,
              1500,
              50,
              (v) => void save({ audio: { vad: { min_silence_ms: v } } }, false),
              (v) => `${v} мс`,
            ),
            "Меньше — быстрее ответ, но может перебить вас на паузе. Применяется сразу",
          ),
        ),
      ),
      panel(
        "Звук",
        h(
          "div",
          { class: "grid" },
          field(
            "Выравнивать тихую речь",
            toggle(cfg.audio.normalize, (v) => void save({ audio: { normalize: v } })),
            "Поднимает уровень тихих фраз (до +30 дБ) перед распознаванием",
          ),
          field(
            "Шумоподавление",
            toggle(cfg.audio.denoise, (v) => void save({ audio: { denoise: v } })),
            "Спектральный гейт: помогает на ровном шуме (вентилятор, гул)",
          ),
          field(
            "Ранний старт распознавания",
            range(
              cfg.audio.speculative_ms,
              0,
              400,
              50,
              (v) => void save({ audio: { speculative_ms: v } }),
              (v) => (v ? `${v} мс` : "выкл."),
            ),
            "Начать распознавать после такой паузы, не ждать конца фразы. Ответ быстрее почти на 40%",
          ),
          field(
            "Эхо после своей речи",
            range(
              cfg.audio.echo_holdoff_s,
              0,
              3,
              0.1,
              (v) => void save({ audio: { echo_holdoff_s: v } }),
              (v) => `${v.toFixed(1)} с`,
            ),
            "Сколько секунд после голоса ассистента услышанное считается эхом. Без наушников держите 0,6 с и больше",
          ),
        ),
      ),
      cuda,
      panel(
        "Распознавание речи",
        h(
          "div",
          { class: "grid" },
          field(
            "Движок",
            select(
              [
                ["whisper", "faster-whisper (точный)"],
                ["vosk", "Vosk (лёгкий)"],
              ],
              cfg.stt.backend,
              (v) => void save({ stt: { backend: v } }),
            ),
          ),
          field(
            "Модель Whisper",
            select(
              [
                ["auto", "авто (turbo на GPU, small на CPU)"],
                ["large-v3-turbo", "large-v3-turbo"],
                ["medium", "medium"],
                ["small", "small"],
                ["base", "base"],
                ["tiny", "tiny"],
              ],
              cfg.stt.whisper_model,
              (v) => void save({ stt: { whisper_model: v } }),
            ),
          ),
          field(
            "Устройство",
            select(
              [
                ["auto", "авто"],
                ["cuda", "GPU (CUDA)"],
                ["cpu", "CPU"],
              ],
              cfg.stt.device,
              (v) => void save({ stt: { device: v } }),
            ),
          ),
          field(
            "Точность вычислений",
            select(
              [
                ["auto", "авто"],
                ["int8", "int8 (быстро на CPU)"],
                ["int8_float16", "int8_float16"],
                ["float16", "float16 (GPU)"],
                ["float32", "float32 (точнее, медленнее)"],
              ],
              cfg.stt.compute_type,
              (v) => void save({ stt: { compute_type: v } }),
            ),
            "Для обычной работы оставьте «авто»",
          ),
          field(
            "Ширина поиска (beam)",
            range(
              cfg.stt.beam_size,
              0,
              10,
              1,
              (v) => void save({ stt: { beam_size: v } }),
              (v) => (v ? String(v) : "авто"),
            ),
            "Авто: 5 на GPU и 1 на CPU. Больше — точнее, но дольше",
          ),
          field(
            "Потоки процессора",
            range(
              cfg.stt.cpu_threads,
              0,
              16,
              1,
              (v) => void save({ stt: { cpu_threads: v } }),
              (v) => (v ? String(v) : "авто"),
            ),
            "Сколько потоков отдать Whisper на CPU",
          ),
        ),
      ),
      panel(
        "Горячая клавиша и запуск",
        h(
          "div",
          { class: "grid" },
          field(
            "Нажми и говори",
            h("input", {
              type: "text",
              value: cfg.ui.push_to_talk ?? "",
              placeholder: "<ctrl>+<alt>+<space>",
              onchange: (e: Event) =>
                void save({ ui: { push_to_talk: (e.target as HTMLInputElement).value || null } }),
            }),
            "Формат pynput, например <ctrl>+<alt>+<space>",
          ),
          field(
            "Автозапуск с Windows",
            toggle(cfg.ui.autostart, (v) => void save({ ui: { autostart: v } }, false)),
          ),
        ),
      ),
    ];
    body.replaceChildren(...parts.filter((p): p is HTMLElement => p !== null));
  }

  return () => unsubs.forEach((u) => u());
}
