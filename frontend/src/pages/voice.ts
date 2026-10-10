import { api } from "../api";
import { field, h, range, select, text, toast } from "../dom";
import type { Config, Profile, VoiceInfo } from "../types";
import { failure, loadConfig, pageShell, panel, patchConfig } from "./common";

const ENGINES: [string, string][] = [
  ["piper", "Piper — быстрый, офлайн"],
  ["silero", "Silero — лучшие русские голоса (нужен torch)"],
  ["edge", "Edge TTS — онлайн, много голосов"],
  ["xtts", "XTTS — клон голоса по образцу WAV (GPU)"],
];
const EFFECTS: [string, string][] = [
  ["none", "без эффекта"],
  ["metallic", "металлический (как у Джарвиса)"],
  ["radio", "рация"],
];
const AVATARS: [string, string][] = [
  ["hud", "Голографический круг (HUD)"],
  ["vrm", "3D-модель VRM"],
  ["live2d", "Live2D (.model3.json)"],
];

export function voicePage(root: HTMLElement): void {
  const body = pageShell(
    root,
    "Голос и персонаж",
    "Имя, голос, внешность и характер. Профилей может быть несколько — переключайтесь между ними.",
  );
  void loadConfig().then(render, (err) => failure(body, err));

  function render(cfg: Config): void {
    const id =
      cfg.assistant.profile in cfg.profiles ? cfg.assistant.profile : Object.keys(cfg.profiles)[0];
    const profile = cfg.profiles[id];
    const patchProfile = async (
      patch: Partial<Profile> | Record<string, unknown>,
    ): Promise<void> => {
      const next = await patchConfig({ profiles: { [id]: patch } }, true);
      if (next) render(next);
    };

    // -- profiles ---------------------------------------------------------------------------
    const profiles = panel(
      "Профиль персонажа",
      h(
        "div",
        { class: "row" },
        select(
          Object.entries(cfg.profiles).map(([pid, p]) => [pid, `${p.name} (${pid})`]),
          id,
          async (v) => {
            const next = await patchConfig({ assistant: { profile: v } });
            if (next) render(next);
          },
        ),
        h(
          "button",
          {
            class: "btn ghost",
            onclick: async () => {
              const pid = prompt("Идентификатор нового профиля (латиницей), например friday:");
              if (!pid) return;
              try {
                const next = await api.post<Config>(`/api/profiles/${pid}`, { name: pid });
                toast(
                  "Профиль создан",
                  "Это копия текущего — поменяйте имя, голос и характер",
                  "success",
                );
                const switched = await patchConfig({ assistant: { profile: pid } }, true);
                render(switched ?? next);
              } catch (err) {
                toast("Ошибка", err instanceof Error ? err.message : String(err), "error");
              }
            },
          },
          "Новый профиль",
        ),
        h(
          "button",
          {
            class: "btn danger",
            disabled: Object.keys(cfg.profiles).length < 2,
            onclick: async () => {
              if (!confirm(`Удалить профиль ${profile.name}?`)) return;
              try {
                render(await api.del<Config>(`/api/profiles/${id}`));
              } catch (err) {
                toast("Ошибка", err instanceof Error ? err.message : String(err), "error");
              }
            },
          },
          "Удалить",
        ),
      ),
    );

    // -- identity ---------------------------------------------------------------------------
    const identity = panel(
      "Имя и обращение",
      h(
        "div",
        { class: "grid" },
        field(
          "Имя ассистента",
          text(profile.name, (v) => void patchProfile({ name: v.trim() || profile.name })),
        ),
        field(
          "Говорит о себе",
          select(
            [
              ["female", "в женском роде («поняла»)"],
              ["male", "в мужском роде («понял»)"],
            ],
            profile.gender,
            (v) => void patchProfile({ gender: v }),
          ),
        ),
        field(
          "Варианты произношения",
          text(
            profile.aliases.join(", "),
            (v) =>
              void patchProfile({
                aliases: v
                  .split(",")
                  .map((s) => s.trim().toLowerCase())
                  .filter(Boolean),
              }),
          ),
          "Как распознаватель может услышать имя — через запятую",
        ),
        field(
          "Обращение к вам",
          text(
            cfg.assistant.user_address,
            (v) => void patchConfig({ assistant: { user_address: v } }),
          ),
          "Например: сэр, мадам, Тони",
        ),
      ),
    );

    // -- voice ------------------------------------------------------------------------------
    const voiceSelect = h("select", {}, h("option", null, "загрузка…"));
    const loadVoices = async (engine: string): Promise<void> => {
      try {
        const voices = await api.get<VoiceInfo[]>(`/api/voices?engine=${engine}`);
        voiceSelect.replaceChildren(
          ...voices.map((v) =>
            h(
              "option",
              { value: v.id, selected: v.id === profile.voice.voice },
              `${v.name}${v.gender ? " · " + (v.gender === "male" ? "муж." : v.gender === "female" ? "жен." : v.gender) : ""}${v.installed ? "" : " · скачается"}`,
            ),
          ),
        );
        if (!voices.some((v) => v.id === profile.voice.voice)) {
          voiceSelect.prepend(
            h("option", { value: profile.voice.voice, selected: true }, profile.voice.voice),
          );
        }
      } catch (err) {
        voiceSelect.replaceChildren(
          h("option", { value: profile.voice.voice }, profile.voice.voice),
        );
        toast("Голоса не загрузились", err instanceof Error ? err.message : String(err), "warning");
      }
    };
    voiceSelect.addEventListener(
      "change",
      () => void patchProfile({ voice: { voice: voiceSelect.value } }),
    );
    void loadVoices(profile.voice.engine);

    const previewText = text(
      "Добрый вечер, сэр. Все системы работают в штатном режиме.",
      () => undefined,
    );
    const player = h("audio", { controls: true, style: "width:100%;margin-top:6px", hidden: true });
    const previewBtn = h(
      "button",
      {
        class: "btn",
        onclick: async () => {
          previewBtn.disabled = true;
          previewBtn.textContent = "Синтез…";
          try {
            const blob = await api.post<Blob>("/api/voice/preview", { text: previewText.value });
            player.src = URL.createObjectURL(blob);
            player.hidden = false;
            await player.play();
          } catch (err) {
            toast("Не получилось", err instanceof Error ? err.message : String(err), "error");
          } finally {
            previewBtn.disabled = false;
            previewBtn.textContent = "Прослушать";
          }
        },
      },
      "Прослушать",
    );

    const voice = panel(
      "Голос",
      h(
        "div",
        { class: "grid" },
        field(
          "Движок",
          select(ENGINES, profile.voice.engine, (v) => {
            const defaults: Record<string, string> = {
              piper: "ru_RU-denis-medium",
              silero: "aidar",
              edge: "ru-RU-DmitryNeural",
              xtts: "clone",
            };
            void patchProfile({ voice: { engine: v, voice: defaults[v] ?? "" } });
          }),
        ),
        field("Голос", voiceSelect),
        field(
          "Скорость",
          range(
            profile.voice.rate,
            0.5,
            2,
            0.05,
            (v) => void patchProfile({ voice: { rate: v } }),
            (v) => `${v.toFixed(2)}×`,
          ),
        ),
        field(
          "Высота тона",
          range(
            profile.voice.pitch,
            -6,
            6,
            0.5,
            (v) => void patchProfile({ voice: { pitch: v } }),
            (v) => `${v > 0 ? "+" : ""}${v} пт`,
          ),
        ),
        field(
          "Громкость",
          range(
            profile.voice.volume,
            0,
            2,
            0.05,
            (v) => void patchProfile({ voice: { volume: v } }),
            (v) => `${Math.round(v * 100)}%`,
          ),
        ),
        field(
          "Эффект",
          select(EFFECTS, profile.voice.effect, (v) => void patchProfile({ voice: { effect: v } })),
        ),
        profile.voice.engine === "xtts"
          ? field(
              "Образец голоса (WAV)",
              text(
                profile.voice.reference_wav ?? "",
                (v) => void patchProfile({ voice: { reference_wav: v || null } }),
              ),
              "Путь к 6–30 секундам чистой речи",
              true,
            )
          : null,
        field(
          "Тестовая фраза",
          h("div", { class: "row" }, h("div", { style: "flex:1" }, previewText), previewBtn),
          "",
          true,
        ),
      ),
      player,
    );

    // -- persona & avatar --------------------------------------------------------------------
    const persona = h(
      "textarea",
      { rows: 6, onchange: () => void patchProfile({ persona: persona.value }) },
      profile.persona,
    );
    const character = panel(
      "Характер",
      field("Системный промт", persona, "{name} и {address} подставляются автоматически", true),
    );

    const avatar = panel(
      "Аватар",
      h(
        "div",
        { class: "grid" },
        field(
          "Тип",
          select(AVATARS, profile.avatar.kind, (v) => void patchProfile({ avatar: { kind: v } })),
        ),
        field(
          "Файл модели",
          text(
            profile.avatar.path ?? "",
            (v) => void patchProfile({ avatar: { path: v.trim() || null } }),
            {
              placeholder: "C:\\models\\avatar.vrm",
            },
          ),
          "Абсолютный путь к .vrm или .model3.json",
        ),
        field(
          "Рост на рабочем столе",
          range(
            cfg.ui.desktop_scale,
            0.15,
            0.9,
            0.01,
            (v) => void patchConfig({ ui: { desktop_scale: v } }),
            (v) => `${Math.round(v * 100)}% экрана`,
          ),
          "Применяется при следующем выходе на рабочий стол",
        ),
      ),
      h(
        "p",
        { class: "muted", style: "font-size:13px;margin:10px 0 0" },
        "VRM-модели можно сделать в VRoid Studio или найти на VRoid Hub. Для Live2D положите рядом с моделью ",
        h("code", { class: "mono" }, "live2dcubismcore.min.js"),
        " из Cubism SDK for Web (лицензия Live2D не позволяет распространять его с Aion).",
      ),
    );

    body.replaceChildren(profiles, identity, voice, character, avatar);
  }
}
