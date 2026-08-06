# Roadmapa — kolejność, zależności, definicja ukończenia

Zastępuje roadmapę etapów 1–8 (zachowaną w `ROADMAP_ETAPY_1_8.md`).
Podstawa: `docs/AUDIT.md`.

**Zasada porządkująca:** nie budujemy nic nowego, dopóki to, co istnieje, nie
działa dla kogoś innego niż autor. Głos pozostaje zablokowany.

---

## Kolejność zależności

Strzałka znaczy „nie ma sensu zaczynać przed".

```
M1 Prawda o stanie ──┬──► M2 Klasyfikator ──► M3 Zadania
                     │
                     └──► M4 Dostawcy ──────► M5 Onboarding ──► M6 Checkpoint
                                                                     │
M7 Nazwy ludzkie ────────────────────────────────────────────────────┤
M8 Ustawienia + personalizacja ──────────────────────────────────────┤
M9 System wizualny ──────────────────────────────────────────────────┤
M10 Dostępność ──────────────────────────────────────────────────────┤
M11 Wydajność ───────────────────────────────────────────────────────┤
                                                                     ▼
                                                          M12 Beta → M13 Głos
```

Dlaczego tak: **M1 i M4 są warunkiem wszystkiego.** Dopóki dodanie klucza nie
działa bez restartu, a nieprawidłowy klucz raportuje się jako sprawny, każdy test
produktu bada nieprawdę. Klasyfikator (M2) musi być przed pracą nad zadaniami
(M3), bo inaczej projektujemy stany dla obiektów, które w ogóle nie powinny
powstawać.

Silnikowa część M1 i M4 stoi dziś na jednej kopercie wykonania
(`CapabilityRunner`, patrz `docs/ARCHITECTURE.md`): jedna decyzja polityki, jeden
wpis audytu i jeden efekt na wywołanie, wspólne dla narzędzi i zdolności. M2 i M3
dziedziczą to i nie muszą tego budować.

---

## M1 — Prawda o stanie · **P0** · silnik zrobiony

Naprawa trzech zmierzonych błędów, które sprawiają, że produkt kłamie.

- ✅ Router przeładowuje dostawców po zmianie w sejfie, bez restartu silnika.
- ✅ Stan dostawcy przestaje wynikać z obecności klucza (patrz M4).
- ✅ Ustawienia zmieniają się na żywo: `SettingsService`, walidacja na kopii,
  zdarzenie `config.changed`, obserwator pliku dla ręcznych edycji.
- ⏳ Zadania nie mogą utknąć: timeouty na wszystkich stanach czekających. **To
  jest część M3** (czternaście stanów) i nie zostało tknięte.
- ⏳ `port-taken` jako osobny stan połączenia — powłoka + UI, nie silnik.

**Ukończone, gdy:** test end-to-end zapisuje klucz przez API i **bez restartu**
zleca zadanie, które kończy się wynikiem.
→ `tests/test_reload.py::test_a_task_submitted_after_saving_a_key_finishes_on_that_provider`.
Idzie przez prawdziwy adapter Gemini, prawdziwy router i prawdziwy planer;
podmieniony jest wyłącznie transport HTTP.

## M2 — Klasyfikator intencji · **P0**

- Dziesięć klas z `docs/STATE_MACHINES.md` §4.
- Przypadki oczywiste (powitanie, podziękowanie, potwierdzenie) rozstrzygane
  **bez modelu chmurowego**.
- Zadanie powstaje tylko wtedy, gdy jest co śledzić.

**Ukończone, gdy:** „witaj", „dzięki", „ok" nie tworzą zadania; „posprzątaj
Pobrane" tworzy. Test na obu.

## M3 — Zadania, które mówią, czego chcą · **P0/P1**

- Czternaście stanów zamiast sześciu.
- Przejścia z powodem, czasem, aktorem i zdaniem dla użytkownika.
- `waiting_input` bez pytania = błąd programu.
- Ekran zadania wg `docs/IA_AND_DESIGN.md`.
- Anuluj / wstrzymaj / wznów działają naprawdę.

**Ukończone, gdy:** żaden stan nie wyświetla się jako „czekam na Ciebie" bez
konkretnego pytania, a każde zadanie da się anulować i wznowić.

## M4 — Dostawcy i router · **P0** · silnik zrobiony

- ✅ Siedem statusów silnika (dziewięć stanów produktowych mapuje się na nie —
  `docs/STATE_MACHINES.md` §3), health check przy zapisie, starcie i co godzinę.
- ✅ Powód niedostępności dociera do użytkownika: `reason` w API, w `NoModelAvailable`
  i w `garis doctor`.
- ✅ Router wybiera tylko sprawnych dostawców; awaria w trakcie pracy natychmiast
  aktualizuje stan.
- ✅ Modalność w kryteriach routingu — modele natywnego audio nie deklarują już
  `Job.CHAT`, więc nie wygrywają rankingu na pisaną rozmowę.
- ⏳ Status `preview` i preferencja użytkownika jako kryteria.
- ⏳ Zero komend CLI w interfejsie; przycisk „Otwórz ustawienia modeli" — UI.

**Ukończone, gdy:** nieprawidłowy klucz pokazuje „Klucz odrzucony przez
dostawcę" w ciągu kilku sekund od zapisania, a nie przy pierwszym zadaniu.
→ `tests/test_reload.py::test_an_invalid_key_says_so_within_seconds_of_being_saved`.
Odpowiedź na `POST /api/vault` niesie już zmierzony stan; ekran, który to
pokaże, jest do zrobienia.

## M5 — Onboarding jako pierwsze spotkanie · **P1**

- Progresywnie, z jasnym podziałem: wymagane / zalecane / opcjonalne.
- Każdy krok nieistotny da się pominąć.
- Na końcu **prawdziwy test**: połączenie z runtime, walidacja dostawcy, krótka
  rozmowa, proste zadanie tylko-do-odczytu, test powiadomienia.

**Ukończone, gdy:** ktoś, kto nigdy nie widział GARIS-a, dochodzi od instalatora
do wykonanego zadania bez pytania autora o cokolwiek.

## M6 — Checkpoint Windows · **P0**

Wszystko z `docs/WINDOWS_CHECKPOINT.md` po naprawach M1–M5. To jedyny moment,
w którym „skompilowane" zamienia się w „działa": tray, Mica, `Ctrl+Alt+G`,
autostart, job object, skalowanie DPI, cykl życia.

**Ukończone, gdy:** cała lista ręczna odhaczona na prawdziwej maszynie.

## M7 — Nazwy ludzkie · **P1**

- Warstwa nazw: `Klucz Gemini` zamiast `gemini_api_key` i `vault://…`.
- Rozdzielenie siedmiu rodzajów danych wg `docs/PRODUCT.md`.
- Pamięć: powód zapamiętania + klasyfikacja wrażliwości.
- Cztery pytania o pamięć odpowiadalne.

**Ukończone, gdy:** w interfejsie poza trybem developerskim nie pada ani jeden
identyfikator techniczny. Test sprawdza to na zrzucie tekstu wszystkich ekranów.

## M8 — Ustawienia i personalizacja · **P2**

- Dwanaście kategorii.
- Profil użytkownika z `docs/PRODUCT.md`, ze źródłem każdego pola.
- Adaptacja obserwowana: widoczna, odrzucalna pojedynczo, wyłączalna globalnie.
- **Najpierw usunąć martwe przełączniki głosu** — to jest P1 i idzie wcześniej.

**Ukończone, gdy:** każde widoczne ustawienie zmienia zachowanie albo jest
oznaczone jako niedostępne. Test przechodzi po wszystkich kontrolkach.

## M9 — System wizualny · **P2/P3**

- Osiem materiałów zamiast jednej klasy `.glass`.
- Tokeny semantyczne.
- Kula rozróżnia dwanaście stanów, nie tylko kolorem.
- Wygląda dobrze przy wyłączonych efektach.

## M10 — Dostępność · **P2**

Lista kontrolna z `docs/IA_AND_DESIGN.md`. Dziś spełniony jeden punkt z dziesięciu.

## M11 — Wydajność · **P2**

Budżety zmierzone na Windows. Redukcja efektów przy zminimalizowaniu, w tray-u,
w trybie gry, na baterii, na pulpicie zdalnym. Kula przestaje renderować
niewidoczna.

## M12 — Beta: dystrybucja i zaufanie · **P2**

- Kanały: developer / preview / stable.
- Podpisane instalatory, sprawdzanie aktualizacji, notatki, wycofanie.
- **Migracja ustawień, pamięci i sejfu** między wersjami — bez tego pierwsza
  aktualizacja kasuje ludziom dane.
- Prywatność wprost: co zostaje lokalnie, co idzie do dostawcy, co jest logowane,
  jak sekrety są redagowane, jak wyeksportować i skasować wszystko.
- **Zero telemetrii bez wyraźnej zgody.**

## M13 — Głos · **Później**

Odblokowany dopiero, gdy: przyciski działają · runtime się łączy · klucz da się
zapisać i zwalidować · proste zadanie kończy się wynikiem · resize działa ·
podstawowe ustawienia są funkcjonalne.

Kolejność wewnątrz: urządzenia → przechwytywanie → poziomy RMS/peak →
kalibracja → push-to-talk → STT → TTS → przerwanie → integracja z runtime →
panel konfiguracji.

---

## Testy przepływów — przekrojowe, nie osobny etap

Dochodzą razem z milestone'ami, których dotyczą. Osiemnaście scenariuszy:
instalacja · onboarding · zapis klucza · walidacja · powitanie · pytanie
faktograficzne · zadanie lokalne · zadanie długie · anulowanie · zadanie
wymagające doprecyzowania · zadanie wymagające zgody · awaria runtime · awaria
dostawcy · ponowne połączenie · restart · cykl tray · aktualizacja ·
odinstalowanie.

Regresja wizualna: rozmiary okna · motywy · skalowanie · stany błędu · stany
puste · długi tekst polski i angielski.

**Warunek jakości:** wszystkie trzy błędy P0 z audytu przeszłyby dzisiejszy
zestaw 249 testów na zielono. Testy jednostkowe nie wystarczą.

---

## Plan migracji z obecnej alfy

1. Wersjonowanie schematu bazy stanu i sejfu **przed** pierwszą zmianą modelu
   danych. Migracje istnieją w `store.py`, ale nikt ich nie ćwiczył.
2. Stare stany zadań mapują się na nowe: `pending→received`, `running→executing`,
   `blocked→waiting_input` (z pustym pytaniem, oznaczone do uzupełnienia),
   `finished→completed`, `failed→failed`, `stopped→cancelled`.
3. Sekrety zachowują nazwy techniczne; warstwa nazw ludzkich jest dodawana obok,
   nie zamiast — istniejące sejfy działają dalej.
4. Profil użytkownika powstaje z obecnego `identity` + `persona` w konfiguracji,
   z oznaczeniem źródła `chosen`.
5. Test migracji uruchamiany na bazie z poprzedniej wersji, w CI.

## Wizja 1.0 a rzeczywistość

Dokument wizji („General AI Runtime Intelligence System", 8 faz) opisuje cel i
**nie opisuje drzewa katalogów**. Jego układ plików — `core/agent.py`,
`ai/router.py`, `memory/`, `mcp/`, `plugins/` — nigdy nie powstał; powstał układ
warstwowy z `kernel/` i regułą, że warstwa niżej nie importuje wyższej, czego
wizja nie ma jak wyrazić. Tabela poniżej mapuje jedno na drugie, żeby nikt nie
szukał katalogu, którego nie ma, ani nie zakładał, że coś działa, bo widnieje w
wizji.

Stan zmierzony, nie zadeklarowany.

| Podsystem wizji | Stan | Gdzie to naprawdę jest | Czego brakuje |
|---|---|---|---|
| Core / agent | **jest** | `agent/` (cel → plan → wykonaj → sprawdź → napraw), `runtime/runner.py` | — |
| AI Router + dostawcy | **częściowo** | `models/router.py` + `providers/{openai,anthropic,gemini,ollama}.py` | llama.cpp, LM Studio — brak dostawcy zgodnego z OpenAI |
| Tool Engine | **jest** | `tools/` — 56 narzędzi w 8 modułach | sterowanie przeglądarką (brak playwright/selenium) |
| Pamięć | **częściowo** | `memory.py` (szyfrowana, `Scope`, 10 rodzajów), `vault.py` | wyszukiwanie wektorowe — **świadomie**, patrz `memory.py:5`; profil użytkownika (M8) |
| Bezpieczeństwo | **jest** | `runtime/policy.py` — `ALLOW`/`CONFIRM`/`DENY`, `runtime/audit.py`, `runtime/approvals.py` | audyt łańcuchowy (hash chain) — przyjęty, niezbudowany |
| Głos | **częściowo** | `voice/` — stany, słowo aktywacyjne, kalibracja, wybór drogi | **cała warstwa sprzętowa**: STT/TTS to atrapy testowe (`voice/engines.py:160,190`), brak wejścia audio. M13 |
| Vision | **częściowo** | `tools/desktop.py` — `screen_capture`; obsługa obrazu w `models/base.py` i adapterach | kamera, OCR, czytanie ekranu; nic nie podaje zrzutu do `Message.images` |
| MCP | **nie ma** | — | całość |
| Pluginy | **nie ma** | `paths.py:90` tworzy pusty katalog, którego nikt nie czyta | całość — patrz `KNOWN_ISSUES.md` |
| Scheduler / autonomia | **nie ma** | — | **świadomie**, patrz `apps/desktop/src/lib/nav.ts:11` |
| UI | **częściowo** | 6 widoków w `apps/desktop/src/lib/nav.ts` | „Tools" tylko w trybie developerskim; „Plugins" nie istnieje |

Fazy 1–8 wizji odpowiadają etapom w `ROADMAP_ETAPY_1_8.md` (zachowanym jako
historia). MCP i pluginy to tam etap 8 i pozostają tam, gdzie były.

## Czego świadomie nie robimy teraz

Mobile · GARIS Server · MCP i pluginy · modele lokalne · integracje poczty i
kalendarza · wielojęzyczność poza polskim i angielskim. Wszystko to jest
sensowne i wszystko to jest bez znaczenia, dopóki produkt nie działa dla jednej
osoby na jednym komputerze.

Do tej listy należy też **wyszukiwanie wektorowe w pamięci** i **scheduler**.
Oba wyglądają na oczywiste braki i oba są decyzją: pamięć trzyma pola, po których
da się wyszukiwać i które da się pokazać człowiekowi, zamiast nieprzejrzystego
wektora, a panel automatyzacji bez ani jednego producenta zdarzeń byłby ekranem
udającym ustawienia.
