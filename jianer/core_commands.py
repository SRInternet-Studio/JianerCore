"""Core plugin-management commands provided by JianerCore itself."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from . import configurator
from .unimessage import Target, UniMessage

DEFAULT_CONFUSED_WORD = (
    "不能这么做！那是一块丞待开发的禁地，可能很危险，"
    "{bot_name}很胆小……꒰>﹏< ꒱"
)


@dataclass
class CoreCommandOptions:
    """Host-provided policy and display settings for core commands."""

    reminder: str = "~"
    bot_name: str = "Jianer"
    bot_name_en: str = "Jianer"
    slogan: str = ""
    confused_word: str = DEFAULT_CONFUSED_WORD
    is_admin: Callable[[Any], bool] | None = None
    before_reload: Callable[[], Any] | None = None
    plugin_view_formatter: Callable[[Any], str] | None = None


def options_from_config() -> CoreCommandOptions:
    config = configurator.BotConfig.get("jianer-bot")
    others = getattr(config, "others", {}) or {}
    return CoreCommandOptions(
        reminder=str(others.get("reminder", "~")),
        bot_name=str(others.get("bot_name", "Jianer")),
        bot_name_en=str(others.get("bot_name_en", "Jianer")),
        slogan=str(others.get("slogan", "")),
        confused_word=str(others.get("confused_words", DEFAULT_CONFUSED_WORD)),
    )


class CoreCommandRouter:
    """Dispatch the four plugin-management commands owned by JianerCore."""

    def __init__(self, client: Any):
        self.client = client
        self.options = options_from_config()

    def configure(
        self,
        *,
        is_admin: Callable[[Any], bool] | None = None,
        before_reload: Callable[[], Any] | None = None,
        plugin_view_formatter: Callable[[Any], str] | None = None,
    ) -> None:
        if is_admin is not None:
            self.options.is_admin = is_admin
        if before_reload is not None:
            self.options.before_reload = before_reload
        if plugin_view_formatter is not None:
            self.options.plugin_view_formatter = plugin_view_formatter

    async def dispatch(
        self,
        event: Any,
        actions: Any,
        *,
        message_text: str | None = None,
    ) -> bool:
        if getattr(event, "group_id", None) is None:
            return False

        text = str(
            message_text
            if message_text is not None
            else getattr(event, "msg_str", getattr(event, "message", ""))
        ).strip()
        reminder = self.options.reminder

        if text == f"{reminder}重载插件":
            await self._reload(event, actions)
            return True
        if text.startswith(f"{reminder}禁用插件 "):
            await self._toggle(event, actions, text, enabled=False)
            return True
        if text.startswith(f"{reminder}启用插件 "):
            await self._toggle(event, actions, text, enabled=True)
            return True
        if text == f"{reminder}插件视角":
            await self._send(event, actions, self.plugin_view_text())
            return True
        return False

    async def _reload(self, event: Any, actions: Any) -> None:
        if not await self._require_admin(event, actions):
            return
        try:
            await self._run_hook(self.options.before_reload)
            result = await self.client.reload_plugins()
        except Exception as exc:
            await self._send(
                event,
                actions,
                self._banner(f"重载失败：{type(exc).__name__}: {exc}"),
            )
            return
        if result.failed:
            await self._send(
                event,
                actions,
                self._banner("重载失败：" + "; ".join(result.failed)),
            )
            return
        await self._send(
            event,
            actions,
            self._banner(
                f"外部后端已重载完成。发送 {self.options.reminder}插件视角 以查看更多信息。"
            ),
        )

    async def _toggle(
        self,
        event: Any,
        actions: Any,
        text: str,
        *,
        enabled: bool,
    ) -> None:
        if not await self._require_admin(event, actions):
            return
        word = "启用插件" if enabled else "禁用插件"
        prefix = f"{self.options.reminder}{word} "
        plugin_name = text[len(prefix):].strip()
        if not plugin_name:
            await self._send(
                event,
                actions,
                "管理员：你的格式有误。\n"
                f"格式：{self.options.reminder}{word} (plugin_name)\n"
                f"参考：{self.options.reminder}{word} Hello World",
            )
            return
        try:
            await self._run_hook(self.options.before_reload)
            result = await self.client.set_plugin_enabled(
                plugin_name,
                enabled=enabled,
            )
        except Exception as exc:
            await self._send(
                event,
                actions,
                self._banner(f"失败: {word} {plugin_name} 时发生错误。\n错误信息：{exc}"),
            )
            return
        if result.failed:
            await self._send(
                event,
                actions,
                self._banner("失败: " + "; ".join(result.failed)),
            )
            return
        changed = bool(getattr(result, "changed", False))
        verb = "启用" if enabled else "禁用"
        if changed:
            message = f"插件 {plugin_name} 已经成功{verb}"
        else:
            message = f"插件 {plugin_name} 当前已处于{verb}状态，无需{verb}"
        await self._send(event, actions, self._banner(message))

    async def _require_admin(self, event: Any, actions: Any) -> bool:
        resolver = self.options.is_admin
        if resolver is None:
            config = configurator.BotConfig.get("jianer-bot")
            owners = {str(item) for item in (getattr(config, "owner", []) or [])}
            allowed = str(getattr(event, "user_id", "")) in owners
        else:
            allowed = bool(await self._run_hook(resolver, event))
        if not allowed:
            await self._send(
                event,
                actions,
                self.options.confused_word.format(bot_name=self.options.bot_name),
            )
        return allowed

    def plugin_view_text(self) -> str:
        formatter = self.options.plugin_view_formatter
        if formatter is not None:
            return str(formatter(self.client.last_load_result))

        result = self.client.last_load_result
        loaded = list(getattr(result, "loaded", ()) or ())
        failed = list(getattr(result, "failed", ()) or ())
        warnings = list(getattr(result, "warnings", ()) or ())
        disabled = list(self.client.disabled_plugins())
        return f"""{self.options.bot_name} {self.options.bot_name_en} - 插件视角
————————————————————
✅ 已加载插件 ({len(loaded)}):
{self._numbered(loaded)}

❌ 已禁用插件 ({len(disabled)}):
{self._numbered(disabled)}

⚠️ 加载失败 ({len(failed)}):
{self._numbered(failed)}

⚠️ 加载警告 ({len(warnings)}):
{self._numbered(warnings)}"""

    @staticmethod
    def _numbered(items: list[Any]) -> str:
        if not items:
            return "无"
        return "\n".join(f"{index + 1}. {item}" for index, item in enumerate(items))

    def _banner(self, message: str) -> str:
        name = f"{self.options.bot_name} {self.options.bot_name_en}"
        if self.options.slogan:
            name = f"{name} - {self.options.slogan}"
        return f"{name}\n————————————————————\n{message}"

    async def _send(self, event: Any, actions: Any, text: str) -> None:
        await UniMessage.text(text).send(
            target=Target.group(event.group_id),
            actions=actions,
        )

    @staticmethod
    async def _run_hook(hook: Callable[..., Any] | None, *args: Any) -> Any:
        if hook is None:
            return None
        response = hook(*args)
        if inspect.isawaitable(response):
            return await response
        return response


__all__ = [
    "CoreCommandOptions",
    "CoreCommandRouter",
    "DEFAULT_CONFUSED_WORD",
    "options_from_config",
]
