// Every page of the main window. Add a page here: pick a section (rail icon), a path and a
// title; sections with several pages get tabs automatically.

import { registerPage } from "../routes";
import { aboutPage } from "./about";
import { audioPage } from "./audio";
import { commandPacksPage } from "./command-packs";
import { commandsPage } from "./commands";
import { consolePage } from "./console";
import { historyPage } from "./history";
import { homePage } from "./home";
import { llmPage } from "./llm";
import { logsPage } from "./logs";
import { mcpPage } from "./mcp-page";
import { pluginsPage } from "./plugins";
import { voicePage } from "./voice";

registerPage({ path: "", section: "home", title: "Главная", page: homePage, fullBleed: true });

registerPage({ path: "commands", section: "commands", title: "Команды", page: commandsPage });
registerPage({
  path: "commands/packs",
  section: "commands",
  title: "Паки и режим",
  page: commandPacksPage,
});

registerPage({ path: "console", section: "console", title: "События", page: consolePage });
registerPage({
  path: "console/logs",
  section: "console",
  title: "Логи",
  page: logsPage,
  devOnly: true,
  aliases: ["logs"],
});

registerPage({ path: "plugins", section: "plugins", title: "Плагины", page: pluginsPage });

registerPage({
  path: "settings/voice",
  section: "settings",
  title: "Голос и персонаж",
  page: voicePage,
  aliases: ["voice"],
});
registerPage({
  path: "settings/audio",
  section: "settings",
  title: "Микрофон",
  page: audioPage,
  aliases: ["audio"],
});
registerPage({
  path: "settings/llm",
  section: "settings",
  title: "LLM",
  page: llmPage,
  aliases: ["llm"],
});
registerPage({
  path: "settings/mcp",
  section: "settings",
  title: "MCP",
  page: mcpPage,
  aliases: ["mcp"],
});
registerPage({
  path: "settings/about",
  section: "settings",
  title: "О программе",
  page: aboutPage,
  aliases: ["about"],
});

registerPage({ path: "history", section: "history", title: "История", page: historyPage });
