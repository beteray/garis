# Maszyny stanów

Trzy, wspólne dla silnika i interfejsu. Każdy stan musi dać się zamienić na
jedno zdanie po polsku — jeśli się nie da, stan jest źle nazwany.

---

## 1. Zadanie

Dzisiaj sześć stanów, z czego `blocked` znaczy trzy różne rzeczy. Docelowo:

```
                    ┌─────────────┐
   przyjęte ──────► klasyfikuję ──┴─► (nie zadanie → rozmowa, koniec)
                          │
                          ▼
                     planuję ◄──────────────┐
                          │                 │
          ┌───────────────┼─────────────┐   │
          ▼               ▼             ▼   │
   czekam na zasób  czekam na Ciebie  czekam na zgodę
          │               │             │   │
          └───────────────┴─────────────┘   │
                          ▼                 │
                      wykonuję              │
                          │                 │
                          ▼                 │
                     weryfikuję             │
                     │        │             │
                     │        └─► odzyskuję ┘
                     ▼
     ┌───────────┬───────────────┬─────────┐
  ukończone  częściowo      anulowane   nieudane
```

Plus `wstrzymane`, osiągalne z każdego stanu nieterminalnego i wracające do
niego.

| Stan | Zdanie dla użytkownika |
|---|---|
| `received` | Przyjąłem. |
| `classifying` | Sprawdzam, czy to zadanie. |
| `planning` | Zastanawiam się, jak to zrobić. |
| `waiting_resource` | Czekam, aż zwolni się *(plik/urządzenie)*. |
| `waiting_input` | **Konkretne pytanie.** |
| `waiting_approval` | **Konkretna operacja do zatwierdzenia.** |
| `executing` | Robię: *(bieżący krok)*. |
| `verifying` | Sprawdzam, czy wyszło. |
| `recovering` | Nie wyszło za pierwszym razem, próbuję inaczej. |
| `paused` | Wstrzymane przez Ciebie. |
| `completed` | Gotowe. |
| `partial` | Zrobione częściowo — *(czego brakuje)*. |
| `cancelled` | Zatrzymane. |
| `failed` | Nie udało się — *(powód)*. |

**Nigdy nie wolno wyświetlić samego „czekam na Ciebie".** Stan `waiting_input`
bez wypełnionego pytania jest błędem programu, nie stanem produktu.

Każde przejście zapisuje: `from`, `to`, `reason`, `at`, `actor`
(`user | agent | system | timeout`), `user_message`, `diagnostic`.

**Timeouty.** Każdy stan czekający ma limit. Po jego upływie zadanie nie umiera —
przechodzi w `partial` albo `failed` z powodem `timeout`, i mówi o tym. Zadanie
bez ruchu przez dobę jest zgłaszane w briefingu, nie porzucane po cichu.

**Przejścia niemożliwe** są odrzucane przez magazyn, nie przez wywołującego.

---

## 2. Połączenie okna z silnikiem

Zaimplementowane w `apps/desktop/src/lib/api.ts` (typ `Link`). Sprawdzone w
przeglądarce; ścieżka Tauri nieuruchomiona.

| Stan | Znaczenie | Akcja użytkownika |
|---|---|---|
| `starting` | powłoka uruchamia silnik | — |
| `handshake` | silnik żyje, czekam na adres i token | — |
| `connected` | działa | — |
| `engine-failed` | powłoka nie zdołała uruchomić silnika | uruchom ponownie, zobacz log |
| `bad-token` | silnik odrzucił token | uruchom silnik ponownie |
| `no-response` | brak odpowiedzi pod adresem | ponów, sprawdź port |
| `retrying` | zerwany socket, wracam | — |

Każdy stan nieterminalny ma timeout. „Łączę się…" bez końca jest zakazane.

Do dodania: **`port-taken`** — dziś zajęty port 8756 daje `no-response` bez
wskazówki.

---

## 3. Dostawca modeli — **zaimplementowane** (`models/health.py`)

Było: `available: true/false`, wyprowadzone z obecności klucza w sejfie.
Zmierzone: całkowicie nieprawidłowy klucz raportował `available: true`.

Jest: **siedem statusów silnika**, mierzonych, nie zakładanych.

```
                        ┌──► ONLINE          router używa
                        ├──► INVALID_KEY     klucz odrzucony
brak sprawdzenia        ├──► INVALID_CONFIG  brak klucza / zły adres / wyłączony
   UNKNOWN ──► check ───┼──► RATE_LIMIT      limit albo brak środków
   (używalny)           ├──► TIMEOUT         jest, ale nie zdążył
                        └──► OFFLINE         nie odpowiada
```

| Status | Zdanie dla użytkownika | Czy router go użyje |
|---|---|---|
| `online` | „Gotowy." | tak |
| `unknown` | „Jeszcze nie sprawdzałem." | **tak** |
| `invalid_config` | „Brak klucza." / „Zły adres dostawcy." | nie |
| `invalid_key` | „Klucz odrzucony przez dostawcę." | nie |
| `rate_limit` | „Limit u dostawcy wyczerpany." / „Brak środków na koncie u dostawcy." | nie, do `retry_after` |
| `timeout` | „Dostawca nie odpowiedział na czas." | nie |
| `offline` | „Dostawca nie odpowiada." | nie |

**Dlaczego `UNKNOWN` jest używalny.** „Nie sprawdzałem" to nie to samo co
„zepsute". Gdyby brak pomiaru blokował, komputer wstający bez sieci nie miałby
agenta w ogóle. Wina blokuje; brak dowodu nie.

**Dziewięć stanów produktowych, siedem statusów silnika.** Interfejs pokazuje
więcej niż silnik rozróżnia, bo część stanów to ta sama sytuacja z innym
powodem. Mapowanie jest jednoznaczne i to ono jest kontraktem UI:

| Stan produktowy | `status` + `reason` |
|---|---|
| brak klucza | `invalid_config` + „Brak klucza." |
| wyłączony przez Ciebie | `invalid_config` (dostawca nie jest w ogóle budowany) |
| sprawdzam… | `unknown`, dopóki nie wróci pierwszy check |
| gotowy | `online` |
| klucz nieprawidłowy | `invalid_key` |
| brak środków | `rate_limit` + „Brak środków na koncie u dostawcy.", `retry_after = 0` |
| limit wyczerpany | `rate_limit` + `retry_after` z nagłówka `Retry-After` |
| dostawca nie odpowiada | `offline` |
| dostawca za wolny | `timeout` |

Rozróżnienie „limit wróci sam" od „brak środków nie wróci sam" niesie
`retry_after`: zero znaczy „nie odblokuje się samo, trzeba sprawdzić ponownie".

**Health check:** najtańsze możliwe wywołanie u dostawcy (`GET /models`,
`/api/tags` — listing, nie generowanie, więc nie kosztuje ani grosza) przy
zapisie klucza, przy starcie i co godzinę. Wynik jest cache'owany — nie
odpytujemy przed każdym zadaniem. Klasyfikacja odpowiedzi żyje **w jednym
miejscu** (`classify_response`); adapter dostawcy podaje wyłącznie adres, żeby
nowy dostawca nie mógł wymyślić własnego pojęcia „zły klucz".

**Awaria w trakcie pracy też jest pomiarem.** Router zgłasza każdą nieudaną
próbę do monitora (`note_failure`), więc klucz unieważniony w południe nie
wygląda na sprawny do najbliższego sprawdzenia o pełnej godzinie.

**Przeładowanie bez restartu** (naprawa P0-1): zapis do sejfu emituje
`vault.changed`, `ProviderPool` przebudowuje dostawców i sprawdza tych, których
konfiguracja faktycznie się ruszyła, a `/api/state` pokazuje nowy stan
natychmiast. `POST /api/vault` **czeka** na przebudowę zamiast zostawiać ją
pętli zdarzeń — następne żądanie może brzmieć „wykonaj zadanie" i musi zastać
nowy klucz.

**Kryteria wyboru modelu** (jakość × szybkość × koszt × prywatność): doszła
bieżąca dostępność i limity. Naprawiony zmierzony błąd: modele natywnego audio
(`gemini-2.5-flash-native-audio-preview`, `gpt-4o-realtime-preview`) deklarowały
`Job.CHAT` i wygrywały ranking na zwykłą pisaną rozmowę — sesja mowa-w-mowę to
inna modalność, nie tańszy czat. Zostaje do zrobienia: rozmiar kontekstu jako
kryterium miękkie, status `preview` i preferencja użytkownika.

---

## 4. Klasyfikator intencji — nowy

Dziś nie istnieje; wszystko staje się zadaniem. Zmierzone: „witaj" zostaje
wiecznym zadaniem `blocked`.

| Klasa | Co powstaje |
|---|---|
| rozmowa | odpowiedź, **żadnego zadania** |
| pytanie faktograficzne | odpowiedź, zadanie tylko gdy trzeba szukać |
| polecenie | zadanie |
| zadanie długie | zadanie ze śledzeniem |
| automatyzacja | reguła + zadanie przy wyzwoleniu |
| przypomnienie | wpis w harmonogramie |
| subskrypcja | źródło + filtr |
| odpowiedź na pytanie GARIS-a | wznowienie istniejącego zadania |
| zatwierdzenie | rozstrzygnięcie zgody |
| zgłoszenie błędu | wpis diagnostyczny |

Klasyfikacja działa **bez modelu chmurowego** dla przypadków oczywistych
(powitania, podziękowania, jednosłowne potwierdzenia) — inaczej brak klucza
znów zamienia „cześć" w zadanie.

Zasada: **zadanie powstaje tylko wtedy, gdy jest co śledzić.**

---

## 5. Taksonomia błędów — nowa

Dziś: odzyskiwalny / blokujący. Za mało, żeby powiedzieć człowiekowi, co robić.

| Klasa | Przykład | Rekomendowana akcja |
|---|---|---|
| `configuration` | brak klucza | otwórz ustawienia modeli |
| `authentication` | klucz odrzucony | popraw klucz |
| `provider` | 429, brak środków | poczekaj / zmień dostawcę |
| `connectivity` | brak sieci | ponów |
| `tool` | narzędzie zwróciło błąd | GARIS próbuje inaczej |
| `permission` | brak uprawnień administratora | uruchom z podniesieniem |
| `runtime` | silnik nie odpowiada | uruchom silnik ponownie |
| `packaging` | brak sidecara | przeinstaluj |
| `internal` | błąd programu | zgłoś, skopiuj diagnostykę |
| `cancelled` | użytkownik przerwał | — |

Każdy błąd niesie: zdanie po polsku, skutek teraz, co już zostało spróbowane,
jedną rekomendowaną akcję, możliwość ponowienia i szczegóły techniczne
w trybie developerskim.
