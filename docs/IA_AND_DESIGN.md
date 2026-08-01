# Architektura informacji i system wizualny

## Mapa ekranów

Siedem sekcji. Diagnostyka nie konkuruje z nimi wizualnie — jest na dole, ciszej.

```
Start          stan GARIS-a · bieżące zadanie · decyzja do podjęcia ·
               ostatni wynik · pole polecenia · głos · maks. 3 sugestie
Rozmowa        historia z rozróżnionymi typami wpisów
Zadania        lista + szczegóły
Pamięć         co pamiętam, dlaczego, edycja, usuwanie
Urządzenia     ten komputer, serwery, telefon
Subskrypcje    zainteresowania i źródła
Ustawienia     12 kategorii
─────────────
Diagnostyka    (stopka nawigacji, nie równorzędna pozycja)
```

Tryb developerski dokłada: Modele · Narzędzia · Runtime · Audyt · Logi ·
Polityki · Kolejka · Eksperymenty.

### Ustawienia — dwanaście kategorii

Ogólne · Wygląd · Głos · Modele · Pamięć i prywatność · Powiadomienia ·
Automatyzacje · Urządzenia · Integracje · Bezpieczeństwo · Zaawansowane ·
Diagnostyka.

Każde widoczne ustawienie **działa albo jest wyraźnie oznaczone jako
niedostępne**. Dziś przełączniki głosu są widoczne, przełączalne i martwe — to
znika w pierwszej kolejności.

### Start — czego tam nie ma

Nie ma siatki kart. Nie ma statystyk. Kula nie zajmuje połowy okna nie mówiąc
nic — jej rozmiar jest uzasadniony tylko wtedy, gdy komunikuje stan.

Stany, które kula musi rozróżniać wizualnie **i nie tylko kolorem**:
spoczynek · słucham · myślę · planuję · wykonuję · mówię · czekam · sukces ·
ostrzeżenie · błąd · tryb cichy · tryb gry.

### Rozmowa — nie klon dymków czatu

Osobne typy wpisów, wizualnie rozróżnialne: wypowiedź · utworzenie zadania ·
zgoda · postęp · wynik · błąd · załącznik · komunikat systemowy.

Gdy wiadomość utworzyła zadanie, widać to i można wejść w szczegóły **bez
opuszczania rozmowy**. Przy każdym wpisie: ponów · popraw i wyślij ·
kopiuj · zatrzymaj · pokaż wynik · pokaż użyte narzędzia.

### Zadanie — co widać

Tytuł · cel · stan · urządzenie · postęp **tylko gdy mierzalny** · czego
potrzebuję od Ciebie · kroki zrobione · krok bieżący · weryfikacja · wynik ·
anuluj · wstrzymaj · wznów · szczegóły.

Zakazane: zmyślone procenty, szacowany czas bez podstaw, „w toku" bez treści.

---

## System Liquid Glass

Dziś jest jedna klasa `.glass` i dwa warianty. Za mało — wszystko wygląda tak
samo, więc nic nie wygląda na ważne.

### Osiem materiałów

| Materiał | Rola | Rozmycie | Wypełnienie | Cień |
|---|---|---|---|---|
| `atmosphere` | tło aplikacji | — | powolne kolorowe światło + ziarno | — |
| `nav` | nawigacja | średnie | niskie | osadzający |
| `workspace` | główny panel | duże | najniższe | osadzający + szeroki |
| `card` | karta w panelu | małe | niskie | ciasny |
| `input` | pola, wgłębienia | brak | ciemniejsze | wewnętrzny |
| `modal` | okna modalne | największe | wyższe | mocny |
| `approval` | karta zgody | największe | najwyższe + akcent | najmocniejszy |
| `diagnostic` | tryb dev | małe | neutralne, bez barwy | minimalny |

Zasada, która dziś jest łamana: **karta nie może rozmywać tak mocno jak panel,
na którym leży** — wygląda wtedy jak dziura w nim, nie jak coś na nim.

### Czego używamy

Warstwowa przezroczystość · subtelna refrakcja na krawędziach · światło od
góry-lewej · adaptacyjne rozmycie zależne od warstwy · wewnętrzne rozjaśnienie ·
drobne ziarno przeciw bandingowi · powściągliwe cienie · reakcja na kursor
**tylko** na kartach zgody i elementach nawigacji.

### Czego nie używamy

Ciężkich szarych prostokątów · jednakowego rozmycia wszędzie · nadmiaru cyjanu
i fioletu · tekstu o niskim kontraście · neonu · szumu wizualnego.

### Warunek twardy

Interfejs musi wyglądać dobrze przy `--glass-strength: 0` i `data-glass="off"`.
Jeśli po wyłączeniu efektów robi się brzydki, to znaczy, że efekty zastępowały
projekt.

### Tokeny

Wszystko przez tokeny semantyczne (`--surface-workspace`, `--ink-primary`),
nigdy przez wartości rozsiane po plikach. Motyw, akcent i intensywność zmieniają
tokeny, nie reguły.

---

## Personalizacja wyglądu

Motyw (system / ciemny / jasny) · kolor akcentu + własny · intensywność szkła ·
przezroczystość · gęstość · skala interfejsu · wielkość tekstu · intensywność
animacji · redukcja ruchu · jakość kuli · zachowanie kuli w spoczynku · tryb
nawigacji · kompaktowy sidebar · automatyczny sidebar · dźwięki · efekty tła ·
wysoki kontrast · wskaźniki bezpieczne dla daltonistów.

---

## Reguły responsywności

| Szerokość | Układ |
|---|---|
| < 1024 | sidebar zwinięty do ikon, kula mniejsza |
| 1024–1279 | sidebar ikonowy, panel pełny |
| 1280–1439 | sidebar pełny |
| 1440–1919 | sidebar pełny, szerszy panel |
| ≥ 1920 | panel z maksymalną szerokością treści, marginesy rosną |
| ultrawide | treść wyśrodkowana, nie rozciągnięta |

Minimum okna: **1024×700** (dziś 720×520 — do podniesienia).
Skalowanie Windows 100 / 125 / 150 / 175% — niesprawdzone, wymaga Windows.

Twarde wymagania: brak ucinania · brak nakładania · brak niedostępnych
przycisków · brak paska przewijania całego okna · przewijanie tylko wewnątrz
obszarów, które tego wymagają · pole polecenia zawsze widoczne · kula skaluje
się proporcjonalnie · tekst zawija się poprawnie (długi polski i angielski) ·
puste stany są użyteczne, nie puste.

---

## Lista kontrolna dostępności

Dziś spełniony jest jeden punkt: `prefers-reduced-motion`.

- [ ] pełna nawigacja klawiaturą, logiczna kolejność tabulacji
- [ ] widoczny fokus na każdym elemencie interaktywnym
- [ ] etykiety ARIA na kontrolkach bez tekstu (kula, przełączniki, ikony)
- [ ] dialogi: pułapka fokusa, `Esc`, powrót fokusa po zamknięciu
- [ ] stan nigdy wyłącznie kolorem — kształt albo tekst też
- [ ] tryb wysokiego kontrastu
- [ ] skalowanie tekstu do 200% bez utraty funkcji
- [ ] minimalny rozmiar celu 24×24 px
- [x] `prefers-reduced-motion` respektowane
- [ ] komunikaty stanu ogłaszane czytnikowi (`aria-live`)

---

## Budżety wydajności

Nic z tego nie jest dziś mierzone. Wartości to cele do weryfikacji na Windows.

| Miara | Budżet |
|---|---|
| Start okna do widocznego interfejsu | < 1,5 s |
| Start silnika do handshake | < 3 s |
| Pamięć w spoczynku (powłoka + silnik) | < 350 MB |
| CPU w spoczynku | < 1% |
| GPU w spoczynku, okno widoczne | < 5% |
| Klatki animacji | 60 fps |
| Opóźnienie API lokalnie | < 50 ms |
| Instalator | < 60 MB |

**Redukcja efektów obowiązkowa przy:** zminimalizowaniu · ukryciu do zasobnika ·
trybie gry · oszczędzaniu baterii · pulpicie zdalnym · redukcji ruchu.

Kula WebGL nie może renderować, gdy okno jest niewidoczne. Dziś renderuje.
