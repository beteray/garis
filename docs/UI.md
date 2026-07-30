# GARIS — interfejs (etap 2)

To jest kontrakt wizualny, nie luźna inspiracja. Aplikacja ma wyglądać i reagować
tak, jakby była częścią systemu, a nie panelem administracyjnym. Estetyka: Apple
i **Liquid Glass**.

Twarda zasada nadrzędna: **wszystko, co się zmienia, zmienia się z animacją.**
Nie ma przeskoków stanu, nie ma pojawiających się natychmiast elementów, nie ma
zmiany liczby bez przejścia. Jeśli coś zmienia pozycję, rozmiar, przezroczystość,
kolor albo treść — jest animowane.

## Stos

| Warstwa | Wybór |
|---|---|
| Powłoka | Tauri 2 (Rust) — tray, autostart, natywne efekty okna, lekki proces |
| UI | React + TypeScript + Vite |
| Animacja | **framer-motion** — domyślny sposób animowania. Wszystko, co potrzebuje ruchu, dostaje `motion.*` |
| Kula GARIS-a | WebGL (three.js / react-three-fiber) + shader; fallback Canvas 2D |
| Stan | Zustand + strumień zdarzeń z rdzenia po WebSocket |
| Styl | CSS Modules + zmienne CSS, bez frameworka utility |

Rdzeń (Python) nie wie nic o UI. UI konsumuje zdarzenia z `garis.events`
(`agent.state`, `task.*`, `approval.*`, `voice.state`) i woła lokalne API.

## Liquid Glass — jak to zrobić dobrze

Szkło to nie „półprzezroczyste tło". To cztery rzeczy naraz:

1. **Rozmycie tła** — `backdrop-filter: blur(40px) saturate(180%)`. Na Windows 11
   dodatkowo natywna Mica/Acrylic dla okna (`window-vibrancy` w Tauri), żeby
   rozmycie brało realny pulpit, nie tylko wnętrze aplikacji.
2. **Warstwa światła** — subtelny gradient na krawędzi górnej (`inset 0 1px 0
   rgba(255,255,255,.18)`), imitujący załamanie światła na szkle.
3. **Głębia przez cień, nie przez obramowanie** — `box-shadow: 0 8px 32px
   rgba(0,0,0,.28)`; obramowanie maksymalnie `1px` i prawie niewidoczne.
4. **Reakcja na ruch** — szkło żyje. Panel pod kursorem delikatnie zmienia
   gradient (`--mx`, `--my` ustawiane z `pointermove`), przycisk unosi się o 1–2 px.

Zakazane: pełne czarne tła, ostre prostokąty bez promienia, ramki 2 px, cienie
bez rozmycia, kolor bez przezroczystości. Promień narożników: 20–28 px dla
paneli, 12–14 px dla elementów wewnątrz.

Tokeny (`src/styles/tokens.css`):

```css
--glass-bg: rgba(255, 255, 255, 0.06);
--glass-bg-strong: rgba(255, 255, 255, 0.10);
--glass-stroke: rgba(255, 255, 255, 0.12);
--glass-blur: 40px;
--radius-panel: 24px;
--radius-control: 13px;
--shadow-panel: 0 8px 32px rgba(0, 0, 0, 0.28);
--ease-glass: cubic-bezier(0.32, 0.72, 0, 1);   /* iOS-owe wytracanie */
--dur-fast: 180ms;
--dur-base: 280ms;
--dur-slow: 520ms;
```

Motyw jasny i ciemny — oba pierwszej klasy, przejście między nimi animowane.

## Kula GARIS-a

Centralny element głównego ekranu. Jedna ciągła forma, która **nigdy nie stoi
w miejscu** — nawet w bezczynności oddycha. Przejścia między stanami są
animowane (morfowanie, nie podmiana):

| Stan | Wygląd i ruch |
|---|---|
| `idle` (oczekiwanie) | wolny oddech, skala 0.98↔1.02, ~4 s, chłodny błękit |
| `listening` (słuchanie) | pierścienie reagujące na amplitudę mikrofonu w czasie rzeczywistym, jaśniejsza krawędź |
| `thinking` (myślenie) | wewnętrzny wir, cząstki krążące po orbicie, fiolet |
| `working` (wykonywanie) | rytmiczny puls postępu, wolniejszy przy długich zadaniach, cyjan |
| `speaking` (mówienie) | fala wychodząca z centrum, zsynchronizowana z obwiednią audio |
| `error` (błąd) | jednorazowe drgnienie i ciepły odcień, wraca do `idle` po ~2 s |

Wymagania techniczne: 60 fps, brak zacięć przy 20 równoległych zadaniach,
degradacja do Canvas 2D bez WebGL, pauza renderu gdy okno niewidoczne
(oszczędność baterii — GARIS działa cały dzień w tle).

## Animacje, których nie wolno pominąć

- **Wejście okna** — panel wypływa i rozmywa się w ~320 ms, nie „mrugnięcie".
- **Zmiana ekranu** — wspólny layout (`layoutId` we framer-motion), elementy
  przenoszą się między widokami, nie znikają i pojawiają.
- **Lista zadań** — dodanie, usunięcie i zmiana kolejności animowane
  (`AnimatePresence`, `layout`). Nowe zadanie wsuwa się, ukończone wygasa.
- **Liczby i postęp** — animowane przejście wartości, nigdy skok.
- **Wiadomości w rozmowie** — wchodzą z lekkim przesunięciem i skalą; odpowiedź
  GARIS-a pisze się strumieniowo (tokeny z API), z animowanym kursorem.
- **Prośba o zgodę** — wsuwa się jako karta z wyraźnym, ale spokojnym akcentem;
  zatwierdzenie ma widoczną animację potwierdzenia.
- **Powiadomienia** — wjeżdżają, żyją, wyjeżdżają; nigdy nie migają.
- **Stan pusty** — ma własną ilustrację i delikatny ruch, nie surowy napis.
- **Hover i focus** — każdy klikalny element reaguje w ≤180 ms.
- **Ładowanie** — szkielety z animowanym połyskiem, nie spinner.

`prefers-reduced-motion`: ograniczamy ruch (przesunięcia, parallaksę, orbity) do
zmian przezroczystości. Kula przechodzi w spokojny, płynny gradient. To ustawienie
respektujemy zawsze — dostępność jest ważniejsza od efektu.

## Ekrany

1. **Główny** — kula, pole tekstowe (rozmowa działa też z klawiatury), przycisk
   push-to-talk, dyskretny licznik aktywnych zadań.
2. **Rozmowa** — historia, strumieniowana odpowiedź, możliwość przerwania.
3. **Zadania** — aktywne i historia, postęp, kroki, przycisk „zatrzymaj",
   rozwijane szczegóły z dziennika.
4. **Pamięć** — przeglądanie, szukanie, edycja, usuwanie. Widoczne rodzaje wpisów
   i to, co przypięte. Osobna, wyraźnie oznaczona sekcja sejfu (tylko nazwy).
5. **Urządzenia** — komputer i serwery, stan, ostatni kontakt, zdalne zadania.
6. **Subskrypcje** — zainteresowania, źródła, próg istotności, godziny ciszy.
7. **Ustawienia** — osobowość, głos, mikrofon, słowo aktywacyjne, autostart,
   modele i klucze, zasady pamięci, tryb szczegółowy.
8. **Diagnostyka** (opcjonalna) — audyt, plany, zużycie modeli, dzierżawy zasobów.

## Pierwsze uruchomienie

Rozmowa, nie formularz. Kula wita, mówi jedno zdanie, zadaje jedno pytanie na
ekran, płynnie przechodzi dalej. Ustala: imię i formę zwracania się, język,
osobowość, głos, mikrofon (z kalibracją hałasu), słowo aktywacyjne, autostart,
godziny ciszy, zasady pamięci, klucze modeli, integracje, pierwsze subskrypcje.
Każdy krok da się pominąć. Postęp widoczny, ale nie w postaci paska „3/12".

## Zasady zachowania interfejsu

- Zamknięcie okna **nie** zatrzymuje GARIS-a — okno się chowa, praca trwa.
- Domyślnie cicho: postęp widać, gdy użytkownik patrzy; nie wypycha powiadomień.
- Raport po zadaniu jest krótki. Szczegóły po rozwinięciu, techniczne w trybie
  developerskim.
- Nic nie pyta o zgodę poza tym, co wymaga jej w `docs/SAFETY.md`.
- Aplikacja musi być używalna wyłącznie klawiaturą; focus jest zawsze widoczny.
