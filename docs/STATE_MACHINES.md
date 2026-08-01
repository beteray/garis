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

## 3. Dostawca modeli

Dziś: `available: true/false`, wyprowadzone z obecności klucza w sejfie.
Zmierzone: całkowicie nieprawidłowy klucz raportuje `available: true`.

Docelowo:

```
brak klucza ──► klucz zapisany ──► sprawdzam ──┬──► skonfigurowany
                                               ├──► klucz nieprawidłowy
                                               ├──► brak środków
                                               ├──► limit wyczerpany
                                               └──► niedostępny
```

| Stan | Co widzi użytkownik | Czy router go użyje |
|---|---|---|
| `no_key` | „Brak klucza" | nie |
| `key_stored` | „Sprawdzam…" | nie |
| `validating` | „Sprawdzam…" | nie |
| `configured` | „Gotowy" | tak |
| `invalid_key` | „Klucz odrzucony przez dostawcę" | nie |
| `no_credit` | „Brak środków na koncie" | nie |
| `rate_limited` | „Limit wyczerpany, wróci o *(godzina)*" | nie, do czasu |
| `unavailable` | „Dostawca nie odpowiada" | nie, ponawiam |
| `disabled` | „Wyłączony przez Ciebie" | nie |

**Health check:** najtańsze możliwe wywołanie u dostawcy przy zapisie klucza,
przy starcie i co godzinę. Wynik jest cache'owany — nie odpytujemy przed każdym
zadaniem.

**Przeładowanie bez restartu.** To jest naprawa P0-1: zapis do sejfu emituje
zdarzenie, router przebudowuje dostawców, `/api/state` natychmiast pokazuje
nowy stan. Dziś wymaga restartu i nikt o tym nie mówi.

**Kryteria wyboru modelu** (dziś: jakość × szybkość × koszt × prywatność):
dochodzi rozmiar kontekstu, wsparcie narzędzi, **modalność**, status `preview`,
bieżąca dostępność, limity i preferencja użytkownika. Zmierzony błąd:
`gemini-2.5-flash-native-audio-preview` wygrywa ranking na czat tekstowy.

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
