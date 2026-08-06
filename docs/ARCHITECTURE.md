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
│  capabilities/  zdolności z własnym weryfikatorem i dowodami  │
├──────────────────────────────────────────────────────────────┤
│  tools/         narzędzia (56) rejestrowane w runtime         │
├──────────────────────────────────────────────────────────────┤
│  models/        router + dostawcy (OpenAI/Claude/Gemini/…)    │
├──────────────────────────────────────────────────────────────┤
│  runtime/       JEDYNA koperta wykonania: CapabilityRunner,   │
│                 polityka, zgody, dzierżawy, audyt             │
├──────────────────────────────────────────────────────────────┤
│  kernel/        słownik wspólny wszystkim: Effect, Risk,      │
│                 Permission, Failure, Verification, efekty,    │
│                 outbox. Nie importuje niczego poza errors     │
├──────────────────────────────────────────────────────────────┤
│  stan:          memory (szyfrowana) · vault (osobno)          │
├──────────────────────────────────────────────────────────────┤
│  infrastruktura: paths · config · crypto · store · net · events│
└──────────────────────────────────────────────────────────────┘
```

`kernel/` powstał, bo dwie warstwy — narzędzia i zdolności — musiały mówić o tych
samych rzeczach (skutek, ryzyko, uprawnienie, werdykt), a żadna nie może
importować drugiej. Słowo, którego potrzebują obie, nie należy do żadnej z nich.

`capabilities/` różni się od `tools/` jedną rzeczą: zdolność deklaruje, **co
znaczy jej sukces**, i nie może zostać zarejestrowana bez weryfikatora. Narzędzie
mówi, czym jest; zdolność mówi, kiedy się udała.

## Droga jednej akcji

Wszystko, co GARIS robi, przechodzi przez `CapabilityRunner` — jedną kopertę,
raz. Narzędzie i zdolność różnią się wyłącznie wykonawcą na dziesiątym kroku:

```
Runtime.perform  →  adapter zgodności  →  CapabilityRunner
                                             ├→ LegacyToolExecutor      → handler narzędzia
                                             └→ NativeCapabilityExecutor → zdolność
```

Kolejność jest ustalona i żaden z powodów nie jest kwestią stylu:

```
 1  ustal cel                    9  odmów powtórzenia efektu niepewnego
 2  sprawdź argumenty           10  wykonaj — dokładnie jednym wykonawcą
 3  sprawdź przerwanie          11  zbierz ustrukturyzowany wynik
 4  oceń politykę               12  zbuduj dowody
 5  poproś o zgodę              13  uruchom niezależną weryfikację
 6  wylicz effect_id            14  zapisz rozliczenie + dowody + zdarzenie
 6b weź dzierżawy                   w jednej transakcji
 7  zarezerwuj efekt            15  wypuść to, co się zapisało
 8  odtwórz efekt już wykonany  16  zwróć ustrukturyzowany wynik
```

**Polityka i zgoda stoją przed rezerwacją efektu**, bo odmowa nie ma prawa
zostawić śladu w świecie. **Zapis stoi przed zdarzeniem**, bo ogłoszenie czegoś,
co się nie zapisało, to ta sama rodzina nieprawdy co atrapa mówiąca
„sprawdzone". Nic nie jest sukcesem, zanim jego dowód i werdykt się nie
zatwierdzą.

`Runtime.perform()` została **fasadą zgodności**: buduje cel, woła runner raz i
tłumaczy odpowiedź na `ActionResult` albo na wyjątek, który wołający łapią od
0.1.0. Sama nie robi polityki, zgód, dzierżaw, audytu, trwałości ani zdarzeń —
pilnuje tego test czytający jej źródło, bo gwarancja jest strukturalna: w chwili,
w której `perform` znów zacznie decydować, drogi są dwie, niezależnie od tego,
czy któryś test to zauważy.

Dwie różne rzeczy dzieją się z błędami, i to jest celowe:

- **Awaria narzędzia to wartość** (`ActionResult.ok == False`). Pętla agenta ma ją
  obsłużyć: ponowić, zmienić narzędzie, przeplanować. Użytkownik nic nie słyszy.
- **Brak zgody i twarda odmowa to wyjątki.** Muszą przerwać przepływ, bo zadanie
  ma się zatrzymać i czekać na człowieka.

Narzędzie wołające inne narzędzie robi to przez `ctx.perform()`, które wraca do
punktu 1 — rekurencja przez tę samą kopertę, nie obejście. Dziecko dostaje własny
klucz kroku (nigdy klucza rodzica: efekty by się zderzyły), własną decyzję
polityki, własny efekt, własny wpis audytu i własną weryfikację. Zagnieżdżenie
jest ograniczone. Nie ma drzwi na skróty.

## Pięć pytań, których nie wolno ze sobą mylić

Najważniejsza rzecz w całym silniku i jedyna, z której wynika reszta reguł
bezpieczeństwa. Każde pytanie ma własne pole i własne miejsce odpowiedzi:

| Pytanie | Odpowiada |
|---|---|
| Czy kod się wykonał? | `Outcome.ok` |
| Czy świat się zmienił? | `EffectDisposition` |
| Czy cel został osiągnięty? | `Verification.goal_met` |
| Czy ktoś to sprawdził? | `Verification.checked` |
| Czy znamy wynik? | `not Verification.uncertain` |

Sklejenie którychkolwiek dwóch to dokładnie ta awaria, od której zaczął się ten
projekt: zadanie odczytało prawdziwe liczby z dysku i zostało ogłoszone jako
„sprawdzone" przez atrapę, która nigdy na nie nie spojrzała. Jedno pole `success`
udające odpowiedź na wszystkie pięć naraz jest tym samym błędem napisanym
krócej.

Stąd jedna definicja słowa „zweryfikowane", w jednym miejscu:

```
report.verified == goal_met and checked and not uncertain
```

Żadna trasa — API, przejście stanu zadania, fasada zgodności, odtworzenie efektu
— nie ma prawa wyprowadzić sukcesu z samego `Outcome.ok`.

## Efekt zdarza się raz

Idempotencja mieszkała kiedyś w słowniku w pamięci. To przeżywa ponowienie i nic
więcej: awaria między uruchomieniem programu a zapisaniem tego faktu gubi zapis,
a wznowione zadanie uruchamia program drugi raz.

Efekt jest więc **rezerwowany przed działaniem**, w bazie, pod kluczem:

- praca zmieniająca świat dostaje **deterministyczny** `effect_id` z zadania,
  kroku, celu i skrótu argumentów — zadanie wznowione po awarii trafia we własną
  rezerwację zamiast instalować, płacić czy wysyłać drugi raz;
- **odczyt dostaje świeży identyfikator za każdym razem**. Odtworzenie pomiaru
  oddaje stan dysku sprzed dziesięciu minut podany jako stan bieżący; zmierzenie
  ponownie nic nie kosztuje i jest jedyną uczciwą odpowiedzią;
- to, czy coś jest efektowne, wynika z **zadeklarowanych metadanych**, nigdy z
  nazwy ani z argumentów.

`EffectState` mówi, jak wiersz został rozliczony. `EffectDisposition` mówi, co
stało się poza GARIS-em, i **tylko ono** może pozwolić na ponowienie:

| Dyspozycja | Znaczenie | Wolno ponowić |
|---|---|---|
| `not_started` | wykonawca nie został wywołany | tak |
| `not_applied` | wykonawca dowiódł, że nic nie zmienił | tak, jako kolejna próba |
| `applied` | efekt zaszedł albo jego stan zaobserwowano | nie |
| `unknown` | mógł zmienić i nikt nie wie | nie |

„Nie udało się" nie jest dowodem, że nic się nie stało. Wykonawca, który wysłał
wiadomość i padł na odczycie potwierdzenia, zawiódł **i** zadziałał; ten, który
nie otworzył gniazda, zawiódł i nie zadziałał. Rzucają identycznie. Runner
zapisuje więc, czy granica wywołania została przekroczona — jako fakt ustawiany
tuż przed wywołaniem, nie odgadywany po typie wyjątku, czasie ani braku wyniku.
`not_applied` pochodzi wyłącznie z własnej, ustrukturyzowanej deklaracji
wykonawcy; treść komunikatu błędu nie jest dowodem na nic.

`applied` obejmuje też sprawdzoną porażkę celu: proszono o 30%, zmierzono 33%.
Efekt zaszedł — po prostu nie był tym, czego chciano. Taki wynik zostaje
zapisany z **całym** werdyktem i przy powtórnym wywołaniu jest odtwarzany jako ta
sama zmierzona porażka. `EffectState.DONE` znaczy „rozliczone zgodnie z prawdą",
nie „użytkownik dostał, o co prosił".

## Profile uruchomieniowe

`include_fake=True` było kiedyś całą historią i było zwykłym argumentem, który
mógł podać ktokolwiek. Atrapa dostawcy odpowiada na każdy prompt planera
zgadywanką ze słów kluczowych, a na każdy prompt weryfikacji `{"ok": true}` —
proces, który ją po cichu zdobędzie, przestaje umieć mówić prawdę o własnej
pracy.

Profil jest więc wybierany jawnie, a PRODUCTION jest **sprawdzany, nie
zakładany**, po tożsamościach a nie po liczbie: proces z atrapą dostawcy albo ze
zdolnością testową odmawia startu. Żadna zmienna środowiskowa nie wybiera
profilu — zmienna, która zamienia PRODUCTION w FIXTURE, zamienia prawdziwą
maszynę w udającą.

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

Nowe narzędzie = jedna funkcja z dekoratorem `@registry.tool(...)`. Deklarujesz
nazwę, opis, schemat parametrów, efekty, platformy i klucze zasobów — i tyle:
planer natychmiast je widzi, polityka go pilnuje, audyt go zapisuje. `ToolSpec`
jest publicznym kontraktem rozszerzeń i pozostaje nim. Szczegóły w
`docs/TOOLS.md`.

`Capability` to nowsza forma tej samej rzeczy, z jedną różnicą: **nie da się jej
zarejestrować bez weryfikatora**. Nie ma wartości domyślnej — zdolność, której
warunku końcowego nikt nie napisał, musi powiedzieć to wprost przez
`always_unchecked`, i wszystko, co produkuje, jest wtedy raportowane jako
skończone, ale niesprawdzone. Uczynienie weryfikatora opcjonalnym jest dokładnie
tym, jak „nikt tego nie sprawdził" staje się „sprawdzone" przez przemilczenie.

Zdolność, która zmienia świat, deklaruje też swoje **skutki** wprost.
`Permission` ich nie opisuje i nigdy nie zastąpi: `FILES` obejmuje odczyt nazwy i
trwałe skasowanie katalogu, `PROCESS` obejmuje listowanie i zabicie procesu.
Wyprowadzenie jednego z drugiego zakłada bramkę odczytu na kasowaniu.

Klient MCP i wtyczki zewnętrzne **nie istnieją** — patrz `docs/ROADMAP.md`,
sekcja „Wizja 1.0 a rzeczywistość".
