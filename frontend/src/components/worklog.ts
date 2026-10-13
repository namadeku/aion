// "Ход работы": a collapsible live log of what the assistant does for the current request
// (heard → command/tool calls → thinking → speaking) with an elapsed timer.
//
// The component knows nothing about the backend: feed it with begin()/step()/end().
// bindWorkLog() maps the bus events to it: the voice pipeline events (listening, tts…) and
// the run/step/confirm events of docs/commands-api.md (section 5).

import { h, icons, svgIcon } from "../dom";
import type { BusEvent, State } from "../types";

export type StepKind =
  | "heard"
  | "listen"
  | "intent"
  | "tool"
  | "think"
  | "speak"
  | "reply"
  | "note"
  | "action"
  | "confirm";
export type StepStatus = "running" | "done" | "error" | "cancelled" | "skipped";
export type RunStatus = "running" | "done" | "error" | "interrupted";

/** An open yes/no question to the user (a dangerous action waits for the answer). */
export interface ConfirmState {
  /** Epoch milliseconds when the backend gives up and cancels. */
  deadline: number;
  question: string;
  /** The user has answered; wait for the backend to close the step. */
  sent: boolean;
}

export interface WorkStep {
  id: number;
  kind: StepKind;
  title: string;
  /** Secondary line: arguments, recognized text, an error message, shell output. */
  detail?: string;
  status: StepStatus;
  /** Epoch milliseconds. */
  startedAt: number;
  endedAt?: number;
  /** Started and finished by the backend (run/step ids): not closed by the next step. */
  external?: boolean;
  confirm?: ConfirmState;
}

export interface WorkRun {
  /** Backend run id; absent for runs the UI inferred from voice events. */
  runId?: string;
  title?: string;
  status: RunStatus;
  startedAt: number;
  endedAt?: number;
  steps: WorkStep[];
}

export interface StepInput {
  kind: StepKind;
  title: string;
  detail?: string;
  /** "running" keeps the step open until the next step, end() or finishStep(). */
  status?: StepStatus;
  /** Epoch milliseconds of the event (defaults to now). */
  at?: number;
  /** Backend step: stays open next to other steps until finishStep(). */
  external?: boolean;
}

const RUN_LABEL: Record<RunStatus, string> = {
  running: "Выполняется",
  done: "Готово",
  error: "Ошибка",
  interrupted: "Прервано",
};

const STEP_ICON: Record<StepKind, string> = {
  heard: icons.voice,
  listen: icons.mic,
  intent: icons.chevron,
  tool: icons.tool,
  think: icons.brain,
  speak: icons.voice,
  reply: icons.send,
  note: icons.info,
  action: icons.bolt,
  confirm: icons.question,
};

const seconds = (ms: number): string =>
  `${(Math.max(0, ms) / 1000).toLocaleString("ru-RU", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} с`;

const clip = (text: string, max = 140): string =>
  text.length > max ? `${text.slice(0, max - 1)}…` : text;

export class WorkLog {
  readonly el: HTMLElement;
  /** Called when the user presses «Да» or «Нет» on a confirmation step. */
  onAnswer: ((run: WorkRun, step: WorkStep, answer: boolean) => void) | null = null;
  private run: WorkRun | null = null;
  private nextId = 1;
  private expanded = false;
  private timer = 0;
  private readonly toggle: HTMLButtonElement;
  private readonly statusEl: HTMLElement;
  private readonly timeEl: HTMLElement;
  private readonly titleEl: HTMLElement;
  private readonly list: HTMLOListElement;
  private readonly live: HTMLElement;
  private countdowns: { el: HTMLElement; step: WorkStep }[] = [];

  constructor() {
    this.statusEl = h("span", { class: "worklog-status" });
    this.timeEl = h("span", { class: "worklog-time" });
    this.titleEl = h("span", { class: "worklog-run-title" });
    this.live = h("div", { class: "sr-only", role: "alert" });
    this.list = h("ol", {
      class: "worklog-steps",
      id: `worklog-${Math.random().toString(36).slice(2)}`,
    });
    this.list.hidden = true;
    this.toggle = h(
      "button",
      {
        type: "button",
        class: "worklog-toggle",
        "aria-expanded": "false",
        "aria-controls": this.list.id,
        onclick: () => this.setExpanded(!this.expanded),
      },
      svgIcon(icons.chevron),
      h("span", { class: "worklog-title" }, "Ход работы"),
      this.statusEl,
      this.timeEl,
      this.titleEl,
    );
    this.el = h(
      "section",
      { class: "worklog", "aria-label": "Ход работы", hidden: true },
      this.toggle,
      this.list,
      this.live,
    );
  }

  get current(): WorkRun | null {
    return this.run;
  }

  get running(): boolean {
    return this.run?.status === "running";
  }

  get isExpanded(): boolean {
    return this.expanded;
  }

  /** Start a new run (a new request). An unfinished previous run counts as interrupted. */
  begin(at = Date.now(), runId?: string, title?: string): void {
    if (this.running) this.end("interrupted", at);
    this.run = { runId, title, status: "running", startedAt: at, steps: [] };
    this.startTimer();
    this.render();
  }

  /** Tie the running run (started from voice events) to the backend run id. */
  adopt(runId: string, title?: string): void {
    if (!this.run) return;
    this.run.runId = runId;
    if (title) this.run.title = title;
    this.render();
  }

  /** Add a step; the previous running step is closed. Starts a run if none is running. */
  step(input: StepInput): number {
    const at = input.at ?? Date.now();
    if (!this.running) this.begin(at);
    const run = this.run!;
    // inferred steps (listening, thinking) end when the next one begins; backend steps
    // close themselves with step_finished
    for (const s of run.steps) {
      if (s.status === "running" && !s.external) this.close(s, "done", at);
    }
    const step: WorkStep = {
      id: this.nextId++,
      kind: input.kind,
      title: input.title,
      detail: input.detail,
      status: input.status ?? "done",
      startedAt: at,
      endedAt: input.status === "running" ? undefined : at,
      external: input.external,
    };
    run.steps.push(step);
    if (run.steps.length > 200) run.steps.shift();
    this.render();
    return step.id;
  }

  /** Close a running step (e.g. speech finished, possibly interrupted). */
  finishStep(id: number, status: StepStatus = "done", detail?: string): void {
    const step = this.run?.steps.find((s) => s.id === id);
    if (!step) return;
    this.close(step, status, Date.now());
    if (detail) step.detail = detail;
    step.confirm = undefined;
    this.render();
  }

  /** Show «Да / Нет» with a countdown on a running confirmation step. */
  requestConfirm(id: number, question: string, timeoutSeconds: number): void {
    const step = this.run?.steps.find((s) => s.id === id);
    if (!step || step.status !== "running") return;
    step.confirm = { deadline: Date.now() + timeoutSeconds * 1000, question, sent: false };
    this.setExpanded(true);
    this.live.textContent = question;
    this.render();
  }

  /** The last step of a kind that is still running, if any. */
  openStep(kind: StepKind): number | null {
    const step = this.run?.steps.findLast((s) => s.kind === kind && s.status === "running");
    return step?.id ?? null;
  }

  end(status: Exclude<RunStatus, "running"> = "done", at = Date.now()): void {
    const run = this.run;
    if (!run || run.status !== "running") return;
    const closed: StepStatus =
      status === "interrupted" ? "cancelled" : status === "error" ? "error" : "done";
    for (const s of run.steps) {
      if (s.status !== "running") continue;
      this.close(s, closed, at);
      s.confirm = undefined;
    }
    run.status = status;
    run.endedAt = at;
    this.stopTimer();
    this.render();
  }

  setExpanded(value: boolean): void {
    this.expanded = value;
    this.toggle.setAttribute("aria-expanded", String(value));
    this.el.classList.toggle("open", value);
    this.list.hidden = !value;
    if (value) this.list.scrollTop = this.list.scrollHeight;
  }

  dispose(): void {
    this.stopTimer();
  }

  // -- internals ------------------------------------------------------------------------

  private close(step: WorkStep, status: StepStatus, at: number): void {
    step.status = status;
    step.endedAt = Math.max(at, step.startedAt);
  }

  private startTimer(): void {
    this.stopTimer();
    this.timer = window.setInterval(() => this.tick(), 100);
  }

  private stopTimer(): void {
    if (this.timer) window.clearInterval(this.timer);
    this.timer = 0;
  }

  private tick(): void {
    this.renderTime();
    let expired = false;
    for (const { el, step } of this.countdowns) {
      const left = step.confirm ? step.confirm.deadline - Date.now() : 0;
      if (left <= 0) expired = true;
      else el.textContent = `ещё ${Math.ceil(left / 1000)} с`;
    }
    if (expired) this.render();
  }

  private renderTime(): void {
    const run = this.run;
    if (!run) return;
    this.timeEl.textContent = seconds((run.endedAt ?? Date.now()) - run.startedAt);
  }

  private answer(step: WorkStep, value: boolean): void {
    const run = this.run;
    const state = step.confirm;
    // a late answer is dropped: the backend has already cancelled the step
    if (!run || !state || state.sent || step.status !== "running") return;
    if (Date.now() > state.deadline) return;
    state.sent = true;
    this.render();
    this.onAnswer?.(run, step, value);
  }

  private confirmRow(step: WorkStep): HTMLElement | null {
    const state = step.confirm;
    if (!state || step.status !== "running") return null;
    if (state.sent) {
      return h(
        "div",
        { class: "worklog-confirm" },
        h("span", { class: "muted" }, "Ответ отправлен…"),
      );
    }
    if (Date.now() > state.deadline) {
      return h("div", { class: "worklog-confirm" }, h("span", { class: "muted" }, "Время вышло"));
    }
    const left = h("span", { class: "worklog-countdown" });
    left.textContent = `ещё ${Math.max(0, Math.ceil((state.deadline - Date.now()) / 1000))} с`;
    this.countdowns.push({ el: left, step });
    return h(
      "div",
      { class: "worklog-confirm", role: "group", "aria-label": "Подтверждение" },
      h(
        "button",
        { type: "button", class: "btn small", onclick: () => this.answer(step, true) },
        "Да",
      ),
      h(
        "button",
        { type: "button", class: "btn small ghost", onclick: () => this.answer(step, false) },
        "Нет",
      ),
      left,
    );
  }

  private detailBlock(detail: string): HTMLElement {
    // multi-line or long text (shell output) goes to a clipped monospace block
    if (detail.includes("\n") || detail.length > 160) {
      return h("pre", { class: "worklog-output", tabindex: 0, "aria-label": "Вывод" }, detail);
    }
    return h("span", { class: "worklog-detail" }, detail);
  }

  private render(): void {
    const run = this.run;
    this.el.hidden = !run;
    if (!run) return;
    this.el.dataset.status = run.status;
    this.statusEl.textContent = RUN_LABEL[run.status];
    this.titleEl.textContent = run.title ? clip(run.title, 48) : "";
    this.titleEl.hidden = !run.title;
    this.renderTime();
    this.countdowns = [];
    // follow the newest step unless the user has scrolled up to read
    const list = this.list;
    const follow = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    list.replaceChildren(
      ...run.steps.map((s) =>
        h(
          "li",
          { class: `worklog-step ${s.status}`, "data-kind": s.kind },
          h("span", { class: "worklog-icon" }, svgIcon(STEP_ICON[s.kind])),
          h(
            "div",
            { class: "worklog-body" },
            h("span", { class: "worklog-step-title" }, s.title),
            s.detail ? this.detailBlock(s.detail) : null,
            this.confirmRow(s),
          ),
          h(
            "span",
            { class: "worklog-offset", title: "от начала запроса" },
            `+${seconds(s.startedAt - run.startedAt)}`,
          ),
        ),
      ),
    );
    if (!run.steps.length) {
      this.list.append(h("li", { class: "worklog-step empty" }, "Шаги появятся по ходу работы"));
    }
    if (follow) list.scrollTop = list.scrollHeight;
  }
}

/** Anything with store.on(type, fn): the live store, or a fake in tests. */
export interface EventSource {
  on(type: string, fn: (e: BusEvent) => void): () => void;
  /** Sends a message to the backend (confirm answers). */
  send?(message: Record<string, unknown>): void;
}

const at = (e: BusEvent): number => (typeof e.ts === "number" ? e.ts * 1000 : Date.now());

const KINDS = new Set<string>([
  "heard",
  "listen",
  "intent",
  "tool",
  "think",
  "speak",
  "reply",
  "note",
  "action",
  "confirm",
]);

const STEP_STATUS: Record<string, StepStatus> = {
  ok: "done",
  error: "error",
  cancelled: "cancelled",
  skipped: "skipped",
};

const RUN_STATUS: Record<string, Exclude<RunStatus, "running">> = {
  done: "done",
  error: "error",
  interrupted: "interrupted",
};

/**
 * Feeds the log from bus events. Returns an unsubscribe function.
 * Voice pipeline: speech_started, speech_recognized, intent_matched, tool_called, state_changed,
 * tts_started/tts_finished, assistant_reply, barge_in.
 * Runs: run_started, step_started, step_finished, confirm_requested, run_finished.
 */
export function bindWorkLog(log: WorkLog, source: EventSource): () => void {
  let state: State = "idle";
  let reply = "";
  /** Backend step id → log step id, and back (for the answers). */
  const steps = new Map<string, number>();
  const keys = new Map<number, string>();

  /** Close the run; a reply that was never spoken (text mode) becomes the last step. */
  const finish = (when: number, status: Exclude<RunStatus, "running"> = "done"): void => {
    if (!log.running) return;
    const spoken = log.current?.steps.some((s) => s.kind === "speak");
    if (reply && !spoken && status === "done")
      log.step({ kind: "reply", title: "Ответ готов", detail: clip(reply), at: when });
    reply = "";
    log.end(status, when);
    steps.clear();
    keys.clear();
  };

  /** A run with a backend id ends with run_finished, not with the state falling back to idle. */
  const hasBackendRun = (): boolean => Boolean(log.running && log.current?.runId);

  /** Make the log show the run `runId`; null if that run is already over. */
  const ensureRun = (runId: string, when: number): boolean => {
    const cur = log.current;
    if (log.running && cur?.runId === runId) return true;
    if (log.running && cur && !cur.runId) {
      log.adopt(runId);
      return true;
    }
    if (!log.running && cur?.runId === runId) return false;
    log.begin(when, runId);
    steps.clear();
    keys.clear();
    return true;
  };

  const startStep = (
    runId: string,
    stepId: string,
    kind: string,
    title: string,
    detail: string,
    when: number,
  ): number | null => {
    if (!ensureRun(runId, when)) return null;
    const id = log.step({
      kind: KINDS.has(kind) ? (kind as StepKind) : "note",
      title,
      detail: detail || undefined,
      status: "running",
      external: true,
      at: when,
    });
    steps.set(stepId, id);
    keys.set(id, stepId);
    return id;
  };

  log.onAnswer = (run, step, answer) => {
    const stepId = keys.get(step.id);
    if (run.runId && stepId) {
      source.send?.({ type: "confirm", run_id: run.runId, step_id: stepId, answer });
    }
  };

  const offs = [
    source.on("run_started", (e) => {
      const runId = String(e.run_id);
      const title = String(e.title ?? "");
      const cur = log.current;
      // a voice request has already shown «Слушаю»: the run continues that log
      if (log.running && cur && !cur.runId && Math.abs(at(e) - cur.startedAt) < 60_000) {
        log.adopt(runId, title);
      } else {
        log.begin(at(e), runId, title);
      }
      steps.clear();
      keys.clear();
      reply = "";
    }),
    source.on("step_started", (e) => {
      startStep(
        String(e.run_id),
        String(e.step_id),
        String(e.kind ?? ""),
        String(e.title ?? ""),
        String(e.detail ?? ""),
        at(e),
      );
    }),
    source.on("step_finished", (e) => {
      const id = steps.get(String(e.step_id));
      if (id === undefined) return;
      log.finishStep(id, STEP_STATUS[String(e.status)] ?? "done", String(e.detail ?? ""));
    }),
    source.on("confirm_requested", (e) => {
      const stepId = String(e.step_id);
      const question = String(e.question ?? "Выполнить?");
      const id =
        steps.get(stepId) ?? startStep(String(e.run_id), stepId, "confirm", question, "", at(e));
      if (id !== null) log.requestConfirm(id, question, Number(e.timeout) || 15);
    }),
    source.on("run_finished", (e) => {
      const cur = log.current;
      if (!cur || cur.runId !== String(e.run_id)) return;
      finish(at(e), RUN_STATUS[String(e.status)] ?? "done");
    }),
    source.on("speech_started", (e) => {
      if (!log.running) log.begin(at(e));
      log.step({ kind: "listen", title: "Слушаю", status: "running", at: at(e) });
    }),
    source.on("speech_recognized", (e) => {
      const cur = log.current;
      const inRun = Boolean(log.running && cur?.runId);
      // an answer to a question of a running command stays in that run; a voice request
      // continues the run its «Слушаю» step started; anything else is a new request
      const continues =
        inRun &&
        (cur!.steps.some((s) => s.status === "running") ||
          !cur!.steps.some((s) => s.kind === "heard"));
      if (!continues && (!log.running || log.openStep("listen") === null)) log.begin(at(e));
      reply = "";
      const voice = e.source === "voice";
      log.step({
        kind: "heard",
        title: voice ? "Распознан запрос" : "Получен запрос",
        detail: `«${clip(String(e.text ?? ""))}»`,
        at: at(e),
        external: inRun,
      });
    }),
    source.on("intent_matched", (e) =>
      log.step({
        kind: "intent",
        title: `Команда ${e.plugin} → ${e.command}`,
        // the router scores 0..100 (fuzzy match)
        detail: typeof e.score === "number" ? `совпадение ${Math.round(e.score)}%` : undefined,
        at: at(e),
        external: hasBackendRun(),
      }),
    ),
    source.on("tool_called", (e) => {
      // inside a backend run the brain reports every call as step_started/step_finished
      if (hasBackendRun()) return;
      const args = e.arguments && typeof e.arguments === "object" ? e.arguments : {};
      const shown = Object.keys(args).length ? clip(JSON.stringify(args), 160) : undefined;
      log.step({
        kind: "tool",
        title: `Инструмент ${e.plugin}.${e.tool}`,
        detail: shown,
        at: at(e),
      });
    }),
    source.on("state_changed", (e) => {
      const next = e.new as State;
      if (next === state) return;
      const previous = state;
      state = next;
      // speaking → thinking is the dialog wrapping up after the reply, not a new thought
      if (next === "thinking" && previous !== "speaking")
        log.step({ kind: "think", title: "Думаю", status: "running", at: at(e) });
      // back to idle: the request is complete (a backend run says so itself)
      if (next === "idle" && !hasBackendRun()) finish(at(e));
    }),
    source.on("tts_started", (e) =>
      log.step({
        kind: "speak",
        title: "Говорю",
        detail: clip(String(e.text ?? "")),
        status: "running",
        at: at(e),
        external: hasBackendRun(),
      }),
    ),
    source.on("tts_finished", (e) => {
      const id = log.openStep("speak");
      if (id !== null) log.finishStep(id, e.interrupted ? "cancelled" : "done");
    }),
    source.on("assistant_reply", (e) => {
      if (!log.running) return;
      reply = `${reply} ${String(e.text ?? "")}`.trim();
      if (e.final && state === "idle" && !hasBackendRun()) finish(at(e));
    }),
    source.on("barge_in", (e) => {
      if (!hasBackendRun()) log.end("interrupted", at(e));
    }),
  ];
  return () => {
    offs.forEach((off) => off());
    log.onAnswer = null;
  };
}
