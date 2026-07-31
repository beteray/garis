# Znane problemy

Stan na commit `18e0b18`. Kolejność: najpierw to, co blokuje działającą
instalację.

---

## 1. Instalator nie zawiera silnika — BLOKUJĄCE

**Objaw:** po instalacji z `.msi` okno się otwiera, tray działa, ale interfejs
zostaje w stanie „rozłączony" i nigdy się nie łączy.

**Przyczyna:** `apps/desktop/src-tauri/tauri.conf.json` ma `"resources": []` i
nie deklaruje `externalBin`. Bundle wozi wyłącznie powłokę Tauri. Silnik to
osobny program w Pythonie, którego nikt do paczki nie wkłada, więc na czystej
maszynie nie ma go ani obok pliku wykonywalnego, ani na `PATH`.

**Czego to *nie* jest:** to nie jest błąd startu ani łączności. Powłoka robi
dokładnie to, co ma robić — nie ma tylko czego uruchomić.

**Obejście na czas testów:** zainstaluj silnik na maszynie testowej
(`pip install -e .`) albo wskaż go jawnie:

```powershell
$env:GARIS_ENGINE = "C:\ścieżka\do\garis.exe"
```

Od commita `18e0b18` powłoka szuka też `garis.exe` obok własnego pliku
wykonywalnego, więc skopiowanie go do katalogu instalacji wystarczy.

**Właściwa naprawa** (wymaga Windows, nieprzetestowana — dlatego nie jest
zacommitowana): zbuduj silnik PyInstallerem i zadeklaruj go jako sidecar.

```powershell
pip install pyinstaller
pyinstaller --onefile --name garis --console core/src/garis/cli.py
# → dist/garis.exe
```

Tauri wymaga sufiksu z triple targetu:

```powershell
mkdir apps/desktop/src-tauri/binaries
copy dist\garis.exe apps\desktop\src-tauri\binaries\garis-x86_64-pc-windows-msvc.exe
```

```jsonc
// tauri.conf.json → "bundle"
"externalBin": ["binaries/garis"]
```

Uwaga: po dodaniu `externalBin` build **nie przejdzie**, dopóki plik nie
istnieje. Dlatego ta zmiana należy do sesji na Windows, która może ją od razu
sprawdzić, a nie do commita na ślepo.

---

## 2. GitHub Actions nie startuje — BLOKUJĄCE dla CI

Wszystkie zadania kończą się porażką w 2–4 sekundy, bez wykonania ani jednego
kroku:

> The job was not started because your account is locked due to a billing issue.

Potwierdzone na dwóch przebiegach (`30634740763`, `30635280781`). To blokada
konta, nie konfiguracja workflow. Do odblokowania:
<https://github.com/settings/billing>.

Dopóki trwa, `.msi` nie powstanie automatycznie i **lokalny Windows jest
jedynym źródłem prawdy** dla funkcji systemowych.

---

## 3. Ryzyka do sprawdzenia przy pierwszym buildzie na Windows

Żadne z poniższych nie jest potwierdzonym błędem — to miejsca, gdzie kod nigdy
nie został wykonany i gdzie spodziewam się problemów najpierw.

- **Profil `release`.** `panic = "abort"` + `lto = true` + `strip = true` nigdy
  nie przeszły pełnego linkowania. `cargo check` nie linkuje, więc błędy
  linkera są tu wciąż możliwe.
- **Mica.** `apply_mica` kompiluje się pod targetem Windows, ale nie wiadomo,
  czy działa z `"transparent": true` i `"decorations": false`. Jeśli okno
  wyjdzie czarne albo całkiem przezroczyste — to jest pierwszy podejrzany.
- **Okno bez dekoracji.** Nie ma natywnego paska tytułu; przeciąganie i zmiana
  rozmiaru zależą wyłącznie od warstwy webowej.
- **WebView2.** `webviewInstallMode: downloadBootstrapper` wymaga sieci przy
  pierwszej instalacji na maszynie bez WebView2.
- **Autostart.** Wtyczka jest podpięta, ale nic nigdy nie zapisało wpisu do
  rejestru. Argument `--minimised` jest przekazywany do wtyczki i obsługiwany
  w `setup()`, lecz nieprzetestowany.
- **Tray.** Ikona, menu i klik lewym przyciskiem — skompilowane, nieuruchomione.
- **`Ctrl+Alt+G`.** Do commita `8ccaac7` w ogóle się nie kompilował (brakujący
  `use GlobalShortcutExt`). Teraz się kompiluje; czy rejestracja skrótu się
  udaje i czy nie koliduje z czymś w systemie — nie wiadomo.
- **Port 8756 na sztywno.** Zajęty port oznacza brak połączenia bez sensownego
  komunikatu.

---

## 4. Drobne, niepilne

- `window-vibrancy` jest przypięte na `0.5` (dostępne `0.8`). Nie ruszam bez
  możliwości sprawdzenia efektu wizualnie.
- Głos ma warstwę decyzyjną (maszyna stanów, słowo aktywacyjne, kalibracja,
  wybór drogi) bez żadnego audio pod spodem — patrz `docs/ROADMAP.md`, etap 3.
- Interfejs nie pokazuje jeszcze pola `EngineInfo.error`; powłoka je wypełnia,
  UI je ignoruje. Do zrobienia razem z panelem diagnostyki.
