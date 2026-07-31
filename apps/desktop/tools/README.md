# ui-probe

Otwiera interfejs w prawdziwej przeglądarce, klika w niego i robi zrzuty.

Powstało, bo przez cały etap 2 nikt tego interfejsu **nie zobaczył** — przechodził
kompilator i to wszystko. Pierwsze uruchomienie znalazło w minutę: brak CORS
(przez co okno nigdy się nie łączyło), przepełnienie układu i to, że panele
wyglądają jak szare kafle.

Działa, bo `discover()` przyjmuje `?base=` i `?token=` — w przeglądarce nie ma
powłoki Tauri, ale reszta aplikacji jest ta sama.

## Użycie

```bash
# 1. silnik
GARIS_HOME=/tmp/ui .venv/bin/garis serve --print-token --port 8790 &

# 2. zbudowany interfejs
cd apps/desktop && npm run build && npx vite preview --port 4173 &

# 3. sonda
API_BASE=http://127.0.0.1:8790 \
API_TOKEN=$(GARIS_HOME=/tmp/ui ../../.venv/bin/garis token) \
SHOTS=/tmp/shots \
node tools/ui-probe.mjs
```

Raportuje: błędy konsoli i sieci, stan połączenia, co przechwytuje kliknięcia
(`elementFromPoint` — tak namierza się niewidoczne nakładki), czy nawigacja
reaguje, czy da się zapisać klucz, i czy przy czterech rozmiarach okna nie ma
przepełnienia.

Zrzuty lądują w `$SHOTS`. **Obejrzyj je** — „brak przepełnienia" nie znaczy, że
wygląda dobrze.

## Czego to nie sprawdza

Wszystkiego, co jest po stronie Tauri: `invoke`, tray, Mica, skrót globalny,
autostart. Do tego trzeba Windows i `docs/WINDOWS_CHECKPOINT.md`.
