# Znane problemy

Stan na commit z pakowaniem silnika. Kolejność: najpierw to, co blokuje.

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

## 5. Drobne

- Interfejs nie pokazuje jeszcze `EngineInfo.error` — powłoka je wypełnia, UI
  ignoruje. Log silnika (`engine.log`) już jest i zawiera powód.
- `window-vibrancy` przypięte na `0.5` (dostępne `0.8`). Nie ruszam bez
  możliwości obejrzenia efektu.
- Głos ma warstwę decyzyjną bez audio — `docs/ROADMAP.md`, etap 3.
