"""Public plugin SDK.

Example::

    from aion.sdk import Context, Plugin, command, tool


    class Weather(Plugin):
        @command(["какая погода", "погода в {city}"])
        async def weather(self, ctx: Context, city: str = "Москва") -> None:
            await ctx.say(f"В городе {city} сейчас ...")

        @tool("Получить погоду в городе")
        async def get_weather(self, city: str) -> str: ...
"""

from aion.core.initiative import Observation
from aion.sdk.decorators import command, tool
from aion.sdk.plugin import Context, LLMAccess, Plugin
from aion.sdk.webhook import serve_json
from aion.text import normalize
from aion.text.plural import fmt_count, plural

__all__ = [
    "Context",
    "LLMAccess",
    "Observation",
    "Plugin",
    "command",
    "fmt_count",
    "normalize",
    "plural",
    "serve_json",
    "tool",
]
