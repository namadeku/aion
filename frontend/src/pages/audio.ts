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
              (v) => void save({ audio: { vad: { threshold: v } } }),
              (v) => v.toFixed(2),
            ),
          ),
          field(
            "Пауза конца фразы",
            range(
              cfg.audio.vad.min_silence_ms,
              200,
              1500,
              50,
              (v) => void save({ audio: { vad: { min_silence_ms: v } } }),
              (v) => `${v} мс`,
            ),
            "Меньше — быстрее ответ, но может перебить вас на паузе",
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
