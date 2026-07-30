# GARIS

Osobisty, autonomiczny agent AI dla Windows 11. Mówisz, **jaki rezultat** chcesz
uzyskać — GARIS sam wybiera metodę, wykonuje pracę, radzi sobie z błędami i wraca
ze sprawdzonym wynikiem.

Nie kolejny chatbot. Nie musisz wiedzieć, jakiego modelu, programu, API czy
komendy użyje. To nie Twoja sprawa — to jego.

```
garis do "sprawdź, ile miejsca zostało na dysku"
garis do "napraw problem z dźwiękiem"
garis do "zainstaluj i skonfiguruj nginx na serwerze"
garis do "przygotuj mi notatkę z tego projektu"
```

## Stan projektu

**Etap 1 ukończony:** silnik działa i jest przetestowany (177 testów).
**Etap 2 w toku:** lokalne API i interfejs gotowe i kompilujące się; powłoka
natywna (tray, Mica, autostart) napisana, czeka na kompilację na Windows.
Głos, serwer i mobile — `docs/ROADMAP.md`.

## Co już potrafi

- **Pętla agenta** — cel → plan → wykonanie → weryfikacja → naprawa → krótki raport.
  Nieudany krok próbuje inaczej: inne narzędzie, inna metoda, ponowne planowanie.
  Bez zawracania Ci głowy każdym błędem.
- **53 narzędzia** — pliki, PowerShell i terminal, procesy, usługi systemowe,
  rejestr, sieć i firewall, instalacja programów (winget/apt), ekran, mysz,
  klawiatura, schowek, okna, internet, zdalne serwery, pamięć, sejf.
- **Trwałe zadania** — mogą trwać godzinami. Przeżywają restart aplikacji
  i komputera, wznawiają się od miejsca przerwania, nie powtarzają wykonanej pracy.
  Wiele zadań równolegle; konflikt na tym samym pliku czy urządzeniu GARIS
  rozwiązuje sam.
- **Pamięć** — lokalna, szyfrowana, widoczna i edytowalna. Pamięta preferencje,
  projekty, urządzenia, sposoby rozwiązywania problemów. Hasła i klucze API nigdy
  nie są pamięcią — trafiają do osobnego sejfu.
- **Wybór modelu bez Ciebie** — OpenAI, Claude, Gemini, modele lokalne. Router
  decyduje na podstawie rodzaju pracy, jakości, szybkości, kosztu i prywatności.
  Awaria dostawcy → cicha zmiana na następnego.
- **Zabezpieczenia pod spodem** — nie pyta o zgodę na zwykłą pracę. Pyta przed
  płatnością, publikacją, wysłaniem wiadomości w Twoim imieniu, zmianą danych
  logowania i trwałym usunięciem danych. Uprzedza przed dużym pobraniem i kosztem.
- **Interfejs** — animowana kula pokazująca stan (słucham, myślę, pracuję, mówię,
  czekam, błąd), rozmowa, zadania na żywo, pamięć i sejf, urządzenia, ustawienia,
  diagnostyka. Liquid Glass, wszystko animowane. Zamknięcie okna nie zatrzymuje
  pracy — GARIS zostaje w zasobniku.

## Start

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"        # Windows: .venv\Scripts\pip

.venv/bin/garis doctor                   # co potrafię na tym komputerze
.venv/bin/garis do "pokaż, jakie procesy zajmują najwięcej pamięci"
```

Działa bez żadnego klucza API — z ograniczeniem: bez modelu GARIS wykona proste
rozpoznanie, a przy celu wymagającym realnych zmian powie to wprost, zamiast
udawać, że coś zrobił. Żeby odblokować pełne możliwości:

```bash
.venv/bin/garis vault set openai_api_key      # albo anthropic_api_key / gemini_api_key
```

Klucz trafia do zaszyfrowanego sejfu, nie do pliku konfiguracyjnego.

### Interfejs

```bash
.venv/bin/garis serve --print-token       # silnik + lokalne API
cd apps/desktop && npm install && npm run dev
```

Szczegóły i stan powłoki natywnej: `apps/desktop/README.md`.

## Polecenia

| Polecenie | Do czego |
|---|---|
| `garis do "<cel>"` | Zleć zadanie. `--dry-run` pokaże plan i skutki bez wykonania. `-v` pokaże postęp. |
| `garis tasks` / `garis task <id>` | Co się dzieje, szczegóły, kroki, `--audit` |
| `garis stop <id>` | Zatrzymaj zadanie |
| `garis approvals` / `approve` / `reject` | Co czeka na Twoją zgodę |
| `garis memory list\|search\|remember\|forget` | Co GARIS pamięta — i zapomnij |
| `garis vault list\|set\|delete` | Sejf haseł i kluczy |
| `garis activity --minutes 60` | „Co robiłeś przez ostatnią godzinę?" |
| `garis doctor` | Diagnostyka: narzędzia, modele, bramki zgody, stan |
| `garis serve` | Praca w tle (tak jak później w zasobniku systemowym) |

## Jak to jest zbudowane

Sercem jest **jedna kontrolowana ścieżka wykonania**. Model AI może proponować
plan, ale nigdy nie dotyka systemu bezpośrednio. Każda akcja przechodzi te same
bramki:

```
resolve → walidacja → polityka → zgoda → dzierżawa zasobu → wykonanie → audyt
```

Dzięki temu „pełny dostęp do komputera" jest do obrony: wszystko jest zadeklarowane,
sprawdzone i zapisane. Szczegóły: `docs/ARCHITECTURE.md`, zasady bezpieczeństwa:
`docs/SAFETY.md`.

## Dokumentacja

| Plik | Zawartość |
|---|---|
| `docs/ARCHITECTURE.md` | Warstwy, droga akcji, trwałość, współbieżność, szyfrowanie |
| `docs/SAFETY.md` | Co bez pytania, co za zgodą, czego GARIS nie zrobi nigdy |
| `docs/ROADMAP.md` | Etapy 1–8 z kryteriami ukończenia |
| `docs/HANDOFF.md` | Stan projektu, zasady, pułapki, następny krok |
| `docs/TOOLS.md` | Jak dodać własną zdolność |
| `docs/API.md` | Protokół HTTP + WebSocket dla interfejsu, mobile i serwera |
| `docs/UI.md` | Kontrakt wizualny: Liquid Glass, animacje, stany kuli |

## Rozwój

```bash
.venv/bin/python -m pytest -q      # 177 testów
.venv/bin/ruff check .
.venv/bin/mypy

cd apps/desktop && npm run build   # TypeScript strict + Vite
```

Testy opisują gwarancje produktu, nie implementację: że zwykła praca nie pyta
o pozwolenie, że płatność zawsze pyta, że pamięć nie zostawia jawnego tekstu na
dysku, że zadanie wznawia się po restarcie bez powtarzania kroków.

## Licencja

MIT — `LICENSE`.
