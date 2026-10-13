// Aion's own mark: an open ring of time (Aion holds the wheel of the ages) with a small
// orbiting body at the break. Drawn in currentColor so it follows the theme's accent.

const SVG_NS = "http://www.w3.org/2000/svg";

export const LOGO_PATHS =
  // the ring, open at the upper right
  '<path d="M24.66 12.41A10 10 0 1 1 16.74 5.15" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>' +
  // the orbiting body sitting in the gap
  '<circle cx="21.76" cy="7.63" r="2.1" fill="currentColor"/>' +
  // the still centre
  '<circle cx="15" cy="15" r="3.4" fill="currentColor" opacity="0.55"/>';

export function logoMark(size = 30): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 30 30");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = LOGO_PATHS;
  return svg;
}
