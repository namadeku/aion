import { pageShell } from "./common";
import { mcpPanel } from "./mcp";

/** Settings → MCP: external tool servers (Model Context Protocol). */
export function mcpPage(root: HTMLElement): void {
  const body = pageShell(
    root,
    "MCP",
    "Внешние серверы инструментов: их функции становятся доступны языковой модели.",
  );
  body.append(mcpPanel());
}
