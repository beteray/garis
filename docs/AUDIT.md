# Audyt GARIS-a — stan faktyczny

Data: sierpień 2026. Podstawa: `a0c244e`.

Każde znalezisko poniżej zostało **sprawdzone uruchomieniem**, nie wywnioskowane
z kodu. Gdzie czegoś nie dało się sprawdzić, jest to napisane wprost.

Klasyfikacja: **P0** blokuje podstawowe użycie · **P1** powoduje błędne albo
mylące zachowanie · **P2** wymagane do wiarygodnej bety · **P3** dopracowanie ·
**Później** świadomie odłożone.

---

## Podsumowanie w jednym akapicie

GARIS ma solidny silnik i nieukończony produkt. Warstwa wykonawcza — jedna
kontrolowana ścieżka akcji, bramki zgody, szyfrowana pamięć, trwałe zadania —
jest napisana dobrze i pokryta 249 testami. Natomiast wszystko, co decyduje o
tym, czy zwykły człowiek da radę tego użyć, jest albo niekompletne, albo
niepodłączone: dodanie klucza nie działa bez restartu i nikt o tym nie mówi,
„witaj" zostaje wiecznym zadaniem, nieprawidłowy klucz raportuje się jako
sprawny, a interfejs pokazuje `vault://gemini_api_key`. To nie są usterki
kosmetyczne — to jest różnica między demem a produktem.

---

## Dowody: co zmierzyłem

### P0-1 — Dodanie klucza nie działa do czasu restartu silnika

Zmierzone. Po zapisaniu klucza Gemini przez API, `/api/state` nadal zwraca:

```json
"providers": [{"name": "fake", "available": true, "models": ["fake-1"]}]
```

Po restarcie silnika, bez żadnej innej zmiany:

```json
"providers": [{"name": "gemini", "available": true,
               "models": ["gemini-2.5-pro", "gemini-2.5-flash", ...]}, ...]
```

`build_router()` czyta sejf raz, przy starcie. Interfejs w tym czasie pisze
„klucz w sejfie" — czyli **komunikat prawdziwy i bezużyteczny naraz**. Użytkownik
dodaje klucz, widzi potwierdzenie, zleca zadanie i dostaje odmowę. To jest
dokładnie to, co zgłosiłeś jako „nie da się wykonać żadnego zadania".

**Naprawa:** router musi przeładować dostawców po zmianie w sejfie (zdarzenie z
`/api/vault`), a UI musi pokazywać stan dostawcy, nie stan sejfu.

### P0-2 — Powitanie staje się trwałym zadaniem

Zmierzone. `POST /api/tasks {"goal":"witaj"}` → zadanie `a81ac2a6`, po sześciu
sekundach:

```
blocked   goal='witaj'   err='Bez skonfigurowanego modelu AI poradzę sobie...'
```

Zostaje w liście aktywnych zadań na zawsze. Nie ma **żadnego** klasyfikatora
intencji — `agent/` zawiera `goal, planner, loop, verify, report` i nic, co
odróżniałoby rozmowę od pracy. Każde wpisane zdanie idzie tą samą drogą.

**Naprawa:** klasyfikator przed utworzeniem zadania. Zadanie powstaje tylko
wtedy, gdy jest co śledzić.

### P0-3 — Nieprawidłowy klucz raportuje się jako sprawny

Zmierzone. Zapisałem `CALKOWICIE-NIEPRAWIDLOWY-KLUCZ` jako `gemini_api_key`.
Po restarcie dostawca zgłasza `"available": true` i trzy modele. Nikt nigdy nie
zapytał Google, czy ten klucz cokolwiek otwiera.

Obecność sekretu w sejfie jest traktowana jako dowód sprawności dostawcy. To
nieprawda i prowadzi do najgorszego rodzaju awarii: wszystko wygląda na
skonfigurowane, a pierwsze prawdziwe zadanie pada z błędem 401 gdzieś w środku.

**Naprawa:** osobne stany dostawcy + health check. Szczegóły w
`docs/STATE_MACHINES.md`.

### P1-1 — Router wybiera model audio do czatu tekstowego

Zmierzone: `pick chat: gemini/gemini-2.5-flash-native-audio-preview (0.59)`.
Model preview do natywnego audio wygrywa ranking na zwykłą rozmowę tekstową.
Katalog nie rozróżnia modalności ani statusu „preview".

### P1-2 — „53 narzędzia" to liczba deklaracji, nie możliwości

Zmierzone: 53 zadeklarowane, 48 z `available: true`. Ale `available` to
wyłącznie `supported_here()` — sprawdzenie platformy. Nie znaczy: przetestowane,
uprawnione, działające na tej maszynie. Na Windows **żadne** z narzędzi
systemowych nie zostało nigdy wykonane.

`ToolSpec` ma schemat wejścia, timeout, `reversible`, efekty, zasoby i
`danger_note`. Nie ma: schematu wyjścia, rollbacku, weryfikacji, polityki
redakcji.

### P1-3 — Interfejs pokazuje wewnętrzne identyfikatory

Zmierzone, `/api/vault` tak jak czyta go UI:

```json
{"name": "gemini_api_key", "ref": "vault://gemini_api_key", "kind": "token"}
{"name": "api_token", "ref": "vault://api_token"}
```

Zwykły użytkownik nie ma prawa widzieć `vault://`. Powinien widzieć „Klucz
Gemini". Ta warstwa nazw ludzkich nie istnieje w ogóle.

### P1-4 — Stany zadania nie opisują tego, co się dzieje

Jest sześć: `pending, running, blocked, finished, failed, stopped`. `blocked`
oznacza jednocześnie: czekam na zgodę, czekam na odpowiedź, brakuje mi
konfiguracji. UI nie może z tego zbudować zdania „Wybierz katalog do
wyczyszczenia", bo ta informacja nie istnieje w modelu.

Brakuje: klasyfikacji, planowania, czekania na zasób, weryfikacji, odzyskiwania,
pauzy, ukończenia częściowego. Brak też przyczyny przejścia i aktora.

### P1-5 — Rekord pamięci nie mówi, dlaczego istnieje

Ma `source, confidence, created_at, used_at, expires_at, pinned, scope`.
Nie ma: klasyfikacji wrażliwości ani powodu zapamiętania. Pytanie „dlaczego to
pamiętasz?" jest dziś nieodpowiadalne.

### P2-1 — Brak taksonomii błędów

`errors.py` dzieli błędy na odzyskiwalne i blokujące. To wystarcza silnikowi i
nie wystarcza produktowi: nie da się odróżnić „zły klucz" od „brak sieci" od
„narzędzie padło", a każde z nich wymaga innej akcji od człowieka.

### P2-2 — Zero testów przepływów użytkownika

249 testów pokrywa funkcje. Żaden nie przechodzi ścieżki: instalacja →
onboarding → klucz → walidacja → proste zadanie → wynik. Wszystkie trzy błędy
P0 powyżej przeszłyby cały zestaw na zielono.

### P2-3 — Ustawienia to jedna strona, personalizacji nie ma

Dwanaście kategorii z Twojej listy nie istnieje. Motyw, akcent, skala,
gęstość, jakość kuli, dźwięki — brak. Fundament jest (`--glass-strength`,
`data-glass`, `data-quiet`, `data-theme` działają), ekranu nie ma.

### P2-4 — Głos ma widoczne, martwe przełączniki

Ustawienia pokazują „Rozmowa głosowa", „Słowo aktywacyjne", „Nasłuchiwanie",
„Push-to-talk" — wszystkie przełączalne, żaden nie robi nic, bo audio nie
istnieje. To wprost łamie Twój warunek „każde widoczne ustawienie musi działać
albo być oznaczone jako niedostępne".

### P3-1 — Dostępność nie była brana pod uwagę

Brak zarządzania fokusem, brak etykiet ARIA, dialogi bez pułapki fokusa,
wskaźniki stanu wyłącznie kolorem. `prefers-reduced-motion` jest respektowane —
to jedyny element z tej listy, który istnieje.

### P3-2 — Brak budżetów wydajności

Nic nie jest mierzone: czas startu, zużycie pamięci, CPU w spoczynku, klatki.
Kula WebGL renderuje niezależnie od tego, czy okno jest widoczne.

---

## Co naprawdę działa

Uczciwie, bo tego jest sporo i to jest fundament, na którym da się budować:

| Obszar | Status | Dowód |
|---|---|---|
| Jedna ścieżka wykonania, audyt każdej akcji | działa | 249 testów |
| Bramki zgody na pięciu efektach | działa | `test_policy.py` |
| Osobowość odcięta od polityki | działa | test strukturalny |
| Pamięć szyfrowana w spoczynku | działa | test czyta surowy plik |
| Sejf osobno, `vault://` podstawiany przy wywołaniu | działa | `test_executor.py` |
| Zadania przeżywają restart | działa | `test_tasks_resume.py` |
| Bramka powiadomień | działa | `test_notifications.py` |
| API + WebSocket + CORS | działa | `test_api.py` |
| Instalator wozi silnik | działa na Linuksie | `.deb`, uruchomione |
| Zapis klucza do sejfu | działa | sonda UI, zaszyfrowany |
| Nawigacja, brak przepełnień | działa | sonda UI, 4 rozmiary |

## Co jest tylko skompilowane

Tray, Mica, `Ctrl+Alt+G`, autostart, job object, `restart_engine`, kula WebGL.
Wszystko przechodzi kompilator pod target Windows. **Nic z tego nie zostało
uruchomione.**

## Czego nie ma wcale

Audio i głos. Klasyfikator intencji. Walidacja dostawców. Personalizacja.
Onboarding jako realna konfiguracja. Testy przepływów. Aktualizacje. Telemetria
i jej brak jako świadoma decyzja. Dostępność.

## Sprawdzone tylko na Linuksie

Cała ścieżka instalacji i uruchomienia, interfejs, CORS, sonda UI. Chromium to
nie WebView2 — `invoke`, Mica, skalowanie DPI i tray zachowują się inaczej.

## Sprawdzone na Windows

Build release, sidecar, `.msi`, instalacja, uruchomienie — przez Ciebie.
Wszystko dalej: czerwone, z przyczyną znalezioną (CORS) i naprawioną, ale
niezweryfikowaną na Windows po naprawie.
