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
| Runtime: `resolve → polityka → zgoda → dzierżawa → efekt → wykonanie → dowody → weryfikacja → zapis → zdarzenie` | ✅ | 553 testów |
| **Jedna koperta wykonania** — narzędzie i zdolność wchodzą w to samo `CapabilityRunner.run` | ✅ | `test_runner.py` — test strukturalny czyta źródło `Runtime.perform` |
| Jedna decyzja polityki, jeden wpis audytu, jeden efekt na wywołanie | ✅ | `test_runner.py` — liczniki na prawdziwym `PolicyEngine` |
| Odmowa polityki i odmowa użytkownika nie zostawiają śladu w świecie | ✅ | `test_effect_safety.py` |
| Efekt zdarza się raz; sukces jest odtwarzany, nie powtarzany | ✅ | `test_effect_safety.py`, `test_effects.py` |
| „Nie udało się" ≠ „nic się nie stało" — 4 dyspozycje efektu | ✅ | `test_effect_safety.py` — wyjątek nigdy nie dowodzi braku skutku |
| Sprawdzona porażka celu nie zmienia się w sukces przy odtworzeniu | ✅ | `test_effect_safety.py` — pełny werdykt zapisany i odtwarzany |
| Rozliczenie efektu, audyt i zdarzenie w jednej transakcji | ✅ | `test_runner.py`, `test_effects.py` |
| PRODUCTION odmawia startu z atrapą dostawcy albo zdolnością testową | ✅ | `test_runner.py` — po tożsamościach, nie po liczbie |
| Bramki zgody (płatność, publikacja, wiadomość, poświadczenia, trwałe usunięcie) | ✅ | `test_policy.py` |
| Osobowość nie ma dostępu do `PolicyEngine` | ✅ | test strukturalny na sygnaturze |
| Pamięć szyfrowana w spoczynku | ✅ | `test_memory.py` czyta surowy plik |
| Sejf + podstawianie `vault://` w chwili wywołania | ✅ | `test_executor.py` |
| Wznawianie zadań po restarcie | ✅ | `test_tasks_resume.py` |
| Router modeli + łańcuch zapasowy | ✅ | `test_router.py` |
| Stan dostawcy mierzony, nie zakładany (7 statusów) | ✅ | `test_provider_health.py` |
| Klucz zapisany przez API działa bez restartu | ✅ | `test_reload.py` — zadanie kończy się wynikiem |
| Nieprawidłowy klucz zgłasza się przy zapisie, nie w środku zadania | ✅ | `test_reload.py` |
| Ustawienia zmieniane na żywo, atomowo, ze zdarzeniem | ✅ | `test_reload.py` |
| Health check przeciw **prawdziwym** dostawcom (OpenAI/Anthropic/Gemini) | ❔ | klasyfikacja przetestowana na atrapie transportu; żaden prawdziwy klucz nie był użyty |
| Pętla agenta na atrapie modelu | ✅ | `test_agent_loop.py` |
| Brama powiadomień (cisza, gra, duplikaty, limit) | ✅ | `test_notifications.py` |
| Warstwa decyzyjna głosu (stany, słowo aktywacyjne, kalibracja) | ✅ | `test_voice.py` |
| Lokalne API HTTP + WebSocket | ✅ | `test_api.py` |
| CORS dla okna Tauri | ✅ | `test_api.py` — bez tego okno nie łączy się wcale |
| **Narzędzia Windows** (rejestr, firewall, usługi, ekran, mysz, winget) | ❔ | deklarują platformę; nigdy nie wykonane na Windows |
| Audio (mikrofon, głośniki, STT, TTS) | ❌ | nie istnieje — M13 |
| Klient MCP | ❌ | nie istnieje — ani jednej linii |
| Pluginy / rozszerzenia zewnętrzne | ❌ | nie istnieje; `paths.py:90` tworzy pusty katalog bez czytelnika |
| Scheduler / zadania cykliczne | ❌ | nie istnieje — świadomie, patrz `nav.ts:11` |
| Wyszukiwanie wektorowe w pamięci | ❌ | nie istnieje — świadomie, patrz `memory.py:5` |
| Kamera, OCR, czytanie ekranu | ❌ | jest tylko zrzut ekranu (`screen_capture`) |
| Sterowanie przeglądarką | ❌ | jest `web_fetch` i wyszukiwanie; brak automatyzacji |
| Dostawcy lokalni poza Ollamą (llama.cpp, LM Studio) | ❌ | brak dostawcy zgodnego z OpenAI |

Ostatnie osiem wierszy jest tu po to, żeby brak był **widoczny w tej samej
tabeli**, co reszta. Podsystem nieopisany nigdzie wygląda jak podsystem gotowy.

Weryfikacja: `pytest` 553 zielonych (1 pominięty) · `ruff` czysto ·
`mypy` czysto (80 plików).

**Przeładowanie i zdrowie dostawców sprawdzone też na żywo**, nie tylko testami:
uruchomiony `garis serve`, prawdziwe gniazda, atrapa Gemini na `127.0.0.1`
odpowiadająca 400 na zły klucz i 200 na dobry. Przebieg, bez ani jednego
restartu silnika:

1. przed zapisaniem klucza widoczna wyłącznie wbudowana atrapa,
2. `POST /api/vault` ze złym kluczem → `invalid_key`, „Klucz odrzucony przez
   dostawcę." **w odpowiedzi na zapis**,
3. zadanie zlecone przy złym kluczu kończy się przez wbudowany fallback zamiast
   umierać,
4. poprawiony klucz → `online`, 3,4 ms, natychmiast,
5. `PATCH /api/config` widoczny w `/api/state` od razu,
6. `config.json` **edytowany ręcznie z zewnątrz** dotarł do silnika w ~2 s.

Czego to nie dowodzi: że treść odpowiedzi prawdziwego OpenAI, Anthropica i
Google mapuje się tak, jak zakłada `classify_response`. Kody HTTP owszem,
komunikaty w ciele — nie sprawdzone na żadnym prawdziwym kluczu.

## Interfejs (React/TypeScript)

Interfejs został wreszcie **wyświetlony i wyklikany** — `apps/desktop/tools/ui-probe.mjs`
otwiera go w prawdziwym Chromium przeciwko żywemu silnikowi. Pierwsze uruchomienie
znalazło przyczynę wszystkich objawów z testu na Windows: brak CORS.

| Obszar | Stan |
|---|---|
| Build produkcyjny, TypeScript strict | ✅ `npm run build` |
| Połączenie z runtime | ✅ status `v0.1.0`, `/api/state` czytane przez okno |
| Nawigacja — wszystkie sekcje | ✅ każda klikalna, sonda przechodzi po kolei |
| Zapis klucza modelu (Gemini) | ✅ zapisany, w sejfie, zaszyfrowany, przetrwał przeładowanie |
| Brak przepełnienia układu | ✅ 900×620, 1160×760, 1280×700, 1920×1080 |
| Onboarding | ✅ pojawia się i daje się przejść |
| Stany połączenia + restart silnika + kopiowanie diagnostyki | 🔨 napisane, ścieżka Tauri nieuruchomiona |
| Kula WebGL + fallback | 🔨 |
| Liquid Glass — warstwy, aurora, ziarno | 🔨 przebudowane, oglądane na zrzutach, nie na Windows |
| Skalowanie Windows 125/150/175% | ❔ |

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
| 3 | testy Python | ✅ 553 |
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
