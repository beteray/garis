# GARIS Desktop

Interfejs GARIS-a: powłoka Tauri 2 (Rust) + React/TypeScript.

## Stan

| Warstwa | Stan |
|---|---|
| Front (React, TS, framer-motion, WebGL) | **zbudowany i skompilowany** — `npm run build` przechodzi, TypeScript w trybie strict |
| Powłoka Tauri (Rust: tray, Mica, autostart, cykl życia silnika) | **napisana, nieskompilowana** — wymaga Windows albo Linuksa z webkit2gtk; ta sesja nie miała żadnego z nich |
| Głos (nasłuch, STT, TTS) | etap 3 — jest przycisk push-to-talk i realny miernik poziomu, silniki dochodzą później |
| Subskrypcje | etap 6 — panel pokazuje kontrakt, źródła dochodzą później |

Front łączy się z lokalnym API silnika (`docs/API.md`), więc działa też w zwykłej
przeglądarce — bez tray-a i efektów okna, ale z pełną resztą.

## Uruchomienie

```bash
# 1. silnik
cd ../..
.venv/bin/garis serve --print-token

# 2. interfejs
cd apps/desktop
npm install
npm run dev
# przeglądarka: http://localhost:5183?token=<token z kroku 1>
```

Wersja desktopowa (wymaga Rusta i zależności systemowych Tauri):

```bash
npm run tauri dev
npm run tauri build      # MSI + NSIS
```

W trybie Tauri powłoka sama uruchamia `garis serve` i przekazuje adres oraz token
do interfejsu poleceniem `engine_info` — token nigdy nie ląduje w kodzie front-endu
ani w `localStorage` aplikacji desktopowej.

## Co gdzie leży

```
src/
  App.tsx              powłoka: nawigacja, animowane przełączanie widoków
  components/
    Orb.tsx            kula GARIS-a — WebGL, morfowanie stanów, fallback 2D
    Composer.tsx       pole celu + push-to-talk + miernik mikrofonu
    Conversation.tsx   rozmowa, strumieniowana odpowiedź
    Tasks.tsx          zadania: wejście, postęp, zatrzymanie, szczegóły
    Approvals.tsx      prośby o zgodę (płatność, publikacja, wiadomość…)
    Panels.tsx         pamięć + sejf, urządzenia, subskrypcje, diagnostyka
    Settings.tsx       ustawienia — bez wyboru modelu do zadania
    Onboarding.tsx     pierwsze uruchomienie jako rozmowa
    ui.tsx             szkło, animowane liczby, stany puste, odznaki
  lib/
    api.ts             klient HTTP + WebSocket z auto-reconnectem
    store.ts           stan UI karmiony zdarzeniami z silnika
    motion.ts          wspólne krzywe i warianty animacji
  styles/
    tokens.css         cały język wizualny w jednym pliku
    global.css         szkło, kontrolki, przewijanie, shimmer
src-tauri/             powłoka natywna
```

## Zasady, których nie wolno złamać

Pełny kontrakt: `docs/UI.md`.

1. **Wszystko, co się zmienia, zmienia się z animacją.** Bez przeskoków stanu,
   bez nagle pojawiających się elementów, bez skaczących liczb.
2. **Kula morfuje, nie przełącza się.** Parametry shadera dążą do celu co klatkę.
3. **`prefers-reduced-motion` wygrywa z każdym efektem.** Ruch znika, forma zostaje.
4. **UI nie podejmuje decyzji silnika.** Nie zgaduje, co wymaga zgody, nie wymyśla
   stanu zadania, nie trzyma kolejki celów. Wysyła intencję, renderuje odpowiedź.
5. **Zamknięcie okna nie zatrzymuje pracy.** Okno się chowa, silnik pracuje dalej.
6. **Wartości sekretów nigdy nie trafiają do interfejsu.** Tylko nazwy i referencje.

## Wydajność

Kula pauzuje render przy ukrytym oknie (`visibilitychange`) — GARIS działa cały
dzień w tle i nie ma prawa grzać GPU, kiedy nikt nie patrzy. `devicePixelRatio`
jest ograniczony do 2, a shader używa jednego trójkąta na pełny ekran.
