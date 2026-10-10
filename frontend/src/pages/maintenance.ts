// Self-update and the on-demand CUDA download: panels with live progress.

import { api } from "../api";
import { h, toast } from "../dom";
import { store } from "../store";
import type { BusEvent, CudaStatus, Job, UpdateStatus } from "../types";
import { panel } from "./common";

const mb = (bytes: number): string => `${Math.round(bytes / 1e6)} МБ`;

/** Progress bar + caption of a background download; null when there is no job. */
function jobView(job: Job | null | undefined): HTMLElement | null {
  if (!job) return null;
  if (job.state === "error") return h("div", { class: "banner" }, `Ошибка: ${job.error ?? ""}`);
  const share = job.total ? Math.min(1, job.done / job.total) : 0;
  const bar = h("i");
  bar.style.width = `${Math.round(share * 100)}%`;
  const caption =
    job.state === "done"
      ? "Готово"
      : `${job.label}: ${mb(job.done)}${job.total ? ` из ${mb(job.total)}` : ""}`;
  return h(
    "div",
    { style: "display:grid;gap:6px;margin-top:12px" },
    h("div", { class: "meter" }, bar),
    h("div", { class: "muted mono", style: "font-size:13px" }, caption),
  );
}

function jobFromEvent(e: BusEvent): Job {
  return {
    state: e.state as Job["state"],
    label: String(e.label ?? ""),
    done: Number(e.done ?? 0),
    total: Number(e.total ?? 0),
    error: e.error as string | undefined,
  };
}

/** "Updates" panel; returns the element and an unsubscribe function. */
export function updatePanel(): [HTMLElement, () => void] {
  const root = panel("Обновления");
  const content = h("div");
  root.append(content);
  let status: UpdateStatus | null = null;
  let checking = false;

  const render = (): void => {
    if (!status) return;
    const s = status;
    const running = s.job?.state === "running" || s.job?.state === "done";
    const lines: (HTMLElement | null)[] = [];
    if (!s.supported) {
      lines.push(
        h(
          "p",
          { class: "muted" },
          s.edition === "dev"
            ? "Версия для разработки: обновляется через git, установщик не нужен."
            : "Обновление из программы работает в версии, установленной установщиком.",
        ),
      );
    }
    if (s.latest) {
      lines.push(
        h(
          "p",
          null,
          h("b", null, `Доступна версия ${s.latest.version}`),
          ` (${mb(s.latest.size)})`,
        ),
      );
      if (s.latest.notes) {
        lines.push(
          h("pre", { class: "mono muted", style: "white-space:pre-wrap" }, s.latest.notes),
        );
      }
    } else if (s.checked_at && !s.error) {
      lines.push(h("p", { class: "muted" }, "У вас последняя версия."));
    }
    if (s.error) lines.push(h("div", { class: "banner" }, s.error));
    const buttons = h(
      "div",
      { class: "row" },
      h(
        "button",
        {
          class: "btn ghost",
          disabled: checking || running,
          onclick: () => void check(),
        },
        checking ? "Проверяю…" : "Проверить обновления",
      ),
      s.latest && s.supported
        ? h(
            "button",
            { class: "btn", disabled: running, onclick: () => void install() },
            "Обновить и перезапустить",
          )
        : null,
    );
    content.replaceChildren(...lines.filter((l) => l !== null), buttons);
    const job = jobView(s.job);
    if (job) content.append(job);
  };

  const load = async (): Promise<void> => {
    status = await api.get<UpdateStatus>("/api/update");
    render();
  };
  const check = async (): Promise<void> => {
    checking = true;
    render();
    try {
      status = await api.post<UpdateStatus>("/api/update/check");
    } finally {
      checking = false;
      render();
    }
  };
  const install = async (): Promise<void> => {
    try {
      await api.post("/api/update/install");
    } catch (err) {
      toast("Не удалось обновить", err instanceof Error ? err.message : String(err), "error");
    }
  };

  void load().catch((err) => content.replaceChildren(h("div", { class: "banner" }, String(err))));
  const unsubs = [
    store.on("job", (e) => {
      if (e.name !== "update" || !status) return;
      status.job = jobFromEvent(e);
      if (status.job.state === "done") toast("Устанавливаю обновление", "Aion перезапустится");
      render();
    }),
    store.on("update_available", () => void load()),
  ];
  return [root, () => unsubs.forEach((u) => u())];
}

/** GPU acceleration panel: shown when there is an NVIDIA GPU but no CUDA libraries. */
export function cudaPanel(): [HTMLElement, () => void] {
  const root = h("div");
  let status: CudaStatus | null = null;

  const render = (): void => {
    const s = status;
    if (!s || !s.gpu || (s.available && !s.job)) {
      root.replaceChildren();
      return;
    }
    const running = s.job?.state === "running";
    const done = s.job?.state === "done";
    root.replaceChildren(
      panel(
        "Ускорение на видеокарте",
        h(
          "p",
          { class: "muted" },
          done
            ? "Библиотеки CUDA установлены. Перезапустите Aion — распознавание переедет на видеокарту."
            : `Найдена видеокарта NVIDIA. Скачайте библиотеки CUDA (${mb(s.size)}), ` +
                "чтобы распознавание речи работало быстрее и точнее (модель large-v3-turbo).",
        ),
        done
          ? null
          : h(
              "button",
              { class: "btn", disabled: running, onclick: () => void download() },
              running ? "Скачиваю…" : "Скачать",
            ),
        jobView(s.job),
      ),
    );
  };

  const download = async (): Promise<void> => {
    try {
      await api.post("/api/cuda/download");
    } catch (err) {
      toast("Не удалось скачать", err instanceof Error ? err.message : String(err), "error");
    }
  };

  void api
    .get<CudaStatus>("/api/cuda")
    .then((s) => {
      status = s;
      render();
    })
    .catch(() => undefined);
  const unsub = store.on("job", (e) => {
    if (e.name !== "cuda" || !status) return;
    status.job = jobFromEvent(e);
    render();
  });
  return [root, unsub];
}
