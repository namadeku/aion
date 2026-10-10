import { api } from "../api";
import { field, h, range, select, text, toast, toggle } from "../dom";
import type { Config } from "../types";
import { failure, loadConfig, pageShell, panel, patchConfig } from "./common";

export function llmPage(root: HTMLElement): void {
  const body = pageShell(
    root,
    "Языковая модель",
    "«Мозг» для свободного разговора и вызова навыков. Ollama работает полностью офлайн.",
  );
  void loadConfig().then(render, (err) => failure(body, err));

  async function render(cfg: Config): Promise<void> {
    const llm = cfg.llm;
    const save = async (patch: Record<string, unknown>): Promise<void> => {
      const next = await patchConfig({ llm: patch });
      if (next) void render(next);
    };
    const initiative = cfg.initiative;
    const saveInitiative = async (patch: Record<string, unknown>): Promise<void> => {
      const next = await patchConfig({ initiative: patch });
      if (next) void render(next);
    };
    const secrets = await api
      .get<Record<string, boolean>>("/api/secrets")
      .catch(() => ({}) as Record<string, boolean>);

    const keyInput = (envName: string): HTMLElement => {
      const input = h("input", {
        type: "password",
        autocomplete: "off",
        placeholder: secrets[envName] ? "•••••••• (ключ задан)" : "не задан",
      });
      return h(
        "div",
        { class: "row" },
        h("div", { style: "flex:1" }, input),
        h(
          "button",
          {
            class: "btn ghost",
            onclick: async () => {
              try {
                await api.put("/api/secrets", { name: envName, value: input.value });
                toast("Ключ сохранён", `в ${envName} (файл .env в папке данных)`, "success");
                void render(cfg);
              } catch (err) {
                toast("Ошибка", err instanceof Error ? err.message : String(err), "error");
              }
            },
          },
          "Сохранить",
        ),
      );
    };

    let providerFields: HTMLElement;
    if (llm.provider === "ollama") {
      const models = await api
        .get<string[]>("/api/llm/models?provider=ollama")
        .catch(() => [] as string[]);
      providerFields = h(
        "div",
        { class: "grid" },
        field(
          "Адрес Ollama",
          text(llm.ollama.base_url, (v) => void save({ ollama: { base_url: v } })),
        ),
        field(
          "Модель",
          models.length
            ? select(models, llm.ollama.model, (v) => void save({ ollama: { model: v } }))
            : text(llm.ollama.model, (v) => void save({ ollama: { model: v } })),
          models.length
            ? "Модели, установленные в Ollama"
            : `Ollama не отвечает или моделей нет. Установите ollama.com и выполните: ollama pull ${llm.ollama.model}`,
        ),
      );
    } else if (llm.provider === "anthropic") {
      providerFields = h(
        "div",
        { class: "grid" },
        field(
          "Модель Claude",
          text(llm.anthropic.model, (v) => void save({ anthropic: { model: v } })),
        ),
        field(
          "Глубина рассуждений",
          select(
            [
              ["low", "низкая — быстрый голосовой ответ"],
              ["medium", "средняя"],
              ["high", "высокая"],
            ],
            llm.anthropic.effort,
            (v) => void save({ anthropic: { effort: v } }),
          ),
        ),
        field(
          `API-ключ (${llm.anthropic.api_key_env})`,
          keyInput(llm.anthropic.api_key_env),
          "",
          true,
        ),
      );
    } else if (llm.provider === "openai") {
      providerFields = h(
        "div",
        { class: "grid" },
        field(
          "Адрес API",
          text(llm.openai.base_url, (v) => void save({ openai: { base_url: v } })),
          "OpenAI, LM Studio, vLLM, OpenRouter…",
        ),
        field(
          "Модель",
          text(llm.openai.model, (v) => void save({ openai: { model: v } })),
        ),
        field(`API-ключ (${llm.openai.api_key_env})`, keyInput(llm.openai.api_key_env), "", true),
      );
    } else {
      providerFields = h(
        "p",
        { class: "muted" },
        "LLM выключена: работают только команды плагинов.",
      );
    }

    const result = h("div", { class: "muted mono", style: "font-size:13px;margin-top:10px" });
    const testBtn = h(
      "button",
      {
        class: "btn",
        onclick: async () => {
          testBtn.disabled = true;
          result.textContent = "Спрашиваю модель…";
          try {
            const r = await api.post<{
              ok: boolean;
              answer?: string;
              error?: string;
              seconds?: number;
            }>("/api/llm/test");
            result.textContent = r.ok ? `✓ «${r.answer}» за ${r.seconds} с` : `✗ ${r.error}`;
          } catch (err) {
            result.textContent = `✗ ${err instanceof Error ? err.message : err}`;
          } finally {
            testBtn.disabled = false;
          }
        },
      },
      "Проверить",
    );

    body.replaceChildren(
      panel(
        "Провайдер",
        h(
          "div",
          { class: "grid" },
          field(
            "Где работает модель",
            select(
              [
                ["ollama", "Ollama — локально, офлайн"],
                ["anthropic", "Claude (Anthropic API)"],
                ["openai", "OpenAI-совместимый API"],
                ["none", "Выключена"],
              ],
              llm.provider,
              (v) => void save({ provider: v }),
            ),
          ),
        ),
        h("div", { style: "margin-top:14px" }, providerFields),
        h("div", { class: "row", style: "margin-top:14px" }, testBtn),
        result,
      ),
      panel(
        "Поведение",
        h(
          "div",
          { class: "grid" },
          field(
            "Температура",
            range(llm.temperature, 0, 1.5, 0.05, (v) => void save({ temperature: v })),
          ),
          field(
            "Длина ответа (токенов)",
            range(
              llm.max_tokens,
              100,
              2000,
              50,
              (v) => void save({ max_tokens: v }),
              (v) => String(v),
            ),
          ),
          field(
            "Память диалога (обменов)",
            range(
              llm.history_turns,
              0,
              30,
              1,
              (v) => void save({ history_turns: v }),
              (v) => String(v),
            ),
          ),
          field(
            "Разрешить навыки (инструменты)",
            toggle(llm.tools, (v) => void save({ tools: v })),
            "Модель сможет ставить таймеры, узнавать погоду и т. д.",
          ),
        ),
      ),
      panel(
        "Комментарии к событиям",
        h(
          "div",
          { class: "grid" },
          field(
            "Включены",
            toggle(initiative.enabled, (v) => void saveInitiative({ enabled: v })),
            "Реплики без вопроса: события в играх, напоминания плагинов",
          ),
          field(
            "Болтливость",
            range(
              initiative.talkativeness,
              0,
              1,
              0.05,
              (v) => void saveInitiative({ talkativeness: v }),
              (v) => (v < 0.25 ? "только важное" : v < 0.7 ? "в меру" : "болтушка"),
            ),
            "Как часто и о каких мелочах говорить",
          ),
          field(
            "Сочинять реплики моделью",
            toggle(initiative.use_llm, (v) => void saveInitiative({ use_llm: v })),
            "Выключите, если в игре не хватает видеокарты: будут готовые фразы",
          ),
          field(
            "Ждать модель не дольше",
            range(
              initiative.llm_timeout,
              1,
              10,
              0.5,
              (v) => void saveInitiative({ llm_timeout: v }),
              (v) => `${v} с`,
            ),
            "Потом прозвучит готовая фраза — комментарий не опоздает",
          ),
        ),
      ),
    );
  }
}
