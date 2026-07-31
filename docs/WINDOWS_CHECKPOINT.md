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
git log --oneline -1        # oczekiwane: 18e0b18 lub nowszy

python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev,windows]"
cd apps\desktop; npm ci; cd ..\..
```

## 3–5. Testy, jakość, frontend

```powershell
.\.venv\Scripts\python -m pytest -q          # oczekiwane: 243 passed
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

To pierwszy moment, w którym cokolwiek się **linkuje**. Profil `release` ma
`panic = "abort"`, `lto = true` i `strip = true` — żadne z tego nigdy nie
przeszło linkera.

```powershell
cd apps\desktop
npm run tauri build -- --no-bundle
```

Wynik: `apps\desktop\src-tauri\target\release\GARIS.exe`.

Jeśli tu pęknie, to jest błąd linkera lub profilu, nie kodu — patrz
`docs/KNOWN_ISSUES.md` §3.

## 9. Instalator

**Najpierw zbuduj silnik**, inaczej instalator wyjdzie pusty w sensie, który
opisuje `KNOWN_ISSUES.md` §1 — aplikacja się zainstaluje i nigdy nie połączy.

```powershell
cd ..\..
.\.venv\Scripts\pip install pyinstaller
.\.venv\Scripts\pyinstaller --onefile --name garis --console core\src\garis\cli.py
# → dist\garis.exe
```

Sprawdź, że sam silnik żyje, zanim spakujesz go w cokolwiek:

```powershell
.\dist\garis.exe doctor
```

Podepnij go jako sidecar:

```powershell
mkdir apps\desktop\src-tauri\binaries -Force
copy dist\garis.exe apps\desktop\src-tauri\binaries\garis-x86_64-pc-windows-msvc.exe
```

W `apps\desktop\src-tauri\tauri.conf.json`, w sekcji `"bundle"`, dopisz:

```jsonc
"externalBin": ["binaries/garis"]
```

I zbuduj:

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

Można pominąć cały krok z PyInstallerem i wskazać silnik jawnie:

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

- [ ] `garis activity --minutes 30` pokazuje wykonane akcje
- [ ] Brak nieobsłużonych wyjątków i „poisoned mutex"
- [ ] Brak mignięć okna konsoli przy starcie silnika (`CREATE_NO_WINDOW`)

---

## Po zaliczeniu

Zaktualizuj `docs/CURRENT_STATE.md`: przenieś, co zweryfikowane, z 🔨 na ✅ —
**tylko to, co naprawdę odhaczyłeś.** Wpisz do `docs/KNOWN_ISSUES.md` wszystko,
co pękło. Dopiero wtedy zaczyna się etap 3 (audio i głos).
