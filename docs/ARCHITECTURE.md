# Architektura GARIS-a

## Jedna zasada, z której wynika reszta

Użytkownik mówi **jaki rezultat** chce. Wszystko inne — model, narzędzie, kolejność,
obsługa błędów — jest decyzją GARIS-a. Z tego wynikają cztery własności, które
architektura musi gwarantować, a nie tylko obiecywać:

1. **Jedna kontrolowana ścieżka wykonania.** Model proponuje; runtime decyduje.
2. **Trwałość.** Zadanie przeżywa restart aplikacji i komputera.
3. **Cisza.** Milczenie i samodzielna naprawa są domyślne.
4. **Zgoda na skutkach, nie na krokach.**

## Warstwy

Warstwa niżej nigdy nie importuje wyższej. To jest sprawdzalne i sprawdzane.

```
┌──────────────────────────────────────────────────────────────┐
│  powierzchnie:  cli · api · voice · host (tray/autostart)     │
├──────────────────────────────────────────────────────────────┤
│  tasks/         trwałe, współbieżne, wznawialne zadania       │
├──────────────────────────────────────────────────────────────┤
│  agent/         cel → plan → wykonaj → sprawdź → napraw       │
├──────────────────────────────────────────────────────────────┤
│  tools/         zdolności (53) rejestrowane w runtime         │
├──────────────────────────────────────────────────────────────┤
│  models/        router + dostawcy (OpenAI/Claude/Gemini/…)    │
├──────────────────────────────────────────────────────────────┤
│  runtime/       JEDYNA droga wykonania: polityka, zgody,      │
│                 dzierżawy zasobów, audyt                      │
├──────────────────────────────────────────────────────────────┤
│  stan:          memory (szyfrowana) · vault (osobno)          │
├──────────────────────────────────────────────────────────────┤
│  infrastruktura: paths · config · crypto · store · net · events│
└──────────────────────────────────────────────────────────────┘
```

## Droga jednej akcji

Każda zdolność GARIS-a przechodzi przez `runtime.Runtime.perform()` i te same
siedem bramek w tej samej kolejności:

```
Action(tool, params, intent, task_id)
   │
   1. resolve      narzędzie istnieje w rejestrze?         → nie: ActionResult(not_found)
   2. validate     platforma + schemat parametrów           → nie: ActionResult(invalid)
   3. policy       efekty → ALLOW / CONFIRM / DENY          → DENY: PolicyDenied (wyjątek)
   4. approval     zgoda w bazie? zużyj : poproś            → brak: ApprovalRequired (wyjątek)
   5. lease        dzierżawa pliku/programu/urządzenia      → zajęte za długo: busy
   6. run          podstawienie vault:// + timeout          → błąd: ActionResult(error)
   7. audit        intencja, decyzja, wynik, czas
   │
   ActionResult
```

Dwie różne rzeczy dzieją się z błędami, i to jest celowe:

- **Awaria narzędzia to wartość** (`ActionResult.ok == False`). Pętla agenta ma ją
  obsłużyć: ponowić, zmienić narzędzie, przeplanować. Użytkownik nic nie słyszy.
- **Brak zgody i twarda odmowa to wyjątki.** Muszą przerwać przepływ, bo zadanie
  ma się zatrzymać i czekać na człowieka.

Narzędzie wołające inne narzędzie robi to przez `ctx.perform()`, które wraca do
punktu 1. Nie ma drzwi na skróty.

## Dlaczego efekty deklaruje narzędzie

`ToolSpec.effects` to jedyne źródło prawdy o tym, co akcja robi ze światem.
Model nie może przemianować płatności na odczyt, opisując ją inaczej — bo
polityka patrzy na deklarację narzędzia, nie na słowa modelu. Test
`test_registry_declares_effects_not_the_caller` pilnuje tej granicy.

Efekty: `read`, `write`, `delete_permanent`, `exec`, `install`, `network`,
`payment`, `publish`, `send_message`, `credentials`, `system_config`,
`input_control`, `capture`, `elevate`, `remote`.

## Trwałość zadań

Zadanie to wiersz w SQLite plus dziennik kroków:

- `tasks` — cel, kryteria, stan, plan, raport, znaczniki czasu.
- `task_steps` — `(task_id, step_key)` → stan i wynik. **To** jest mechanizm
  wznawiania: przed wykonaniem czegokolwiek pętla czyta klucze ukończonych kroków
  i je pomija.
- `approvals` — zgody jako wiersze, nie obietnice w pamięci. Dlatego „tak" może
  przyjść po restarcie, z telefonu, następnego dnia.

Po starcie `TaskSupervisor.recover()` zbiera zadania zostawione w stanie RUNNING
(nie zatrzymały się z własnej woli) i uruchamia je ponownie. Zadania BLOCKED
zostawia w spokoju — czekają słusznie, ponowne pytanie byłoby hałasem.

## Współbieżność bez kolizji

Zadania są domyślnie równoprawne i biegną równolegle (`tasks.max_parallel`).
Konflikt rozwiązują dzierżawy zasobów o kluczach `file:…`, `app:…`, `device:…`,
`host:…`, `service:…`, `registry:…`:

- odczyty współdzielone, zapisy wyłączne,
- klucze pobierane w kolejności posortowanej → brak zakleszczeń przy `{A,B}` vs `{B,A}`,
- ten sam właściciel może wejść ponownie (akcja zagnieżdżona nie blokuje sama siebie),
- dzierżawy **nie** są utrwalane: opisują żywą rywalizację, a po restarcie nic nie
  działa, więc stara dzierżawa byłaby tylko kłamstwem blokującym odzysk.

## Wybór modelu

Wołający deklaruje `Need(job, privacy, requires, max_cost, prefer_speed)`.
Router punktuje każdy model każdego **sprawnego** dostawcy: jakość × szybkość ×
koszt × prywatność, z profilem zależnym od ustawienia prywatności i twardym
priorytetem jakości dla `plan`, `reason`, `code`. Po wyczerpaniu budżetu zostają
tylko modele darmowe (lokalne). Awaria dostawcy → następny w rankingu, cicho.

Nazwy modeli i ceny żyją w plikach dostawców. Zmiana cennika to jeden plik.

**„Sprawny" jest pomiarem, nie założeniem.** `models/health.py` sprawdza każdego
dostawcę najtańszym możliwym wywołaniem (listing modeli — nie kosztuje nic) przy
zapisie klucza, przy starcie i co godzinę, a wynik trzyma w cache, żeby żadne
zadanie za to nie płaciło. Siedem statusów i zdanie dla użytkownika opisuje
`docs/STATE_MACHINES.md` §3. Wcześniej „dostępny" znaczyło „klucz jest w sejfie",
przez co całkiem nieprawidłowy klucz raportował się jako gotowy, a pierwsze
prawdziwe zadanie padało na 401 w środku pracy.

Klasyfikacja odpowiedzi jest **jedna** dla wszystkich dostawców
(`classify_response`); adapter podaje wyłącznie adres do odpytania. Dzięki temu
nowy dostawca nie może wprowadzić własnego rozumienia „zły klucz".

## Konfiguracja na żywo

Ustawienia i poświadczenia zmieniają się w trakcie działania, więc silnik nie
może czytać ich raz przy starcie.

- `SettingsService` (`settings.py`) jest **jedynym pisarzem** ustawień:
  waliduje na kopii (żądanie w połowie błędne jest odrzucane w całości), zapisuje,
  ogłasza `config.changed` ze ścieżkami, które faktycznie się ruszyły, i pilnuje
  pliku (odpytywanie `stat` co dwie sekundy — plik edytowany ręcznie też ma
  zadziałać, a zależność do obserwacji katalogu nie jest tego warta).
- `Config.adopt()` podmienia **wartości w miejscu**, nie obiekt. `PolicyEngine`
  trzyma `config.autonomy`, router `config.models`, bramka powiadomień
  `config.notifications` — podmiana korzenia zostawiłaby ich wszystkich przy
  konfiguracji, której nikt już nie edytuje.
- `Vault.set()` ogłasza `vault.changed` (samą nazwę, nigdy wartość);
  `ProviderPool` (`models/pool.py`) przebudowuje na to dostawców i sprawdza
  tych, których odcisk konfiguracji się zmienił.

Ścieżki zmiany zbiegają się w jednej idempotentnej metodzie `rebuild()`, więc
zdarzenie i jawne wywołanie z API mogą przyjść oba i nic się nie dubluje.

## Pamięć i sejf

Pamięć jest lokalna, szyfrowana, widoczna i edytowalna. Treść, temat i tagi
siedzą w jednej kopercie AES-GCM; jawne zostają tylko metadane (rodzaj, zakres,
czasy). Skradziony plik bazy pokazuje **ile** GARIS pamięta i **kiedy**, nigdy
**co**. Wyszukiwanie odszyfrowuje w trakcie skanowania — przy skali pamięci
użytkownika to mikrosekundy i lepsza cena niż jawny indeks.

Poświadczenia nigdy nie są pamięcią. Detektor wzorców odrzuca je z `memory` i
kieruje do sejfu: osobny plik, osobny klucz wyprowadzony z klucza głównego
(wyciek klucza pamięci nie otwiera sejfu), szyfrogram wiązany z nazwą wpisu.
Narzędzia dostają referencję `vault://nazwa`; wartość podstawia runtime
bezpośrednio przed wywołaniem handlera i nigdy nie trafia do promptu, planu,
zdarzenia ani audytu.

## Klucz główny

Windows: klucz owinięty DPAPI (per użytkownik, per maszyna) — plik skradziony na
inny komputer jest bezużyteczny. POSIX: plik 0600 w katalogu 0700. Opcjonalnie
hasło (scrypt) na dowolnej platformie — to jest „zablokuj GARIS-a".

## Dwa agenty

GARIS Desktop i GARIS Server mają **ten sam** runtime i ten sam model
bezpieczeństwa; różnią się zestawem narzędzi (`tools.SERVER_MODULES` nie zawiera
sterowania ekranem i myszą). Desktop przekazuje serwerowi **cel**, nie listę
komend — więc serwer może wybrać inną metodę niż wyobraził sobie desktop, i ma
własny plan, własną weryfikację i własny audyt.

## Rozszerzanie

Nowa zdolność = jedna funkcja z dekoratorem `@registry.tool(...)`. Deklarujesz
nazwę, opis, schemat parametrów, efekty, platformy i klucze zasobów — i tyle:
planer natychmiast ją widzi, polityka ją pilnuje, audyt ją zapisuje. Szczegóły
w `docs/TOOLS.md`.
