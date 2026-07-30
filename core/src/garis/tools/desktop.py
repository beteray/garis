"""Screen, mouse, keyboard, clipboard, windows.

The last-resort path for software with no API: GARIS looks at the screen and uses
it the way a person would. Powerful and easy to get wrong, so every tool here
declares INPUT_CONTROL or CAPTURE, takes an exclusive lease on the physical
device, and is unavailable rather than silently broken when the optional Windows
dependencies are missing.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from typing import Any

from ..errors import ExecutionError, Unsupported
from ..runtime import Effect, ParamSpec, ToolContext, ToolRegistry, device_key

_INPUT_LEASE = [device_key("mouse-keyboard")]
_SCREEN_LEASE = [device_key("screen")]


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        "screen_capture",
        "Robi zrzut ekranu i zapisuje go w katalogu roboczym zadania.",
        params={
            "region": ParamSpec("list", "Obszar [x, y, szerokość, wysokość]", default=[]),
            "return_image": ParamSpec("bool", "Zwróć obraz w base64", default=False),
        },
        effects=[Effect.CAPTURE, Effect.READ],
        category="desktop",
        resources=lambda p: _SCREEN_LEASE,
    )
    async def screen_capture(
        ctx: ToolContext, region: list[int] | None = None, return_image: bool = False
    ) -> dict[str, Any]:
        grab = _screenshot_backend()
        box = tuple(int(v) for v in region) if region and len(region) == 4 else None
        path = ctx.task_workspace() / f"screen-{int(asyncio.get_running_loop().time())}.png"

        def capture() -> tuple[int, int]:
            image = grab(box)
            image.save(path, "PNG")
            return image.size

        width, height = await asyncio.to_thread(capture)
        out: dict[str, Any] = {"path": str(path), "width": width, "height": height}
        if return_image:
            out["image_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
        return out

    @registry.tool(
        "mouse_click",
        "Klika myszą we wskazanym punkcie ekranu.",
        params={
            "x": ParamSpec("int", "Współrzędna X", required=True),
            "y": ParamSpec("int", "Współrzędna Y", required=True),
            "button": ParamSpec("string", "Przycisk", default="left",
                                choices=("left", "right", "middle")),
            "clicks": ParamSpec("int", "Liczba kliknięć", default=1),
        },
        effects=[Effect.INPUT_CONTROL],
        category="desktop",
        resources=lambda p: _INPUT_LEASE,
    )
    async def mouse_click(
        ctx: ToolContext, x: int, y: int, button: str = "left", clicks: int = 1
    ) -> dict[str, Any]:
        gui = _gui()
        await asyncio.to_thread(gui.click, x=x, y=y, clicks=clicks, button=button)
        return {"x": x, "y": y, "button": button, "clicks": clicks}

    @registry.tool(
        "type_text",
        "Wpisuje tekst z klawiatury do aktywnego okna.",
        params={
            "text": ParamSpec("string", "Tekst do wpisania", required=True),
            "interval": ParamSpec("float", "Odstęp między znakami w sekundach", default=0.01),
        },
        effects=[Effect.INPUT_CONTROL],
        category="desktop",
        resources=lambda p: _INPUT_LEASE,
    )
    async def type_text(
        ctx: ToolContext, text: str, interval: float = 0.01
    ) -> dict[str, Any]:
        gui = _gui()
        await asyncio.to_thread(gui.write, text, interval=interval)
        return {"characters": len(text)}

    @registry.tool(
        "key_press",
        "Wysyła skrót klawiszowy, np. ctrl+s albo alt+tab.",
        params={"keys": ParamSpec("string", "Klawisze rozdzielone plusem", required=True)},
        effects=[Effect.INPUT_CONTROL],
        category="desktop",
        resources=lambda p: _INPUT_LEASE,
    )
    async def key_press(ctx: ToolContext, keys: str) -> dict[str, Any]:
        gui = _gui()
        parts = [k.strip().lower() for k in keys.replace(" ", "").split("+") if k.strip()]
        if not parts:
            raise ExecutionError("Puste kombinacja klawiszy", retryable=False)
        await asyncio.to_thread(gui.hotkey, *parts)
        return {"keys": parts}

    @registry.tool(
        "clipboard_read",
        "Czyta zawartość schowka.",
        params={},
        effects=[Effect.READ],
        category="desktop",
        resources=lambda p: [device_key("clipboard")],
    )
    async def clipboard_read(ctx: ToolContext) -> dict[str, Any]:
        text = await asyncio.to_thread(_clipboard_get)
        return {"text": text, "length": len(text)}

    @registry.tool(
        "clipboard_write",
        "Wstawia tekst do schowka.",
        params={"text": ParamSpec("string", "Tekst", required=True)},
        effects=[Effect.WRITE],
        category="desktop",
        resources=lambda p: [device_key("clipboard")],
    )
    async def clipboard_write(ctx: ToolContext, text: str) -> dict[str, Any]:
        await asyncio.to_thread(_clipboard_set, text)
        return {"length": len(text)}

    @registry.tool(
        "window_list",
        "Wypisuje otwarte okna z tytułami.",
        params={"title": ParamSpec("string", "Filtr tytułu", default="")},
        effects=[Effect.READ],
        platforms=("win32",),
        category="desktop",
    )
    async def window_list(ctx: ToolContext, title: str = "") -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(_windows)
        needle = title.lower()
        return [w for w in rows if not needle or needle in w["title"].lower()]

    @registry.tool(
        "window_focus",
        "Przenosi okno na pierwszy plan.",
        params={"title": ParamSpec("string", "Fragment tytułu okna", required=True)},
        effects=[Effect.INPUT_CONTROL],
        platforms=("win32",),
        category="desktop",
        resources=lambda p: _INPUT_LEASE,
    )
    async def window_focus(ctx: ToolContext, title: str) -> dict[str, Any]:
        rows = await asyncio.to_thread(_windows)
        needle = title.lower()
        match = next((w for w in rows if needle in w["title"].lower()), None)
        if match is None:
            raise ExecutionError(f"Nie znalazłem okna z tytułem zawierającym {title!r}",
                                 retryable=False)

        def focus() -> None:
            import ctypes

            ctypes.windll.user32.SetForegroundWindow(match["handle"])  # type: ignore[attr-defined]

        await asyncio.to_thread(focus)
        return {"title": match["title"], "handle": match["handle"]}


# --------------------------------------------------------------------- backends


def _gui() -> Any:
    """pyautogui, or a clear explanation of what is missing."""
    try:
        import pyautogui  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 - import fails without a display too
        raise Unsupported(
            "Sterowanie myszą i klawiaturą wymaga pakietu pyautogui i aktywnej sesji graficznej"
        ) from exc
    pyautogui.FAILSAFE = False  # a background agent must not abort on cursor position
    return pyautogui


def _screenshot_backend():  # type: ignore[no-untyped-def]
    try:
        from PIL import ImageGrab  # type: ignore[import-not-found]
    except ImportError as exc:
        raise Unsupported("Zrzut ekranu wymaga pakietu pillow") from exc

    def grab(box: tuple[int, ...] | None):  # type: ignore[no-untyped-def]
        if box is None:
            return ImageGrab.grab()
        x, y, width, height = box
        return ImageGrab.grab(bbox=(x, y, x + width, y + height))

    return grab


def _clipboard_get() -> str:
    if sys.platform == "win32":
        try:
            import win32clipboard  # type: ignore[import-not-found]
        except ImportError as exc:
            raise Unsupported("Schowek wymaga pakietu pywin32") from exc
        win32clipboard.OpenClipboard()
        try:
            import win32con  # type: ignore[import-not-found]

            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT))
            return ""
        finally:
            win32clipboard.CloseClipboard()
    raise Unsupported("Schowek jest na razie obsługiwany tylko na Windows")


def _clipboard_set(text: str) -> None:
    if sys.platform == "win32":
        try:
            import win32clipboard  # type: ignore[import-not-found]
            import win32con  # type: ignore[import-not-found]
        except ImportError as exc:
            raise Unsupported("Schowek wymaga pakietu pywin32") from exc
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return
    raise Unsupported("Schowek jest na razie obsługiwany tylko na Windows")


def _windows() -> list[dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    results: list[dict[str, Any]] = []

    callback_type = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def collect(handle: int, _: int) -> bool:
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        results.append({"handle": int(handle), "title": buffer.value})
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return results


__all__ = ["register"]
