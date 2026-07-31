# Stan projektu — co jest naprawdę sprawdzone

Commit `18e0b18`. Ten plik istnieje po to, żeby nikt nie pomylił „kompiluje się"
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
**Na Windows 11 nie sprawdzano niczego.** Sesja, która to pisała, nie miała
dostępu do maszyny z Windows, a GitHub Actions jest zablokowane przez billing.

---

## Silnik (Python)

| Obszar | Stan | Dowód |
|---|---|---|
| Runtime: `resolve → policy → zgoda → dzierżawa → wykonanie → audyt` | ✅ | 243 testy |
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

Weryfikacja: `pytest` 243 zielone · `ruff` czysto · `mypy` czysto (60 plików).

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
| Build produkcyjny (linkowanie) | ❔ | `check` nie linkuje — `panic=abort` + `lto` niesprawdzone |
| `.msi` / NSIS | ❔ | nigdy nie zbudowane |
| Instalacja i uruchomienie | ❌ | instalator nie wozi silnika — `KNOWN_ISSUES.md` §1 |

## Checkpoint Windows — niezaliczony

Z dziesięciu kroków weryfikacji zaliczone są 1–7. Kroki 8–10 i cały test ręczny
wymagają maszyny z Windows 11.

| # | Krok | Stan |
|---|---|---|
| 1 | `git status`, commit | ✅ |
| 2 | zależności | ✅ |
| 3 | testy Python | ✅ 243 |
| 4 | `ruff`, `mypy` | ✅ |
| 5 | build frontendu | ✅ |
| 6 | `cargo fmt --check` | ✅ |
| 7 | `cargo clippy -D warnings` | ✅ (oba targety) |
| 8 | produkcyjny build Tauri na Windows | ⛔ brak maszyny |
| 9 | instalator `.msi` | ⛔ brak maszyny |
| 10 | instalacja lokalna | ⛔ brak maszyny |

Test ręczny (pierwsze uruchomienie, okno, łączność z runtime, tray, przywrócenie,
`Ctrl+Alt+G`, lifecycle, autostart, logi) — **niewykonany w całości**.

Komendy do wykonania tych kroków: `docs/WINDOWS_CHECKPOINT.md`.
