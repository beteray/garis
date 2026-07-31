# Przekazanie

Plik dla każdego, kto siada do tego projektu — człowieka albo modelu.
**Aktualizowany na koniec każdego etapu.**

Ostatnia aktualizacja: etap 2 w toku (API + interfejs gotowe, powłoka natywna
kompiluje się pod Linux i pod target Windows, ale nie została uruchomiona).

**Zanim uwierzysz, że coś działa — przeczytaj `docs/CURRENT_STATE.md`.**
Rozdziela zweryfikowane od tylko-skompilowanego, bo w tym projekcie ta różnica
zdążyła już raz kosztować: powłoka miała za sobą pełny code review i nie
kompilowała się w ogóle.

- `docs/CURRENT_STATE.md` — co sprawdzone, co tylko skompilowane, co nietknięte.
- `docs/KNOWN_ISSUES.md` — co blokuje. Najpierw §1: instalator nie wozi silnika.
- `docs/WINDOWS_CHECKPOINT.md` — komendy i test ręczny do wykonania na Windows 11.

## Kto prowadzi

Projekt prowadzi Claude Code. Pierwotny plan zakładał, że fundament dostarczy
Codex, a Claude Code przejmie — to nie zaszło: repozytorium było puste, żadnego
commita. Cała architektura i cały kod etapu 1 powstały tutaj, od zera, i to jest
architektura, którą utrzymujemy dalej.

## Stan: co działa

Silnik jest kompletny i przetestowany (243 testy). Lokalne API działa. Interfejs
jest napisany i kompiluje się; powłoka natywna czeka na maszynę z Windows.

```
core/src/garis/
  paths errors events config crypto store net    infrastruktura
  memory.py vault.py                             stan, szyfrowanie
  runtime/                                       JEDYNA ścieżka wykonania
  models/ + models/providers/                    router + 5 dostawców
  tools/                                         53 narzędzia
  agent/                                         plan → wykonaj → sprawdź → napraw
  tasks/                                         trwałość, współbieżność
  api/                                           HTTP + WebSocket dla wszystkich powierzchni
  voice/                                         rozmowa: stany, wake word, kalibracja, wybór drogi
  notifications.py                               bramka: cisza, gra, próg, duplikaty
  text.py                                        składanie tekstu do porównań
  app.py cli.py                                  złożenie + CLI

apps/desktop/                                    interfejs (etap 2)
  src/                                           React + TS + framer-motion + WebGL
  src-tauri/                                     powłoka: tray, Mica, autostart
```

Uruchomienie:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q                 # 173 passed
.venv/bin/garis doctor
.venv/bin/garis do "sprawdź, ile miejsca zostało na dysku"

.venv/bin/garis serve --print-token           # silnik + API
cd apps/desktop && npm install && npm run dev # interfejs
```

Działa bez żadnego klucza API: atrapa dostawcy ma odruchowy planer, który radzi
sobie z prostym rozpoznaniem, a **odmawia** celów wymagających zmian, zamiast
udawać sukces.

## Czego nie ma

- **Powłoka Tauri nie została skompilowana.** Kod Rusta (tray, Mica, autostart,
  cykl życia silnika) jest napisany, ale ta sesja nie miała ani Windows, ani
  webkit2gtk. `npm run tauri build` to pierwsza rzecz do zrobienia na Windows —
  spodziewaj się drobnych poprawek w API Tauri 2, nie w architekturze.
- Ikony aplikacji (`src-tauri/icons/`) są puste — trzeba wygenerować.
- audio i słowo aktywacyjne (etap 3), mobile (etap 7)
- ścieżki natywne Windows **nie były uruchomione na Windows** — powstały na
  Linuksie, z deklaracją platformy i `Unsupported` poza Windows. To pierwsza
  rzecz do zrobienia w etapie 4, razem z CI na `windows-latest`
- MCP, pluginy, poczta/kalendarz (etap 8)


## Zasady, których nie wolno złamać

Nie są kwestią gustu. Każda ma test, który przewróci się przy naruszeniu.

1. **Nic nie wykonuje się poza `Runtime.perform()`.** Narzędzie wołające narzędzie
   używa `ctx.perform()`. Zero bezpośrednich `subprocess` w agencie czy zadaniach.
2. **Efekty deklaruje `ToolSpec`, nie wołający.** Dodajesz narzędzie, które wysyła
   wiadomość — deklarujesz `Effect.SEND_MESSAGE`, choćby to była „tylko notatka".
3. **Osobowość nie dotyka polityki.** `PolicyEngine` przyjmuje `autonomy` i `paths`.
   Koniec.
4. **Poświadczenia nie są pamięcią.** Zawsze sejf, zawsze `vault://` w parametrach.
5. **Warstwa niżej nie importuje wyższej.** `models` nie wie o `agent`; `runtime`
   nie wie o `tasks`.
6. **Awaria narzędzia to `ActionResult`, brak zgody to wyjątek.** Nie zamieniaj.
7. **Cisza jest domyślna.** `ctx.progress()` do dziennika; `ctx.note()` tylko gdy
   człowiek naprawdę powinien to usłyszeć.
8. **Raport buduje się z faktów** (`agent/report.py`), nie z modelu. Model nie
   opowiada, co zrobił.

## Pułapki, w które już wpadłem

Zapisane, żeby nie wpaść drugi raz:

- **`str.format` na promptach z przykładami JSON.** Klamry przykładu trzeba
  podwoić (`{{`, `}}`). Kosztowało to całą weryfikację (`KeyError: '"ok"'`).
- **`resume()` musi zmienić stan synchronicznie.** Zanim korutyna wystartuje,
  wszyscy obserwatorzy widzą stary stan — zatwierdzona zgoda wyglądała, jakby nic
  nie zrobiła.
- **Zadanie BLOCKED musi zapisać pytanie do bazy.** Sam stan nie mówi, na co czeka.
- **Atrapa dostawcy nie może odpowiadać z kolejki.** Dwa równoległe zadania mieszają
  kolejność wywołań; `role_aware()` rozpoznaje prompt po roli.
  `tests/test_fake_provider.py` pilnuje tego sprzężenia — jeśli zmienisz prompt
  planera lub weryfikatora, ten test Ci o tym powie.
- **`ctypes.wintypes` nie importuje się na Linuksie.** Struktury DPAPI budowane
  leniwie, w środku funkcji.
- **pytest importuje `tests/conftest.py` jako `conftest`.** `from tests.conftest
  import X` daje drugą kopię modułu i cicho gubi stan. Współdzielony stan → fixture.
- **Podglądy i tematy pamięci też zdradzają treść.** Dlatego wszystko idzie do
  jednej koperty, a jawne zostają wyłącznie metadane.
- **Synchroniczny klient HTTP wołany z pętli zdarzeń zakleszcza serwer w tym
  samym procesie.** Skrypt demonstracyjny wisiał, dopóki nie poszedł na gniazda
  asynchroniczne. Serwer był w porządku.
- **RFC 6455: ramki klienta są maskowane, ramki serwera nie.** Jeden czytnik nie
  obsłuży obu kierunków bez parametru (`read_frame(expect_mask=...)`).
- **Metoda o nazwie `list` przesłania wbudowany typ w adnotacjach metod tej samej
  klasy.** `MemoryService`, `Vault`, `TaskStore` i `TaskSupervisor` mają `list()`,
  więc `-> list[X]` w ich sygnaturach oznaczało metodę, nie listę. W czasie
  wykonania nic nie pękało (adnotacje są łańcuchami), ale typy u wszystkich
  wywołujących były fikcją. Zwracamy `Sequence[X]`.
- **Ref mutowany w `useEffect` nie przerysowuje Reacta.** Fallback kuli bez WebGL
  nigdy by się nie pokazał — na maszynie bez WebGL użytkownik zobaczyłby pustkę.
- **`useStore()` bez selektora subskrybuje cały store.** Każda linia postępu
  przerysowywała całe drzewo.
- **`async def` w Protocolu to nie to samo co async generator.** Protokół
  dostawcy deklarował `async def stream(...) -> AsyncIterator[str]`, czyli
  korutynę zwracającą iterator; implementacje są generatorami. mypy to złapał.
- **NFKD nie rozkłada polskiego `ł`.** To jeden znak bez dekompozycji, więc
  „usuń znaki składające, zostaw a-z" zamieniało je w spację: `wołam` → `wo am`.
  Dotyczyło i wyszukiwania w pamięci, i słowa aktywacyjnego. Jedna implementacja
  w `text.py`, jeden test.
- **Powłokę w Ruście da się sprawdzić na Linuksie i trzeba to robić.** Przez
  długi czas nikt jej nie skompilował ani razu — i siedział w niej zwykły błąd
  kompilacji: `app.global_shortcut()` bez `use GlobalShortcutExt`. Tauri jest
  Windows-first, ale to nie jest kod tylko-dla-Windows; brakujący import,
  przeniesiona wartość czy zmieniona nazwa w pluginie psują się tak samo
  wszędzie. Wystarczy zainstalować biblioteki systemowe i uruchomić `cargo
  clippy` (patrz „Praca" niżej) — a w CI robi to zadanie `shell-check`, zanim
  w ogóle ruszy wolny build na Windows.

## Etap 2 — co zrobione, co zostało

Zrobione:

1. ✅ Lokalne API (`core/src/garis/api/`) + `docs/API.md` — 31 testów.
2. ✅ Interfejs: kula WebGL ze stanami, panele (rozmowa, zadania, pamięć + sejf,
   urządzenia, subskrypcje, ustawienia, diagnostyka), karty zgód, onboarding
   jako rozmowa. `npm run build` przechodzi, TypeScript strict.
3. ✅ Powłoka Tauri napisana: tray, hide-on-close, Mica, autostart, skrót
   globalny, uruchamianie i dołączanie do silnika.

Zostało:

4. ⏳ **Zbudować powłokę na Windows 11** i poprawić to, co się posypie.
5. ⏳ Ikony aplikacji i tray-a.
6. ⏳ Instalator (MSI/NSIS) + podpis.
7. ⏳ Test „zamknięcie okna nie zatrzymuje zadania" na realnym Windows.

Wymagania wizualne są nienegocjowalne i opisane w `docs/UI.md`: Liquid Glass,
framer-motion, wszystko animowane, `prefers-reduced-motion` respektowane.

## Praca

```bash
.venv/bin/python -m pytest -q            # 243 testy, musi być zielone
.venv/bin/ruff check .
.venv/bin/python -m mypy
cd apps/desktop && npm run build         # TypeScript strict + Vite
```

Powłoka w Ruście — sprawdzalna na Linuksie, raz zainstaluj biblioteki:

```bash
sudo apt-get install -y libwebkit2gtk-4.1-dev libgtk-3-dev \
  libayatana-appindicator3-dev librsvg2-dev libsoup-3.0-dev
cd apps/desktop && npm run build         # generate_context! chce gotowego dist/
cd src-tauri && cargo clippy --all-targets -- -D warnings
```

Pełnego `.msi` nie zbudujesz poza Windows, ale błędy kompilacji złapiesz tutaj.

## Konwencje

- Python 3.11+, wcięcia 4, `ruff` (linia 100), `mypy` na `core/src/garis`.
- Komentarze wyjaśniają **dlaczego**, nie **co**. Kod mówi, co robi.
- Komunikaty do użytkownika po polsku, jedno zdanie, bez żargonu.
- Nazwy w kodzie i docstringi po angielsku.
- Test opisuje gwarancję produktu, nie implementację. Nazwa testu to zdanie o
  zachowaniu.
- Commit: co i **dlaczego**; jeśli naprawia realny błąd, opisz błąd.
