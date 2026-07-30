# Jak dodać zdolność

Narzędzie to jedna funkcja z dekoratorem. Rejestr jest jednocześnie menu dla
planera, źródłem prawdy dla polityki i wejściem do audytu — więc deklaracja musi
być uczciwa.

## Minimum

```python
from ..runtime import Effect, ParamSpec, ToolContext, ToolRegistry, file_key


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        "backup_create",
        "Tworzy kopię zapasową katalogu do archiwum ZIP.",
        params={
            "source": ParamSpec("path", "Katalog do zarchiwizowania", required=True),
            "destination": ParamSpec("path", "Plik docelowy .zip", required=True),
        },
        effects=[Effect.READ, Effect.WRITE],
        category="backup",
        resources=lambda p: [file_key(p["source"]), file_key(p["destination"])],
        timeout=1800.0,
    )
    async def backup_create(ctx: ToolContext, source: str, destination: str) -> dict:
        ctx.progress(f"Archiwizuję {source}")
        ...
        return {"archive": destination, "bytes": size}
```

Dopisz moduł do `ALL_MODULES` w `core/src/garis/tools/__init__.py` i, jeśli ma
sens na serwerze, do `SERVER_MODULES`.

## Co deklarujesz i dlaczego

| Pole | Znaczenie |
|---|---|
| `effects` | **Co to robi ze światem.** Na tej podstawie polityka decyduje o zgodzie. Deklaruj najgorszy możliwy skutek, nie typowy. |
| `params` | Schemat. Walidacja zatrzymuje złe wywołanie przed uruchomieniem czegokolwiek, a komunikat błędu jest tak sformułowany, żeby model umiał się poprawić. |
| `platforms` | `("win32",)` dla narzędzi Windows. Poza platformą narzędzie jest ukryte przed planerem, a nie psuje planu w połowie. |
| `resources` | Klucze dzierżaw: `file:`, `app:`, `device:`, `host:`, `service:`, `registry:`. Bez nich dwa zadania wejdą na ten sam plik. |
| `reversible` | `False` → zawsze potwierdzenie. |
| `trusted_source` | Dla `INSTALL`: czy repozytorium jest zaufane (winget, apt). |
| `estimate` | `(bajty, koszt)` → progi „uprzedź przed dużym pobraniem / kosztem". |
| `timeout` | Sekundy. Runtime egzekwuje bezwarunkowo. |
| `danger_note` | Zdanie dopisywane do prośby o zgodę. |
| `examples` | Wzorcowe wywołania — trafiają do promptu planera. |

## Co daje `ctx`

- `ctx.perform("inne_narzedzie", ...)` — wywołanie zagnieżdżone, przez te same
  bramki. Nigdy nie wołaj handlera innego narzędzia bezpośrednio.
- `ctx.progress(tekst)` — do dziennika i UI. **Nie** powiadamia użytkownika.
- `ctx.note(tekst, importance=1..5)` — kandydat na powiadomienie; warstwa
  powiadomień zdecyduje, czy i kiedy.
- `ctx.secret("nazwa")` — wartość z sejfu. Zwykle niepotrzebne: wystarczy przyjąć
  parametr, a użytkownik/planer poda `vault://nazwa` — runtime podstawi wartość.
- `ctx.task_workspace()` — katalog roboczy zadania.
- `ctx.http` — klient HTTP (proxy i magazyn zaufania systemu).
- `ctx.memory`, `ctx.vault`, `ctx.db`, `ctx.paths`, `ctx.config`, `ctx.bus`.
- `ctx.remaining_seconds` — ile czasu zostało do timeoutu.

## Błędy

```python
from ..errors import ExecutionError, Unsupported

raise ExecutionError("Serwer odrzucił połączenie")                  # spróbuje ponownie
raise ExecutionError("Nie ma takiego pliku", retryable=False)        # przeplanuje
raise Unsupported("Wymaga pywin32")                                 # ukryte, nie próbuje
```

Nie łap wyjątków, żeby zwrócić `{"ok": False}` — runtime zamieni wyjątek na
`ActionResult` i zapisze audyt. Zwracaj dane albo rzucaj.

## Czego nie robić

- Nie uruchamiaj `subprocess` poza narzędziami — to obejście bramek.
- Nie zwracaj megabajtów. Obcinaj i zapisuj resztę do `ctx.task_workspace()`.
- Nie loguj wartości sekretów i nie wstawiaj ich do wyniku.
- Nie pytaj użytkownika z wnętrza narzędzia. Brak informacji → `ExecutionError`
  z jasnym komunikatem; pytanie zadaje agent.
- Nie deklaruj `READ`, jeśli coś zmieniasz. To złamanie modelu bezpieczeństwa.

## Test

Każde nowe narzędzie potrzebuje testu, który sprawdza gwarancję, nie implementację:

```python
async def test_backup_declares_its_resources(runtime):
    spec = runtime.registry.get("backup_create")
    assert spec.resource_keys({"source": "/a", "destination": "/b.zip"})
```
