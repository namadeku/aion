// Minimal DOM helpers (no framework).

type Child = Node | string | number | null | undefined | false;
type Props = Record<string, unknown> & { class?: string; style?: string };

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Props | null = null,
  ...children: (Child | Child[])[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value as EventListener);
    } else if (key === "class") {
      el.className = String(value);
    } else if (key in el && key !== "list" && key !== "style") {
      (el as unknown as Record<string, unknown>)[key] = value;
    } else {
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

export function svgIcon(path: string): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.innerHTML = path;
  return svg;
}

export const icons = {
  home: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/>',
  plugins: '<path d="M9 3v4M15 3v4M7 7h10v4a5 5 0 0 1-10 0z"/><path d="M12 16v5"/>',
  voice: '<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  micOff:
    '<path d="M3 3l18 18M9 9v2a3 3 0 0 0 5 2.2M15 9.3V6a3 3 0 0 0-5.7-1.3M5 11a7 7 0 0 0 11.4 5.4M19 11a7 7 0 0 1-.6 2.8M12 18v3"/>',
  brain:
    '<path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 3 5h2V4zM15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-3 5h-2V4z"/>',
  logs: '<path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  send: '<path d="M4 12l16-8-6 16-2-7z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  desktop:
    '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/><circle cx="16" cy="12" r="2.2"/>',
};

export function toast(title: string, text = "", level = "info"): void {
  let box = document.querySelector<HTMLDivElement>(".toasts");
  if (!box) {
    box = h("div", { class: "toasts" });
    document.body.append(box);
  }
  const item = h("div", { class: `toast ${level}` }, h("b", null, title), text);
  box.append(item);
  setTimeout(() => item.remove(), 5000);
}

export function field(label: string, control: HTMLElement, hint = "", wide = false): HTMLElement {
  return h(
    "label",
    { class: `field${wide ? " wide" : ""}` },
    h("span", null, label),
    control,
    hint ? h("small", null, hint) : null,
  );
}

export function select(
  options: (string | [string, string])[],
  value: string,
  onChange: (v: string) => void,
): HTMLSelectElement {
  const el = h("select", { onchange: () => onChange(el.value) });
  for (const opt of options) {
    const [v, label] = Array.isArray(opt) ? opt : [opt, opt];
    el.append(h("option", { value: v, selected: v === value }, label));
  }
  if (!options.some((o) => (Array.isArray(o) ? o[0] : o) === value) && value) {
    el.append(h("option", { value, selected: true }, value));
  }
  return el;
}

export function range(
  value: number,
  min: number,
  max: number,
  step: number,
  onChange: (v: number) => void,
  format: (v: number) => string = (v) => v.toFixed(2),
): HTMLElement {
  const out = h("output", null, format(value));
  const input = h("input", {
    type: "range",
    min,
    max,
    step,
    value,
    oninput: () => (out.textContent = format(Number(input.value))),
    onchange: () => onChange(Number(input.value)),
  });
  return h("div", { class: "range-row" }, input, out);
}

export function toggle(checked: boolean, onChange: (v: boolean) => void): HTMLElement {
  const input = h("input", { type: "checkbox", checked, onchange: () => onChange(input.checked) });
  return h("label", { class: "switch" }, input, h("span"));
}

export function text(
  value: string,
  onChange: (v: string) => void,
  props: Record<string, unknown> = {},
): HTMLInputElement {
  const el = h("input", { type: "text", value, ...props, onchange: () => onChange(el.value) });
  return el;
}
