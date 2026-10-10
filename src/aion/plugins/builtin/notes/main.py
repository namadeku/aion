"""Notes and a to-do list stored in the assistant database."""

from __future__ import annotations

from typing import Annotated, Any

from rapidfuzz import fuzz, process

from aion.sdk import Context, Plugin, command, fmt_count, tool

NOTE_FORMS = ("заметка", "заметки", "заметок")
TODO_FORMS = ("дело", "дела", "дел")


class Notes(Plugin):
    async def _add(self, kind: str, text: str) -> None:
        await self.host.db.execute(
            "INSERT INTO notes (kind, text) VALUES (?, ?)", (kind, text.strip())
        )

    async def _list(self, kind: str, *, done: bool = False) -> list[dict[str, Any]]:
        return await self.host.db.fetchall(
            "SELECT id, text FROM notes WHERE kind = ? AND done = ? ORDER BY id", (kind, int(done))
        )

    async def _find_todo(self, query: str) -> dict[str, Any] | None:
        todos = await self._list("todo")
        hit = process.extractOne(
            query, [t["text"] for t in todos], scorer=fuzz.WRatio, score_cutoff=70
        )
        return todos[hit[2]] if hit else None

    # -- notes ----------------------------------------------------------------------------

    @command(["(запиши|сохрани|создай) заметку {text}", "заметка {text}", "запиши что {text}"])
    async def add_note_cmd(self, ctx: Context, text: str) -> None:
        await self._add("note", text)
        await ctx.say(ctx.g("Записал.", "Записала."))

    @command(["(прочитай|покажи|какие у меня) заметки", "что я записывал"])
    async def read_notes(self, ctx: Context) -> None:
        notes = await self._list("note")
        if not notes:
            await ctx.say("Заметок нет.")
            return
        last = notes[-5:]
        intro = f"У вас {fmt_count(len(notes), NOTE_FORMS)}." + (
            " Последние:" if len(notes) > 5 else ""
        )
        await ctx.say(intro + " " + " ".join(f"{i}. {n['text']}." for i, n in enumerate(last, 1)))

    @command(["(удали|очисти) [все] заметки"], dangerous=True, description="удалить все заметки")
    async def clear_notes(self, ctx: Context) -> None:
        await self.host.db.execute("DELETE FROM notes WHERE kind = 'note'")
        await ctx.say("Заметки удалены.")

    # -- to-do ----------------------------------------------------------------------------

    @command(
        [
            "добавь в (список дел|дела|задачи) {text}",
            "(новая задача|новое дело) {text}",
        ]
    )
    async def add_todo_cmd(self, ctx: Context, text: str) -> None:
        await self._add("todo", text)
        await ctx.say(ctx.g("Добавил в список дел.", "Добавила в список дел."))

    @command(
        [
            "что у меня в (списке дел|делах|задачах)",
            "(мой|покажи|прочитай) список дел",
            "какие у меня (дела|задачи)",
        ]
    )
    async def read_todos(self, ctx: Context) -> None:
        todos = await self._list("todo")
        if not todos:
            await ctx.say("Список дел пуст.")
            return
        items = "; ".join(t["text"] for t in todos[:7])
        await ctx.say(f"{fmt_count(len(todos), TODO_FORMS).capitalize()}: {items}.")

    @command(["(вычеркни|отметь|выполнено) {text}", "я (сделал|сделала|выполнил|выполнила) {text}"])
    async def complete_todo_cmd(self, ctx: Context, text: str) -> bool:
        todo = await self._find_todo(text.removesuffix(" выполненным").removesuffix(" выполненной"))
        if todo is None:
            return False  # not a to-do item — let other commands or the LLM handle it
        await self.host.db.execute("UPDATE notes SET done = 1 WHERE id = ?", (todo["id"],))
        await ctx.say(f"{ctx.g('Вычеркнул', 'Вычеркнула')}: {todo['text']}.", emotion="joy")
        return True

    @command(
        ["очисти список дел", "удали все (дела|задачи)"],
        dangerous=True,
        description="очистить список дел",
    )
    async def clear_todos(self, ctx: Context) -> None:
        await self.host.db.execute("DELETE FROM notes WHERE kind = 'todo'")
        await ctx.say("Список дел очищен.")

    # -- LLM tools ------------------------------------------------------------------------

    @tool("Сохранить заметку пользователя")
    async def add_note(self, text: str) -> str:
        await self._add("note", text)
        return "Заметка сохранена"

    @tool("Прочитать заметки пользователя")
    async def list_notes(self) -> str:
        return "\n".join(f"- {n['text']}" for n in await self._list("note")) or "Заметок нет"

    @tool("Добавить дело в список дел")
    async def add_todo(self, text: Annotated[str, "Что нужно сделать"]) -> str:
        await self._add("todo", text)
        return "Добавлено"

    @tool("Невыполненные дела из списка")
    async def list_todos(self) -> str:
        return "\n".join(f"- {t['text']}" for t in await self._list("todo")) or "Список пуст"

    @tool("Отметить дело выполненным (по примерному названию)")
    async def complete_todo(self, text: str) -> str:
        todo = await self._find_todo(text)
        if todo is None:
            return "Такого дела нет"
        await self.host.db.execute("UPDATE notes SET done = 1 WHERE id = ?", (todo["id"],))
        return f"Выполнено: {todo['text']}"
