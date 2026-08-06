# Znane problemy

Kolejność: najpierw to, co blokuje.

---

## 0a. ~~Klucz nie działa do restartu~~ · ~~Nieprawidłowy klucz udaje sprawny~~ — NAPRAWIONE

Dwa z trzech błędów P0 audytu (`docs/AUDIT.md` P0-1 i P0-3), oba w silniku, oba
zamknięte testem, który wcześniej by nie przeszedł:

- zapis do sejfu ogłasza zmianę, `ProviderPool` przebudowuje dostawców i sprawdza
  ich, a `POST /api/vault` **czeka** na wynik i zwraca go w odpowiedzi;
- „dostępny" znaczy teraz „router tam pośle pracę" i wynika z odpowiedzi
  dostawcy, nie z obecności klucza w sejfie.

Zostaje **P0-2** (powitanie staje się wiecznym zadaniem) — to milestone M2,
klasyfikator intencji, nietknięty.

Czego to nie obejmuje: interfejsu. API niesie już `status` i `reason` dla każdego
dostawcy, ale ekran ustawień nadal pokazuje stan sejfu, nie stan dostawcy.
Zamiana `available` na siedem statusów po stronie okna jest do zrobienia.

Sprawdzone na atrapie transportu HTTP, **nie na prawdziwych kluczach** — treść
odpowiedzi 400/401/429 u OpenAI, Anthropic i Google jest odwzorowana z
dokumentacji, nie zmierzona. Pierwszy prawdziwy klucz może wymagać poprawki
w `classify_response`.

---

## 0. Test na Windows 11 (pierwszy prawdziwy) — przyczyna znaleziona i naprawiona

Zgłoszone objawy: martwe przyciski, brak zapisu klucza Gemini, wieczne
„Łączę się…", brak możliwości wykonania zadania.

**Jedna przyczyna dla wszystkich czterech: brak CORS w API silnika.** Okno Tauri
ma origin `http://tauri.localhost`, silnik stoi na `127.0.0.1` — przeglądarka
blokowała każde żądanie przed uwierzytelnieniem. Przyciski „nie reagowały", bo
każdy przycisk to wywołanie API.

Naprawione i zweryfikowane w prawdziwej przeglądarce
(`apps/desktop/tools/ui-probe.mjs`): status pokazuje `v0.1.0`, cała nawigacja
klika, klucz Gemini zapisuje się do sejfu i przeżywa przeładowanie, brak
przepełnienia przy czterech rozmiarach okna.

**Nadal do sprawdzenia na Windows**, bo sonda używa Chromium, nie WebView2:
`invoke` do powłoki, tray, Mica, `Ctrl+Alt+G`, autostart, skalowanie 125/150/175%.

---

## 1. ~~Instalator nie zawiera silnika~~ — NAPRAWIONE, zweryfikowane na Linuksie

Instalator wozi teraz silnik. Mechanizm:

- `packaging/build_engine.py` zamraża silnik PyInstallerem w jeden plik,
  **uruchamia go** (`garis doctor`) zanim cokolwiek skopiuje, i kładzie jako
  `binaries/garis-<target-triple>`;
- `tauri.conf.json` deklaruje `"externalBin": ["binaries/garis"]`;
- Tauri zdejmuje sufiks triple przy pakowaniu, więc obok powłoki ląduje zwykły
  `garis` / `garis.exe`;
- `engine_binary()` w `main.rs` szuka najpierw obok własnego pliku
  wykonywalnego, dopiero potem na `PATH`.

Zweryfikowane realnie, ale **na Linuksie** (`.deb`): instalacja z paczki →
aplikacja uruchamia spakowany silnik → `/api/health` i `/api/state` odpowiadają.
Użytkownik nie potrzebuje Pythona.

**Co zostało:** to samo na Windows (`.msi`). Kod jest wspólny, sprzęt nie.

## 2. Osierocony silnik przy nienaturalnym zakończeniu — CZĘŚCIOWO

Wyjście przez tray („Zakończ") idzie przez `RunEvent::Exit` → `stop_engine`,
które zabija i **reapuje** proces. To jest ścieżka zamierzona i pokryta kodem.

Czego to nie łapie: `kill -9`, Menedżer zadań, crash powłoki. Żaden handler
w procesie się wtedy nie wykona. Na Windows domyka to **job object**
(`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) — kernel zabija silnik razem z powłoką,
niezależnie od tego, jak powłoka zginęła.

Stan: job object **skompilowany pod target Windows, nieuruchomiony**. Na
Linuksie odpowiednika nie ma i `SIGTERM` na powłokę faktycznie zostawia sierotę
— sprawdzone. Linux nie jest platformą docelową, więc tego nie domykam.

## 3. GitHub Actions nie startuje — BLOKUJĄCE dla CI

> The job was not started because your account is locked due to a billing issue.

Blokada konta, nie konfiguracja workflow: <https://github.com/settings/billing>.
Dopóki trwa, lokalna maszyna jest jedynym źródłem prawdy dla `.msi`.

---

## 4. Ryzyka przy pierwszym buildzie na Windows

Nie są to potwierdzone błędy — to miejsca, gdzie kod nie został wykonany.
Skreślone pozycje to te, które zdążyły się już potwierdzić albo upaść gdzie
indziej.

- ~~Profil `release` (`panic=abort` + `lto` + `strip`) nigdy nie linkował.~~
  Zlinkował się — na Linuksie, w pełnym buildzie release.
- ~~`npm run tauri build` nie działa: brak `@tauri-apps/cli`.~~ Dodane do
  `devDependencies`.
- ~~Konfiguracja `plugins.autostart` wywala aplikację przy starcie.~~ Naprawione.
- **Mica.** Kompiluje się pod targetem Windows; czy działa z `"transparent":
  true` i `"decorations": false`, nie wiadomo. Czarne albo całkiem przezroczyste
  okno — pierwszy podejrzany.
- **Okno bez dekoracji.** Przeciąganie i zmiana rozmiaru zależą wyłącznie od
  warstwy webowej.
- **Tray.** W kontenerze nie zainicjował się w ogóle (brak dbus), więc ikona,
  menu i klik są nietknięte.
- **`Ctrl+Alt+G`.** Kompiluje się od `8ccaac7`; rejestracja skrótu i kolizje
  z systemem — nieznane.
- **Autostart.** Nic nigdy nie zapisało wpisu do rejestru.
- **WebView2.** `downloadBootstrapper` wymaga sieci przy pierwszej instalacji.
- **Port 8756 na sztywno.** Zajęty port to brak połączenia bez dobrego
  komunikatu.

## 5. Czego w tej rundzie NIE zrobiono

Uczciwie, żeby nie wyglądało na skończone:

- **Priorytet 5 — hierarchia ekranów.** Ustawienia to nadal jedna długa strona,
  nie dwanaście kategorii. Sekcje główne zostały jak były.
- **Priorytet 6 — personalizacja.** Motyw, akcent, skala UI, gęstość, jakość
  kuli, dźwięki, tryb cichy i reszta listy **nie istnieją**. Świadomie nie
  dodałem martwych przełączników — zgodnie z Twoim warunkiem.
  Fundament pod to jest: `--glass-strength`, `data-glass="off"`,
  `data-quiet="true"` i `data-theme` działają, brakuje ekranu, który je ustawia.
- **Skalowanie Windows 125/150/175%** — nie do sprawdzenia w Chromium na
  Linuksie.
- **Zadanie end-to-end z prawdziwym modelem** — ścieżka „zapisz klucz → zleć
  zadanie → wynik" jest teraz pokryta testem przez prawdziwy adapter Gemini, ale
  z podmienionym transportem HTTP. Na **żywym** dostawcy nadal nie zlecałem
  zadania.

## 6. Drobne

- `EngineInfo.error` trafia już do okna: zamiast wiecznego „Łączę się…" jest
  siedem rozróżnialnych stanów (uruchamiam / handshake / połączono / silnik nie
  wystartował / token odrzucony / brak odpowiedzi / ponawiam), przycisk
  „Uruchom silnik ponownie" i „Skopiuj diagnostykę". Ścieżka przez Tauri
  (`restart_engine`) **nie była uruchomiona** — w przeglądarce nie ma `invoke`.
- `window-vibrancy` przypięte na `0.5` (dostępne `0.8`). Nie ruszam bez
  możliwości obejrzenia efektu.
- Głos ma warstwę decyzyjną bez audio — `docs/ROADMAP.md`, M13.
- **`~/.garis/plugins/` powstaje przy każdym starcie i nikt go nie czyta.**
  `paths.py:90` deklaruje właściwość `plugins`, `ensure()` tworzy katalog, i na
  tym koniec — grep po całym źródle nie znajduje ani jednego czytelnika. Pusty
  katalog o takiej nazwie sugeruje, że pluginy są obsługiwane; nie są, i nie ma
  ani jednej linii kodu, która by je ładowała. Albo katalog znika, albo jest
  pierwszą połową funkcji, której nikt nie skończył — do rozstrzygnięcia razem z
  etapem 8. Zapisane, żeby następny czytelnik nie wyciągnął z niego wniosku.
