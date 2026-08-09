# Checkpoint na Windows 11 — komendy i test ręczny

Ten dokument jest do wykonania **na maszynie z Windows 11**. Kroki 1–7 są już
zaliczone na Linuksie (`docs/CURRENT_STATE.md`) i tutaj służą tylko jako
potwierdzenie, że na Windows też przechodzą. Prawdziwa treść zaczyna się od
kroku 8.

Wszystko poniżej odpalaj w **PowerShell**, z katalogu głównego repozytorium.

## 0. Czego potrzebujesz zainstalowanego

- Python 3.11+ (3.12 zalecany)
- Node 22+
- Rust (stable, toolchain MSVC) — <https://rustup.rs>
- **Visual Studio Build Tools** z komponentem „Desktop development with C++"
  (bez tego Rust nie zlinkuje niczego na Windows)
- WebView2 Runtime — na Windows 11 jest fabrycznie

```powershell
python --version; node --version; rustc --version; cargo --version
```

## 1–2. Repo i zależności

```powershell
git status
git log --oneline -1

python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev,windows]"
cd apps\desktop; npm ci; cd ..\..
```

## 3–5. Testy, jakość, frontend

```powershell
.\.venv\Scripts\python -m pytest -q          # oczekiwane: 249 passed
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m mypy
cd apps\desktop; npm run build; cd ..\..
```

Na Windows dochodzi sprawdzenie, którego na Linuksie zrobić się nie da —
**czy narzędzia Windows są naprawdę dostępne**, a nie tylko zadeklarowane:

```powershell
.\.venv\Scripts\garis doctor
.\.venv\Scripts\garis tools
```

W `tools` narzędzia `registry_read`, `registry_write`, `firewall_rule`,
`window_list`, `window_focus` muszą być oznaczone jako dostępne. Jeśli nie są —
to jest pierwszy błąd do naprawienia i nic dalej nie ma sensu.

## 6–7. Powłoka w Ruście

```powershell
cd apps\desktop\src-tauri
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cd ..\..\..
```

## 8. Produkcyjny build Tauri

Profil `release` (`panic = "abort"`, `lto`, `strip`) linkuje się na Linuksie;
na Windows to pierwsze linkowanie.

```powershell
cd apps\desktop
npm run tauri build -- --no-bundle
```

Wynik: `apps\desktop\src-tauri\target\release\GARIS.exe`.

Jeśli tu pęknie, to jest błąd linkera lub profilu, nie kodu — patrz
`docs/KNOWN_ISSUES.md` §3.

## 9. Instalator

**Najpierw zbuduj silnik.** Jeden skrypt: zamraża go PyInstallerem, *uruchamia
zamrożoną binarkę* zanim cokolwiek skopiuje, i kładzie ją jako sidecar pod
nazwą, której oczekuje Tauri.

```powershell
cd ..\..
.\.venv\Scripts\pip install pyinstaller
.\.venv\Scripts\python packaging\build_engine.py --clean
```

Oczekiwane na końcu:

```
Gotowe: ...\apps\desktop\src-tauri\binaries\garis-x86_64-pc-windows-msvc.exe  (~25 MB)
```

`externalBin` jest już w `tauri.conf.json` — nic nie dopisujesz. Buduj:

```powershell
cd apps\desktop
npm run tauri build
```

Wynik:
- `src-tauri\target\release\bundle\msi\GARIS_0.1.0_x64_en-US.msi`
- `src-tauri\target\release\bundle\nsis\GARIS_0.1.0_x64-setup.exe`

## 10. Instalacja

```powershell
Start-Process .\src-tauri\target\release\bundle\msi\GARIS_0.1.0_x64_en-US.msi
```

Domyślnie ląduje w `C:\Program Files\GARIS\`. Sprawdź, że `garis.exe` leży
**obok** `GARIS.exe` — powłoka szuka go tam w pierwszej kolejności.

### Gdyby sidecar sprawiał kłopot

Można wskazać silnik jawnie — przydatne przy testowaniu samego lifecycle'u:

```powershell
$env:GARIS_ENGINE = "C:\ścieżka\do\repo\.venv\Scripts\garis.exe"
```

To wystarczy do przetestowania lifecycle'u, tray-a i skrótu — tylko nie jest to
test instalacji.

---

# Test ręczny

Odhaczaj po kolei. **Nie oznaczaj niczego jako działające na podstawie tego, że
się skompilowało.**

### Start i okno

- [ ] Pierwsze uruchomienie kończy się widocznym oknem
- [ ] Okno ma szkło (Mica), nie jest czarne ani całkiem przezroczyste
- [ ] Okno da się przesunąć i zmienić mu rozmiar (brak natywnego paska tytułu —
      to robi warstwa webowa)
- [ ] Onboarding pojawia się jako rozmowa, nie formularz

### Łączność z runtime

- [ ] Interfejs nie zostaje w stanie „rozłączony"
- [ ] Widać stan silnika, listę narzędzi, pustą listę zadań
- [ ] Zlecenie celu (`sprawdź, ile miejsca zostało na dysku`) przechodzi
      pełną pętlę i kończy się raportem
- [ ] Ten sam cel z wiersza poleceń daje ten sam wynik:
      `garis do "sprawdź, ile miejsca zostało na dysku"`

### Tray i lifecycle — reguła produktu: zamknięcie okna nie zatrzymuje pracy

- [ ] Zamknięcie okna (X) **ukrywa** je, nie kończy procesu
- [ ] Ikona w zasobniku jest widoczna i ma tooltip „GARIS"
- [ ] Kliknięcie lewym przyciskiem przywraca okno
- [ ] Menu tray-a ma „Otwórz GARIS-a", „Aktywne zadania", „Zakończ"
- [ ] Zlecone zadanie **działa dalej** po zamknięciu okna
      (zleć coś dłuższego, zamknij okno, otwórz — postęp ma iść naprzód)
- [ ] „Zakończ" faktycznie kończy aplikację **i** silnik
      (`Get-Process garis` nic nie zwraca)
- [ ] **Sierota:** ubij `GARIS.exe` z Menedżera zadań — `Get-Process garis`
      też ma nic nie zwracać. To sprawdza job object, jedyną rzecz, która
      działa, gdy żaden handler się nie wykona.

### Skrót globalny

- [ ] `Ctrl+Alt+G` przywołuje okno, gdy jest ukryte
- [ ] Działa też, gdy aktywna jest inna aplikacja
- [ ] Nie koliduje z niczym w systemie

### Autostart

- [ ] Włączenie autostartu tworzy wpis:
      `Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"`
- [ ] Po przelogowaniu GARIS startuje **zminimalizowany** (`--minimised`),
      bez wyskakującego okna i bez mignięcia konsoli
- [ ] Wyłączenie autostartu usuwa wpis

### Funkcje Windows

- [ ] `garis doctor` nie zgłasza braków
- [ ] Odczyt z rejestru działa
- [ ] `window_list` widzi otwarte okna
- [ ] Zapis do rejestru / reguła firewalla proszą o zgodę, jeśli polityka tak mówi

### Logi

- [ ] `%LOCALAPPDATA%\ai.garis.desktop\logs\engine.log` istnieje i pokazuje,
      **którą** binarkę uruchomiono (ma być ta z katalogu instalacji)
- [ ] W logu widnieje `Token: (pominięty w logu)` — token nigdy nie trafia na dysk
- [ ] `garis activity --minutes 30` pokazuje wykonane akcje
- [ ] Brak nieobsłużonych wyjątków i „poisoned mutex"
- [ ] Brak mignięć okna konsoli przy starcie silnika (`CREATE_NO_WINDOW`)

---

## E1 — pierwszy prawdziwy odczyt Windows (`windows.audio.master.get`)

To jest **brakujący dowód E1**, nie osobny etap. Kod jest napisany i
przetestowany, ale środowisko, w którym powstał, to Linux — Core Audio nigdy
nie zostało wywołane.

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev,windows]"
.venv\Scripts\python -m pytest -q

# Ścieżka produkcyjna, nie adapter: odruch buduje plan ze zdolnością,
# a plan idzie przez CapabilityRunner.
.venv\Scripts\garis do "jaka jest głośność"
.venv\Scripts\garis do "czy komputer jest wyciszony"
```

Oczekiwane: jedno zdanie z **prawdziwą** wartością, np. `Głośność: 42%.`,
i `verified: true` w raporcie.

`garis do` mówi zdanie. Żeby zobaczyć surowe liczby do porównania z suwakiem:

```powershell
.venv\Scripts\python.exe tools\e1_audio_check.py
```

Wypisuje `endpoint_id`, `endpoint_name`, `volume_scalar`, `volume_percent`,
`muted`, `verified` i `error` — tą samą drogą produkcyjną, nie z adaptera.

- [ ] `pycaw` i `comtypes` instalują się z extrasu `windows`
- [ ] Odczyt zwraca `endpoint_id`, `endpoint_name`, `volume_scalar`,
      `volume_percent`, `muted`
- [ ] Procent zgadza się z suwakiem głośności w systemie
- [ ] `garis effects list` pokazuje odczyt z `verified: true`
- [ ] Wyciszenie z paska systemowego → ponowny odczyt pokazuje `muted: true`
- [ ] Odłączenie wszystkich urządzeń odtwarzania → **komunikat o braku
      urządzenia**, nie `0%`
- [ ] Głośność systemu **nie zmieniła się** w trakcie żadnego z powyższych

Ostatni punkt jest warunkiem, nie uprzejmością: E1 jest tylko do odczytu.

---

## E2 — pierwsza prawdziwa zmiana w Windows (`windows.audio.master.set` / `.mute`)

**Ten etap rusza suwakiem.** Wszystko poniżej zmienia ustawienia dźwięku i
przywraca je z powrotem. Nie odpalaj tego w trakcie rozmowy ani nagrania.

Sedno E2 nie brzmi „czy da się ustawić głośność”, tylko **czy GARIS uzna zmianę
za wykonaną dopiero po jej zmierzeniu**. `SetMasterVolumeLevelScalar` zwracające
`S_OK` mówi, że wywołanie przeszło, a nie że komputer jest na 30%.

```powershell
.venv\Scripts\python -m pytest -q

# Surowe liczby: prośba, pomiar po zmianie, stan sprzed zmiany.
.venv\Scripts\python.exe tools\e2_audio_check.py
.venv\Scripts\python.exe tools\e2_audio_check.py --percent 55
```

Skrypt ustawia poziom, przywraca poprzedni i wypisuje `requested`, `measured`,
`previous`, `verified`, `disposition` dla obu kroków.

- [ ] `requested` i `measured` różnią się najwyżej o 1 punkt (tolerancja
      kwantyzacji sterownika)
- [ ] `measured` zgadza się z suwakiem głośności w systemie
- [ ] `previous` to poziom, który był tam **przed** zmianą, a nie prośba
- [ ] `verified: true` i `disposition: applied` dla obu kroków
- [ ] Po `--keep` głośność zostaje zmieniona; bez `--keep` wraca na swoje
- [ ] `garis do "ustaw głośność na 30 procent"` kończy się zdaniem z **pomiarem**
      (np. `Głośność: 30% (było 55%).`) i `verified: true`
- [ ] `garis do "wycisz dźwięk"` wycisza; ikona głośnika w pasku pokazuje
      wyciszenie
- [ ] `garis do "odcisz"` zdejmuje wyciszenie, a **poziom głośności zostaje
      nietknięty** (wyciszenie to nie zero)
- [ ] `garis effects list` pokazuje efekt z `disposition: applied` i zapisanym
      celem

### Czego E2 dowodzi mimo awarii

Odłącz wszystkie urządzenia odtwarzania i powtórz `tools\e2_audio_check.py`:

- [ ] komunikat o braku urządzenia, **nie** `0%` i **nie** „ustawione”
- [ ] `disposition: not_applied` — czyli GARIS wie, że nic nie ruszył

### Recovery — najtrudniejszy punkt tego etapu

Zleć zmianę głośności i ubij proces silnika **w trakcie** (`Stop-Process -Name
garis -Force`). Po ponownym uruchomieniu:

- [ ] GARIS **odczytuje** głośność zamiast ustawiać ją drugi raz
- [ ] status recovery to `resolved_goal_only`, a `disposition` zostaje `unknown`
- [ ] w raporcie widnieje, że cel jest zmierzony, ale nie wiadomo, kto go osiągnął

Ostatni punkt jest istotą, nie formalnością: głośność 30% dowodzi, że jest 30%,
a nie że to GARIS ją ustawił — suwak mógł ruszyć człowiek.

---

## E3 — uruchamianie programów (`windows.app.launch`)

Ten etap **startuje aplikacje**. Zamknij, co masz otwarte z rzeczy testowanych
poniżej, żeby „już działał" nie zamaskował braku uruchomienia.

Sedno: `CreateProcess` zwracające pid nie znaczy, że Discord jest na ekranie.
Zdolność czyta listę procesów po starcie i to ten odczyt jest dowodem.

```powershell
.venv\Scripts\garis do "otwórz notatnik"
.venv\Scripts\garis do "uruchom Discorda"
```

- [ ] Odpowiedź to zdanie o **odczycie** (`Notatnik działa.`), nie o wywołaniu
- [ ] `verified: true`, a `garis effects list` pokazuje `disposition: applied`
- [ ] Powtórzenie tego samego polecenia mówi `już działał` i **nie otwiera
      drugiego okna**; efekt ma `disposition: not_applied`
- [ ] `garis do "uruchom program-ktorego-nie-ma"` kończy się porażką z
      `disposition: not_applied` (czyli wolno spróbować ponownie)
- [ ] Program, który startuje przez stub i oddaje sterowanie istniejącej
      instancji (Discord, Steam, przeglądarka), jest **rozpoznany jako
      działający** — to jest właśnie przypadek, w którym pilnowanie pid-u kłamie

### Recovery

Zleć uruchomienie czegoś ciężkiego i ubij silnik w trakcie. Po restarcie:

- [ ] GARIS **czyta listę procesów**, nie uruchamia programu drugi raz
- [ ] status `resolved_goal_only`, `disposition: unknown`
- [ ] gdy program działa, raport mówi, że działa — i **nie twierdzi**, że to
      GARIS go uruchomił

---

## Po zaliczeniu

Zaktualizuj `docs/CURRENT_STATE.md`: przenieś, co zweryfikowane, z 🔨 na ✅ —
**tylko to, co naprawdę odhaczyłeś.** Wpisz do `docs/KNOWN_ISSUES.md` wszystko,
co pękło. Dopiero wtedy zaczyna się etap 3 (audio i głos).
