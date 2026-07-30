# Roadmapa

Etapami, ale celem jest pełny GARIS — nie demonstracja otwierająca Notatnik.
Każdy etap ma kryteria ukończenia, które da się sprawdzić, a nie ocenić.

## Etap 1 — silnik ✅ ukończony

Rdzeń, który działa i jest przetestowany. Bez GUI, na modelach chmurowych.

- kontrolowana ścieżka wykonania: rejestr, polityka, zgody, dzierżawy, audyt
- pamięć szyfrowana + sejf poświadczeń
- router modeli (OpenAI / Claude / Gemini / Ollama jako slot / atrapa)
- 53 narzędzia: pliki, PowerShell, procesy, usługi, rejestr, sieć, firewall,
  pakiety, ekran, mysz, klawiatura, schowek, okna, web, zdalne hosty, pamięć, sejf
- pętla agenta: plan → wykonaj → sprawdź → napraw → raport
- trwałe zadania: dziennik kroków, wznawianie po restarcie, współbieżność
- CLI: `do`, `tasks`, `task`, `stop`, `approvals`, `approve`, `reject`, `memory`,
  `vault`, `doctor`, `activity`, `tools`, `config`, `serve`
- 173 testy, zielone

**Kryteria spełnione:** zadanie zablokowane na płatności wznawia się po symulowanym
restarcie bez powtarzania wykonanych kroków; pamięć nie zostawia jawnego tekstu w
pliku bazy ani w WAL; osobowość nie ma ścieżki do polityki.

## Etap 2 — GUI (Tauri 2) 🔄 w toku

Aplikacja, która wygląda jak część systemu. Wymagania wizualne: `docs/UI.md` —
Liquid Glass, framer-motion, **wszystko animowane**, animowana kula ze stanami.

- ✅ lokalne API HTTP+WebSocket jako kontrakt między rdzeniem a UI (`docs/API.md`)
- ✅ kula WebGL: sześć stanów, morfowanie między nimi, reakcja na mikrofon,
  fallback 2D, pauza renderu przy ukrytym oknie
- ✅ ekrany: główny, rozmowa, zadania, pamięć + sejf, urządzenia, subskrypcje,
  ustawienia, diagnostyka
- ✅ karty zgód: skutki nazwane po polsku, animowane potwierdzenie decyzji
- ✅ onboarding jako rozmowa, nie formularz
- ✅ powłoka Tauri 2 napisana: tray, hide-on-close, Mica, autostart, skrót globalny
- ⏳ **kompilacja powłoki na Windows 11** — jedyne, czego nie dało się zrobić
  w środowisku bez Windows i bez webkit2gtk
- ⏳ ikony, instalator MSI/NSIS, podpis

**Kryteria:** 60 fps przy 20 zadaniach; okno zamknięte → zadanie dalej kończy się
i pojawia w powiadomieniu; `prefers-reduced-motion` respektowane; pełna obsługa
klawiaturą.

## Etap 3 — głos

- wejście/wyjście audio, wybór mikrofonu i głośników, kalibracja hałasu
- słowo aktywacyjne lokalnie (openWakeWord) — strumień nie wychodzi z komputera
- rozmowa w czasie rzeczywistym: realtime API dostawcy (Gemini Live / OpenAI
  Realtime) tam, gdzie jest, silniki lokalne (faster-whisper + Piper) jako
  alternatywa; wybiera router, nie użytkownik
- przerywanie wypowiedzi GARIS-a (barge-in), push-to-talk, skrót klawiszowy
- zmiana słowa aktywacyjnego i głosu

**Kryteria:** przerwanie w trakcie mówienia zatrzymuje TTS w ≤200 ms; wake word
działa offline; brak sieci → rozmowa nadal możliwa lokalnie.

## Etap 4 — Windows na serio

Ścieżki natywne przetestowane na Windows 11, nie tylko zadeklarowane.

- rejestr, usługi, firewall, UAC, winget, sterowanie ekranem i wejściem
- **CI na `windows-latest`** — od tego etapu obowiązkowe
- instalator (MSI/NSIS), podpis, aktualizacje

**Kryteria:** zestaw testów zielony na Windows; `garis doctor` nie zgłasza
niedostępnych narzędzi na czystej instalacji Windows 11.

## Etap 5 — GARIS Server

- instalator dla Linuksa, usługa systemd, kanał desktop↔serwer z tokenem
- narzędzia: Docker, usługi, sieć, kopie zapasowe, aktualizacje
- `sudo` przez kontrolowane narzędzia, pełny audyt

**Kryteria:** „zainstaluj na serwerze nową usługę i skonfiguruj ją" z desktopa
kończy się działającą usługą, przetestowaną przez agenta serwerowego, z raportem
i śladem audytu na obu maszynach.

## Etap 6 — subskrypcje, briefing, proaktywność

- subskrypcje dowolnych zainteresowań, realne źródła, ocena istotności
- filtr szumu, godziny ciszy, wyciszenie podczas grania
- briefing po starcie systemu
- zauważanie powtarzalnych czynności i propozycje automatyzacji
- wykrywanie problemów z komputerem i serwerem

**Kryteria:** dzień pracy bez ani jednego nieistotnego powiadomienia; briefing
mówi tylko o rzeczach, które zmieniły się od ostatniego razu.

## Etap 7 — GARIS Mobile

Pilot, nie druga aplikacja: rozmowa, zlecanie zadań komputerowi i serwerowi,
postęp, odbiór rezultatów, **zatwierdzanie zgód**, zatrzymywanie zadań.

**Kryteria:** zgoda zatwierdzona z telefonu odblokowuje zadanie na komputerze;
działa przy komputerze w innej sieci.

## Etap 8 — otwarcie ekosystemu

- modele lokalne (Ollama) jako pełnoprawna ścieżka
- **dostawca zgodny z API OpenAI** (LM Studio, vLLM, Groq, OpenRouter,
  llama.cpp) — jeden plik, ogromny zasięg
- klient MCP, pluginy, integracje: poczta, kalendarz, wiadomości
- publiczny, udokumentowany kontrakt `ToolSpec` dla wtyczek zewnętrznych

**Kryteria:** zewnętrzna osoba dodaje działające narzędzie bez zmiany kodu rdzenia,
opierając się wyłącznie na `docs/TOOLS.md`.

## Rzeczy przyjęte do zrobienia, bez etapu

- **Audyt odporny na manipulację** — łańcuch skrótów wiążący każdy wiersz z
  poprzednim. Dla agenta z dostępem do shella i płatności dowód, że dziennik nie
  został po cichu zmieniony (również przez samego GARIS-a), jest realną wartością.
- **Tryb „pokaż, co zrobisz"** — `garis do --dry-run` już istnieje; ma trafić do
  GUI jako pełnoprawna funkcja, nie flaga debugowania.
- Rotacja klucza głównego i eksport/import stanu.
- Limity budżetu per zadanie, nie tylko miesięczne.
