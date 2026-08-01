# Przekazanie

Plik dla każdego, kto siada do tego projektu — człowieka albo modelu.
**Aktualizowany na koniec każdego etapu.**

Ostatnia aktualizacja: **M1 i M4 z `docs/ROADMAP.md` zrobione w silniku.**
Konfiguracja i sejf przeładowują się bez restartu; dostawca modeli ma mierzony
stan zamiast `available: bool`. Interfejsu tych rzeczy jeszcze nie pokazuje —
kontrakt API jest gotowy (`docs/API.md`), ekran nie.

Wcześniej: etap 2 (API + interfejs gotowe, powłoka natywna kompiluje się pod
Linux i pod target Windows, ale nie została uruchomiona).

**Zanim uwierzysz, że coś działa — przeczytaj `docs/CURRENT_STATE.md`.**
Rozdziela zweryfikowane od tylko-skompilowanego, bo w tym projekcie ta różnica
zdążyła już raz kosztować: powłoka miała za sobą pełny code review i nie
kompilowała się w ogóle.

- **`docs/AUDIT.md`** — audyt oparty na pomiarach, z klasyfikacją P0–P3. Zacznij tu.
- **`docs/ROADMAP.md`** — milestone'y M1–M13, kolejność zależności, definicja ukończenia.
- `docs/PRODUCT.md` — czym GARIS jest, model użytkownika, zgody, pamięć.
- `docs/STATE_MACHINES.md` — zadanie, połączenie, dostawca, klasyfikator, błędy.
- `docs/IA_AND_DESIGN.md` — mapa ekranów, materiały szkła, responsywność, dostępność, budżety.
- `docs/CURRENT_STATE.md` — co sprawdzone, co tylko skompilowane, co nietknięte.
- `docs/KNOWN_ISSUES.md` — co blokuje i co już zamknięte.
- `docs/WINDOWS_CHECKPOINT.md` — komendy i test ręczny do wykonania na Windows 11.

## Kto prowadzi

Projekt prowadzi Claude Code. Pierwotny plan zakładał, że fundament dostarczy
Codex, a Claude Code przejmie — to nie zaszło: repozytorium było puste, żadnego
commita. Cała architektura i cały kod etapu 1 powstały tutaj, od zera, i to jest
architektura, którą utrzymujemy dalej.

## Stan: co działa

Silnik jest kompletny i przetestowany (298 testów). Lokalne API działa. Interfejs
jest napisany i kompiluje się; powłoka natywna czeka na maszynę z Windows.

```
core/src/garis/
  paths errors events config crypto store net    infrastruktura
  settings.py                                    ustawienia na żywo: jeden pisarz, obserwator pliku
  memory.py vault.py                             stan, szyfrowanie (sejf ogłasza zmiany)
  runtime/                                       JEDYNA ścieżka wykonania
  models/ + models/providers/                    router + 5 dostawców
    health.py                                    7 statusów dostawcy, mierzonych
    pool.py                                      przebudowa przy zmianie + check co godzinę
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
.venv/bin/python -m pytest -q                 # 298 passed
.venv/bin/garis doctor
.venv/bin/garis do "sprawdź, ile miejsca zostało na dysku"

.venv/bin/garis serve --print-token           # silnik + API
cd apps/desktop && npm install && npm run dev # interfejs
```

Działa bez żadnego klucza API: atrapa dostawcy ma odruchowy planer, który radzi
sobie z prostym rozpoznaniem, a **odmawia** celów wymagających zmian, zamiast
udawać sukces.

## Czego nie ma

- **Nic nie było uruchomione na Windows.** Powłoka kompiluje się pod Linux i pod
  `x86_64-pc-windows-msvc`, a pełna ścieżka instalator → start → runtime została
  przejechana na paczce `.deb`. Windows-owe zostaje to, co naprawdę zależy od
  Windows: Mica, tray, `Ctrl+Alt+G`, autostart, `.msi`.
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
9. **Stan dostawcy jest mierzony, nie zakładany.** Obecność klucza w sejfie nie
   jest dowodem na nic. Odpowiedź dostawcy klasyfikuje jedno miejsce
   (`models/health.py`), nie każdy adapter po swojemu.
10. **Konfiguracja podmienia wartości, nie obiekt.** `Config.adopt()` zachowuje
    tożsamość pod-obiektów, bo pół silnika trzyma referencje do `config.autonomy`,
    `config.models` i `config.notifications`.

## Decyzje architektoniczne M1 + M4

Zapisane, bo każda z nich wyglądała na kwestię gustu, a nie jest:

- **`UNKNOWN` jest używalny przez router.** Gdyby brak pomiaru blokował, komputer
  bez sieci nie miałby agenta w ogóle. Wina blokuje, brak dowodu nie.
- **Siedem statusów silnika, dziewięć stanów produktowych.** Interfejs rozróżnia
  „brak środków" od „limit wyczerpany"; silnik ma na to jeden status i różny
  `reason` + `retry_after`. Mapowanie jest w `docs/STATE_MACHINES.md` §3 i to ono
  jest kontraktem — nie wolno go zgadywać po stronie UI.
- **`POST /api/vault` czeka na przebudowę dostawców.** Zdarzenie na szynie
  wystarcza obserwatorom, ale nie wystarcza wywołującemu: następne żądanie może
  brzmieć „wykonaj zadanie" i musi zastać nowy klucz. Zdarzenie zostaje dla
  reszty procesu, jawne wywołanie daje determinizm. Obie drogi wchodzą do tej
  samej idempotentnej `rebuild()`.
- **Odpytywanie pliku konfiguracji zamiast obserwacji katalogu.** Zależność,
  wątek i platformowe tryby awarii — po to, żeby zauważyć plik, który człowiek
  edytuje ręcznie może dwa razy w roku. `stat` co dwie sekundy zachowuje się tak
  samo na Windows, Linuksie i dysku sieciowym.
- **Health check to listing modeli, nie generowanie.** Sprawdzenie, które kosztuje
  pieniądze, przestanie być wykonywane — a wtedy wracamy do zgadywania.
- **Awaria w trakcie pracy aktualizuje zdrowie.** Router zgłasza każdą nieudaną
  próbę (`note_failure`), więc klucz unieważniony w południe nie udaje sprawnego
  do najbliższej pełnej godziny.

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

- **PyInstaller ma sens tylko wtedy, gdy zbudowaną binarkę się uruchomi.**
  `packaging/build_engine.py` odpala `garis doctor` na świeżo zamrożonym pliku,
  zanim skopiuje go do sidecarów. Binarka, która buduje się i umiera na
  brakującym imporcie, jest gorsza niż nieudany build — bundle zapakuje ją bez
  mrugnięcia.
- **`print()` do potoku nie dociera, dopóki proces nie skończy.** Powłoka czyta
  `API:` i `Token:` linia po linii z potoku, a `serve` nie kończy się nigdy —
  więc niezflushowany handshake to handshake, który nie przychodzi. Wyglądało
  na sprawne wyłącznie dlatego, że maszyna deweloperska miała
  `PYTHONUNBUFFERED=1`. `tests/test_engine_handshake.py` **usuwa tę zmienną**,
  bo z nią zepsuty silnik przechodzi.
- **Nie zamykaj potoku po odczytaniu handshake'u.** Wyjście z pętli czytającej
  zamyka `stdout` dziecka; następny zapis silnika idzie w nieistniejący potok.
  Trzeba drenować dalej — i przy okazji jest gdzie zapisywać log.
- **Token nie może trafić do logu.** Linia `Token: …` jest redagowana przy
  zapisie do `engine.log`; poświadczenie w pliku tekstowym to poświadczenie
  wyciekłe.
- **`plugins.autostart: {}` w `tauri.conf.json` wywala aplikację przy starcie.**
  Wtyczka oczekuje `unit`, nie mapy — `invalid type: map, expected unit`. Kod
  kompilował się, linkował, pakował, instalował i ginął natychmiast po
  uruchomieniu. Znalazło to dopiero prawdziwe odpalenie.
- **`@tauri-apps/cli` musi być w `devDependencies`.** Bez tego `npm run tauri
  build` kończy się na „could not determine executable to run" — i tak samo
  padłoby CI.
- **`externalBin` łamie `cargo check`, dopóki plik nie istnieje.** Do samego
  sprawdzania typów wystarczą puste atrapy dla obu tripletów (robi to CI).
- **`RunEvent::Exit` nie wystarcza na sieroty.** Przy `kill -9`, Menedżerze
  zadań czy crashu żaden handler się nie wykona. Na Windows domyka to job
  object z `KILL_ON_JOB_CLOSE`; na Linuksie sierota zostaje — sprawdzone.
- **`windows-sys` 0.59 ma typy job objectów, ale nie funkcje.** Trzy wywołania
  (`CreateJobObjectW`, `SetInformationJobObject`, `AssignProcessToJobObject`)
  deklarujemy sami, zamiast dokładać drugi, dużo większy crate.

- **Okno Tauri nigdy nie jest same-origin z silnikiem — potrzebny jest CORS.**
  Spakowana aplikacja serwuje strony z `tauri://localhost` (macOS/Linux) albo
  `http://tauri.localhost` (Windows), a silnik słucha na `127.0.0.1`. Każdy
  `fetch` z okna jest więc cross-origin i **przeglądarka blokuje go, zanim
  dojdzie do uwierzytelnienia**. Objaw: okno wstaje, mówi „łączę się…" i żaden
  przycisk nic nie robi — bo każdy przycisk to wywołanie API. Wygląda jak
  zepsuty frontend, a jest jedną brakującą funkcją w serwerze. Pilnuje tego
  `test_api.py`; lista dozwolonych origin, nigdy `*` — pod 127.0.0.1 może
  zapukać dowolna strona, którą użytkownik ma otwartą.
- **Skompilowany interfejs to nie obejrzany interfejs.** Cały etap 2 przeszedł
  bez jednego uruchomienia UI. `apps/desktop/tools/ui-probe.mjs` otwiera go w
  prawdziwym Chromium przeciwko żywemu silnikowi i w minutę znalazł brak CORS,
  przepełnienie układu i to, że panele są szarymi kaflami. Uruchamiaj po każdej
  zmianie w UI i **oglądaj zrzuty** — „brak przepełnienia" to nie to samo co
  „wygląda dobrze".
- **`elementFromPoint` mówi, co naprawdę przechwytuje kliknięcie.** Szybciej niż
  czytanie z-indeksów: sonda podaje ścieżkę elementu pod kursorem w miejscu
  przycisku.
- **Warstwa większa niż okno robi przewijanie całej aplikacji.** Aurora ma
  `inset: -20%`, żeby jej krawędzie nie wchodziły w kadr — bez `overflow: hidden`
  na `#root` ten zapas staje się przepełnieniem strony.
- **Szkło nad płaskim tłem to szary prostokąt.** `backdrop-filter` nad jednolitym
  ciemnym wypełnieniem rozmywa się do jednego koloru. Dopiero powolne, kolorowe
  światło pod spodem daje rozmyciu materiał — i dopiero wtedy panele różnią się
  od siebie.
- **Podmiana `garis.config` na nowy obiekt nie przeładowuje konfiguracji.**
  `PolicyEngine` dostaje `config.autonomy`, router `config.models`, bramka
  powiadomień `config.notifications` — po podmianie korzenia wszyscy trzej czytają
  obiekt, którego nikt już nie edytuje, i nic nie pęka głośno. Stąd
  `Config.adopt()`: wartości się zmieniają, tożsamości zostają.
- **`urllib` opakowuje timeout w `URLError`.** Bez zajrzenia w `exc.reason` „za
  wolno" i „nie ma trasy" wyglądają identycznie — a to dwa różne statusy dostawcy
  i dwie różne porady dla człowieka. Stąd `NetworkTimeout` i `NetworkUnreachable`
  w `errors.py`; rozróżnianie ich po treści komunikatu przeżyłoby dokładnie jedną
  zmianę wording-u.
- **`except BaseException` w pętli tła zjada `CancelledError`.** Sonda zdrowia ma
  raportować awarię zamiast rzucać, ale `BaseException` sprawia, że zadanie w tle
  przestaje dawać się zatrzymać. `Exception` wystarcza.
- **Godzinne „nadal działa" to nie jest wiadomość.** Zdarzenie `provider.health`
  leci wyłącznie przy realnej zmianie statusu, inaczej każde otwarte okno
  dostawałoby powiadomienie co godzinę, w nieskończoność.
- **Model natywnego audio deklarował `Job.CHAT`** i wygrywał ranking na pisaną
  rozmowę, bo jest szybki i tani. Katalog musi rozróżniać modalność: sesja
  mowa-w-mowę nie jest tańszym czatem.

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
.venv/bin/python -m pytest -q            # 298 testów, musi być zielone
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
