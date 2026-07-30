"""Internet: HTTP calls, page reading, search."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Any

from ..errors import ExecutionError, Unsupported
from ..runtime import Effect, ParamSpec, ToolContext, ToolRegistry

MAX_PAGE_CHARS = 40_000

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        "web_fetch",
        "Pobiera stronę i zwraca jej treść jako tekst.",
        params={
            "url": ParamSpec("string", "Adres strony", required=True),
            "max_chars": ParamSpec("int", "Limit znaków", default=MAX_PAGE_CHARS),
        },
        effects=[Effect.NETWORK, Effect.READ],
        category="web",
        timeout=90.0,
    )
    async def web_fetch(
        ctx: ToolContext, url: str, max_chars: int = MAX_PAGE_CHARS
    ) -> dict[str, Any]:
        resp = await ctx.http.get(_require_http(url), timeout=60.0)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return {"url": url, "type": "json", "data": resp.json()}
        text = html_to_text(resp.text)
        truncated = len(text) > max_chars
        return {
            "url": url,
            "type": "text",
            "title": extract_title(resp.text),
            "text": text[:max_chars],
            "truncated": truncated,
        }

    @registry.tool(
        "http_request",
        "Wykonuje dowolne zapytanie HTTP do API.",
        params={
            "url": ParamSpec("string", "Adres", required=True),
            "method": ParamSpec("string", "Metoda", default="GET",
                                choices=("GET", "POST", "PUT", "PATCH", "DELETE")),
            "headers": ParamSpec("dict", "Nagłówki", default={}),
            "json_body": ParamSpec("dict", "Ciało zapytania jako JSON", default=None),
            "timeout": ParamSpec("int", "Limit czasu", default=60),
        },
        effects=[Effect.NETWORK],
        category="web",
        timeout=180.0,
        examples=(
            "http_request(url='https://api.example.com/v1/status',"
            " headers={'Authorization': 'Bearer vault://example_token'})",
        ),
    )
    async def http_request(
        ctx: ToolContext,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        resp = await ctx.http.request(
            method,
            _require_http(url),
            headers=headers or {},
            json_body=json_body,
            timeout=float(timeout),
        )
        body: Any
        if "json" in resp.headers.get("content-type", ""):
            try:
                body = resp.json()
            except ExecutionError:
                body = resp.text[:MAX_PAGE_CHARS]
        else:
            body = resp.text[:MAX_PAGE_CHARS]
        return {
            "status": resp.status,
            "ok": resp.ok,
            "headers": dict(resp.headers),
            "body": body,
        }

    @registry.tool(
        "web_search",
        "Szuka w internecie i zwraca listę wyników z opisami.",
        params={
            "query": ParamSpec("string", "Czego szukać", required=True),
            "limit": ParamSpec("int", "Liczba wyników", default=8),
        },
        effects=[Effect.NETWORK, Effect.READ],
        category="web",
        timeout=90.0,
    )
    async def web_search(
        ctx: ToolContext, query: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Search with whichever backend is configured.

        Brave and Tavily give clean, rate-limit-friendly results when the user has a
        key; the DuckDuckGo HTML endpoint is the no-configuration fallback so search
        works out of the box.
        """
        if ctx.has_secret("brave_api_key"):
            return await _brave(ctx, query, limit)
        if ctx.has_secret("tavily_api_key"):
            return await _tavily(ctx, query, limit)
        return await _duckduckgo(ctx, query, limit)


async def _brave(ctx: ToolContext, query: str, limit: int) -> list[dict[str, Any]]:
    resp = await ctx.http.get(
        "https://api.search.brave.com/res/v1/web/search?"
        + urllib.parse.urlencode({"q": query, "count": limit}),
        headers={
            "X-Subscription-Token": ctx.secret("brave_api_key"),
            "Accept": "application/json",
        },
        timeout=45.0,
    )
    data = resp.raise_for_status().json()
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": html_to_text(item.get("description", "")),
        }
        for item in (data.get("web") or {}).get("results", [])[:limit]
    ]


async def _tavily(ctx: ToolContext, query: str, limit: int) -> list[dict[str, Any]]:
    resp = await ctx.http.post(
        "https://api.tavily.com/search",
        json_body={
            "api_key": ctx.secret("tavily_api_key"),
            "query": query,
            "max_results": limit,
        },
        timeout=45.0,
    )
    data = resp.raise_for_status().json()
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", "")[:500],
        }
        for item in data.get("results", [])[:limit]
    ]


async def _duckduckgo(ctx: ToolContext, query: str, limit: int) -> list[dict[str, Any]]:
    resp = await ctx.http.post(
        "https://html.duckduckgo.com/html/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=urllib.parse.urlencode({"q": query}).encode("utf-8"),
        timeout=45.0,
    )
    if not resp.ok:
        raise Unsupported(
            "Wyszukiwanie bez klucza API zostało odrzucone — dodaj klucz Brave albo Tavily"
        )
    results: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
        r'.*?(?:<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>)?',
        re.DOTALL,
    )
    for match in pattern.finditer(resp.text):
        url = html.unescape(match.group("url"))
        if url.startswith("//duckduckgo.com/l/?uddg="):
            url = urllib.parse.unquote(url.split("uddg=", 1)[1].split("&", 1)[0])
        results.append(
            {
                "title": html_to_text(match.group("title")),
                "url": url,
                "snippet": html_to_text(match.group("snippet") or "")[:400],
            }
        )
        if len(results) >= limit:
            break
    return results


def html_to_text(markup: str) -> str:
    """Strip markup down to readable text."""
    without_code = _SCRIPT_STYLE.sub(" ", markup)
    with_breaks = re.sub(r"</(p|div|li|h[1-6]|tr|section|article)>", "\n",
                         without_code, flags=re.IGNORECASE)
    with_breaks = re.sub(r"<br\s*/?>", "\n", with_breaks, flags=re.IGNORECASE)
    text = html.unescape(_TAGS.sub("", with_breaks))
    text = _WHITESPACE.sub(" ", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def extract_title(markup: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.DOTALL | re.IGNORECASE)
    return html.unescape(match.group(1)).strip()[:200] if match else ""


def _require_http(url: str) -> str:
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in ("http", "https"):
        raise ExecutionError(f"Obsługuję tylko http i https, nie {parsed.scheme}",
                             retryable=False)
    return parsed.geturl()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


__all__ = ["extract_title", "html_to_text", "register"]
