# Stan projektu — co jest naprawdę sprawdzone

Commit: patrz `git log`.  Ten plik istnieje po to, żeby nikt nie pomylił „kompiluje się"
z „działa". Skompilowany kod, którego nikt nie uruchomił, jest tu opisany jako
nieuruchomiony — nawet jeśli wygląda na skończony.

## Legenda

| Znak | Znaczenie |
|---|---|
| ✅ | **Zweryfikowane** — uruchomione, wynik obejrzany, test tego pilnuje |
| 🔨 | **Tylko skompilowane** — przechodzi kompilator/typy, nigdy nie wykonane |
| ❔ | **Nieprzetestowane** — brak testu i brak uruchomienia |
| ❌ | **Niedziałające** — znany błąd, opisany w `KNOWN_ISSUES.md` |

Na czym to sprawdzano: Ubuntu 24.04, Python 3.12, Rust 1.94, Node 22.
**Na Windows 11 nadal nie sprawdzano niczego.** Sesja, która to pisała, nie
miała dostępu do maszyny z Windows, a GitHub Actions jest zablokowane przez
billing.

Zmieniło się natomiast to, że pełna ścieżka *zbuduj → spakuj → zainstaluj →
uruchom → połącz z runtime* została przejechana naprawdę — na Linuksie, na
paczce `.deb`. Kod tej ścieżki jest wspólny dla obu platform, więc trzy błędy,
które ją blokowały, są już znalezione i naprawione. Windows-owe pozostaje to,
co naprawdę zależy od Windows: Mica, tray, skrót, autostart, `.msi`.

---

## Silnik (Python)

| Obszar | Stan | Dowód |
|---|---|---|
| Runtime: `resolve → policy → zgoda → dzierżawa → wykonanie → audyt` | ✅ | 245 testów |
| Bramki zgody (płatność, publikacja, wiadomość, poświadczenia, trwałe usunięcie) | ✅ | `test_policy.py` |
| Osobowość nie ma dostępu do `PolicyEngine` | ✅ | test strukturalny na sygnaturze |
| Pamięć szyfrowana w spoczynku | ✅ | `test_memory.py` czyta surowy plik |
| Sejf + podstawianie `vault://` w chwili wywołania | ✅ | `test_executor.py` |
| Wznawianie zadań po restarcie | ✅ | `test_tasks_resume.py` |
| Router modeli + łańcuch zapasowy | ✅ | `test_router.py` |
| Pętla agenta na atrapie modelu | ✅ | `test_agent_loop.py` |
| Brama powiadomień (cisza, gra, duplikaty, limit) | ✅ | `test_notifications.py` |
| Warstwa decyzyjna głosu (stany, słowo aktywacyjne, kalibracja) | ✅ | `test_voice.py` |
| Lokalne API HTTP + WebSocket | ✅ | `test_api.py` |
| **Narzędzia Windows** (rejestr, firewall, usługi, ekran, mysz, winget) | ❔ | deklarują platformę; nigdy nie wykonane na Windows |
| Audio (mikrofon, głośniki, STT, TTS) | ❌ | nie istnieje — etap 3 |

Weryfikacja: `pytest` 245 zielonych · `ruff` czysto · `mypy` czysto (60 plików).

## Interfejs (React/TypeScript)

| Obszar | Stan |
|---|---|
| Build produkcyjny, TypeScript strict | ✅ `npm run build` |

| Kula WebGL + fallback bez WebGL | 🔨 kompiluje się, nieoglądane w przeglądarce |
| Panele, karty zgód, onboarding | 🔨 |
| Animacje, Liquid Glass, `prefers-reduced-motion` | ❔ nikt tego nie zobaczył |

Interfejs nie był ani razu **wyświetlony**. Przechodzi kompilator, i tyle.

## Powłoka (Rust / Tauri 2)

| Obszar | Stan | Uwaga |
|---|---|---|
| Kompilacja pod Linux | ✅ | `cargo clippy -D warnings` |
| Kompilacja pod `x86_64-pc-windows-msvc` | ✅ | `cargo check` nie linkuje, ale kod `#[cfg(windows)]` przechodzi |
| Formatowanie | ✅ | `cargo fmt --check` |
| Tray: ikona, menu, klik | 🔨 | |
| `Ctrl+Alt+G` | 🔨 | do `8ccaac7` **nie kompilował się w ogóle** |
| Mica / szkło natywne | 🔨 | |
| Ukrycie zamiast zamknięcia | 🔨 | |
| Autostart | 🔨 | |
| Start silnika i odczyt tokenu | 🔨 | |
| Build produkcyjny (linkowanie) | ✅ | `panic=abort` + `lto` + `strip` zlinkowały się (Linux, release) |
| Sidecar silnika w paczce | ✅ | `externalBin`, sufiks triple zdjęty, `garis` obok powłoki |
| Start silnika z zainstalowanej lokalizacji | ✅ | proces potomny powłoki, `/api/state` odpowiada |
| Log silnika + redakcja tokenu | ✅ | `engine.log`, `Token: (pominięty w logu)` |
| Zabicie silnika przy wyjściu (tray) | 🔨 | `RunEvent::Exit` → `stop_engine`, nieklikalne w kontenerze |
| Job object (brak sierot na Windows) | 🔨 | kompiluje się pod target Windows, nieuruchomiony |
| `.msi` / NSIS | ❔ | nigdy nie zbudowane |

## Checkpoint Windows — niezaliczony

Z dziesięciu kroków weryfikacji zaliczone są 1–7. Kroki 8–10 i cały test ręczny
wymagają maszyny z Windows 11.

| # | Krok | Stan |
|---|---|---|
| 1 | `git status`, commit | ✅ |
| 2 | zależności | ✅ |
| 3 | testy Python | ✅ 245 |
| 4 | `ruff`, `mypy` | ✅ |
| 5 | build frontendu | ✅ |
| 6 | `cargo fmt --check` | ✅ |
| 7 | `cargo clippy -D warnings` | ✅ (oba targety) |
| 8 | produkcyjny build Tauri | ✅ na Linuksie · ⛔ na Windows |
| 9 | instalator | ✅ `.deb` · ⛔ `.msi` |
| 10 | instalacja lokalna | ✅ na Linuksie · ⛔ na Windows |

Test ręczny (pierwsze uruchomienie, okno, łączność z runtime, tray, przywrócenie,
`Ctrl+Alt+G`, lifecycle, autostart, logi) — **niewykonany w całości**.

Komendy do wykonania tych kroków: `docs/WINDOWS_CHECKPOINT.md`.
