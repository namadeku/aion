// Bridge to the frameless desktop window (pywebview js_api, see src/aion/ui/desktop.py).
// In a normal browser (`aion run --browser`) there is no bridge and the window buttons hide.

export type ResizeEdge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
export type WindowState = "normal" | "maximized" | "minimized";

interface NativeApi {
  minimize(): Promise<void>;
  toggle_maximize(): Promise<boolean>;
  close(): Promise<void>;
  start_drag(): Promise<void>;
  start_resize(edge: ResizeEdge): Promise<void>;
}

interface PyWebView {
  api?: Partial<NativeApi>;
}

type Listener = () => void;

class NativeWindow {
  state: WindowState = "normal";
  private listeners = new Set<Listener>();

  constructor() {
    window.addEventListener("pywebviewready", () => this.notify());
    // pywebview may inject its bridge before this module runs (the ready event is then
    // missed): look for it for a few seconds after start
    let tries = 0;
    const poll = window.setInterval(() => {
      if (this.available) this.notify();
      if (this.available || ++tries > 40) window.clearInterval(poll);
    }, 250);
    // pushed by desktop.py on minimize / maximize / restore
    window.addEventListener("aion-window", (e) => {
      const detail = (e as CustomEvent<WindowState>).detail;
      if (detail === "normal" || detail === "maximized" || detail === "minimized") {
        this.state = detail;
        document.documentElement.dataset.window = detail;
        this.notify();
      }
    });
  }

  private get api(): Partial<NativeApi> | null {
    const py = (window as unknown as { pywebview?: PyWebView }).pywebview;
    return py?.api ?? null;
  }

  /** The page runs inside the Aion desktop window with custom window buttons. */
  get available(): boolean {
    return typeof this.api?.close === "function";
  }

  /** Nothing to draw: the window is minimized or the tab is in the background. */
  get hidden(): boolean {
    return document.hidden || this.state === "minimized";
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private notify(): void {
    for (const fn of this.listeners) fn();
  }

  minimize(): void {
    void this.api?.minimize?.();
  }

  toggleMaximize(): void {
    void this.api?.toggle_maximize?.();
  }

  close(): void {
    void this.api?.close?.();
  }

  startDrag(): void {
    void this.api?.start_drag?.();
  }

  startResize(edge: ResizeEdge): void {
    void this.api?.start_resize?.(edge);
  }
}

export const native = new NativeWindow();
