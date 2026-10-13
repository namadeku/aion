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
  home: '<path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/>',
  console:
    '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="M7 10l3 2.5L7 15M12.5 15H17"/>',
  plugins:
    '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/><path d="M16.75 4v6.5M13.5 7.25H20"/>',
  settings:
    '<path d="M4 7h9M19 7h1M4 17h3M13 17h7"/><circle cx="16" cy="7" r="2.5"/><circle cx="10" cy="17" r="2.5"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4.5V8h3.5"/><path d="M12 8v4.5l3 2"/>',
  user: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/>',
  minimize: '<path d="M6 12h12"/>',
  maximize: '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
  close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  chevronDown: '<path d="M7 10l5 5 5-5"/>',
  code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
  tool: '<path d="M14.5 6.5a4 4 0 0 0-5.3 5.3L4 17l3 3 5.2-5.2a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.5-.5-.5-2.5z"/>',
  voice: '<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  micOff:
    '<path d="M3 3l18 18M9 9v2a3 3 0 0 0 5 2.2M15 9.3V6a3 3 0 0 0-5.7-1.3M5 11a7 7 0 0 0 11.4 5.4M19 11a7 7 0 0 1-.6 2.8M12 18v3"/>',
  brain:
    '<path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 3 5h2V4zM15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-3 5h-2V4z"/>',
  logs: '<path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
  bolt: '<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/>',
  question:
    '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 16.8v.2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M5 7h14M10 7V4.5h4V7M7 7l.8 12.5h8.4L17 7M10 11v5M14 11v5"/>',
  up: '<path d="M6 14l6-6 6 6"/>',
  down: '<path d="M6 10l6 6 6-6"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5M5 20h14"/>',
  record:
    '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none"/>',
  play: '<path d="M8 5.5v13l11-6.5z"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  folder:
    '<path d="M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4l2 2.5h8A1.5 1.5 0 0 1 20.5 9.5v8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 17.5z"/>',
  warning: '<path d="M12 4l9 15.5H3zM12 10v4.5M12 17v.3"/>',
  edit: '<path d="M4 20l1-4L16.5 4.5a2 2 0 0 1 3 3L8 19zM14.5 6.5l3 3"/>',
  close2: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  cloud: '<path d="M7 18a4 4 0 0 1-.5-8 5.5 5.5 0 0 1 10.6 1.3A3.4 3.4 0 0 1 17 18z"/>',
  timer: '<circle cx="12" cy="13" r="7.5"/><path d="M12 9v4l2.5 1.5M9.5 3h5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  calc: '<rect x="5" y="3.5" width="14" height="17" rx="2.5"/><path d="M8.5 8h7M9 12.5h.01M12 12.5h.01M15 12.5h.01M9 16h.01M12 16h.01M15 16h.01"/>',
  note: '<path d="M6 3.5h9l4 4V20.5H6z"/><path d="M14.5 3.5V8H19M9 12.5h7M9 16h5"/>',
  clipboard:
    '<rect x="6" y="5" width="12" height="15.5" rx="2"/><path d="M9.5 5V4h5v1M9.5 11h5M9.5 14.5h5"/>',
  gamepad:
    '<rect x="3" y="7.5" width="18" height="10" rx="5"/><path d="M8 10.5v4M6 12.5h4M15.5 11.5h.01M17.5 13.5h.01"/>',
  plane: '<path d="M20.5 4L3.5 11l6 2.5L12 20l2.5-5.5zM9.5 13.5L20.5 4"/>',
  power: '<path d="M12 3v8M7 6.5a7.5 7.5 0 1 0 10 0"/>',
  heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  database:
    '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5V8H16"/>',
  shield: '<path d="M12 3.5l7 2.5v5.5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/>',
  pause: '<path d="M8 5.5v13M16 5.5v13"/>',
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

/** Square icon-only button with an accessible name and a tooltip. */
export function iconButton(
  label: string,
  icon: string,
  onClick: (e: MouseEvent) => void,
  className = "icon-btn",
): HTMLButtonElement {
  return h(
    "button",
    { type: "button", class: className, "aria-label": label, title: label, onclick: onClick },
    svgIcon(icon),
  );
}
