# Lokalne API

Jedyna droga do silnika z zewnątrz procesu. Mówią nim: GUI (Tauri), GARIS Mobile
(przez przekaźnik) i drugi agent, gdy desktop przekazuje cel serwerowi. Jeden
protokół, więc klient napisany dla desktopu działa z agentem serwerowym bez zmian.

Uruchomienie:

```bash
garis serve --print-token          # domyślnie http://127.0.0.1:8756
garis token                        # sam token, gdy serwer już działa
```

## Zasady

- **Tylko loopback.** `127.0.0.1`. Dostęp z telefonu idzie przez przekaźnik, nie
  przez wystawienie portu na sieć.
- **Token w każdym żądaniu.** `Authorization: Bearer <token>`. Token powstaje przy
  pierwszym uruchomieniu i leży w **sejfie**, nie w pliku konfiguracyjnym — token
  w configu to token w każdej kopii zapasowej.
- **Wyjątek: `/api/health`.** Bez tokenu, żeby interfejs mógł sprawdzić, czy silnik
  żyje. Zwraca wyłącznie `{"ok": true, "protocol": 1}` — nic o użytkowniku.
- **API tłumaczy, nie decyduje.** Każde żądanie idzie przez te same obiekty, co CLI
  (`tasks.submit`, `approvals.resolve`, `memory.remember`), więc nie istnieje druga,
  słabsza droga do runtime. Bramki zgody obowiązują identycznie.
- **Wartości sekretów nigdy nie wychodzą.** `/api/vault` zwraca nazwy i metadane.

## Zasoby

| Metoda | Ścieżka | Do czego |
|---|---|---|
| GET | `/api/health` | Żywotność (bez tokenu) |
| GET | `/api/state` | Wszystko, co potrzebne po otwarciu okna — jeden przelot |
| GET | `/api/tasks` | `?state=` `?active=true` `?limit=` |
| POST | `/api/tasks` | `{goal, criteria?, target?, origin?}` → `{task_id, id, …}` |
| GET | `/api/tasks/{id}` | Szczegóły + `steps` + `approvals` + `plan` + `question` |
| POST | `/api/tasks/{id}/stop` | Zatrzymanie (kooperatywne, między krokami) |
| POST | `/api/tasks/{id}/resume` | Wznowienie zablokowanego zadania |
| GET | `/api/approvals` | Co czeka na zgodę |
| POST | `/api/approvals/{id}` | `{approved: bool, by?}` — **i wznawia zadanie** |
| GET | `/api/memory` | `?query=` `?kind=` `?limit=` + `stats` |
| POST | `/api/memory` | `{content, kind?, subject?, tags?, scope?}` |
| PATCH | `/api/memory/{id}` | `{content?, subject?, tags?, pinned?}` |
| DELETE | `/api/memory/{id}` | Zapomnij |
| GET | `/api/vault` | Nazwy dostępów, bez wartości |
| POST | `/api/vault` | `{name, value, note?}` → `{ref}` |
| DELETE | `/api/vault/{name}` | Usuń dostęp |
| GET | `/api/devices` | Znane komputery i serwery |
| GET | `/api/tools` | Katalog zdolności z deklarowanymi efektami |
| GET | `/api/activity` | `?minutes=` — „co robiłeś przez ostatnią godzinę?" |
| GET | `/api/audit` | `?task=` `?limit=` |
| GET | `/api/config` | Pełna konfiguracja |
| PATCH | `/api/config` | `{"voice.wake_word": "garis", "dev.verbose": true}` — klucze kropkowane |

Kody: `200`, `201` (utworzono), `401` (brak/zły token), `404`, `409` (np. sekret
wysłany do pamięci — z podpowiedzią, że miejsce na to jest w sejfie), `422`
(brak wymaganego pola), `400` (błąd domenowy, z czytelnym zdaniem w `error`).

Błąd zawsze wygląda tak samo: `{"error": "<zdanie dla człowieka>", "detail": "…"}`.

## Strumień zdarzeń

```
ws://127.0.0.1:8756/ws?token=<token>
```

Token w query, bo przeglądarka nie ustawi nagłówka przy handshake WebSocketa.
Ramka wygląda tak:

```json
{"v": 1, "topic": "task.step", "at": 1785400000.12, "data": {"task_id": "…", "tool": "disk_usage"}}
```

**Pierwsza ramka po połączeniu to `state`** z tą samą treścią co `GET /api/state` —
klient nigdy nie musi czekać na kolejne zdarzenie, żeby wiedzieć, co się dzieje.

Tematy: `task.created`, `task.started`, `task.step`, `task.progress`,
`task.finished`, `task.failed`, `task.blocked`, `task.stopped`,
`approval.requested`, `approval.resolved`, `agent.state`, `voice.state`,
`notice`, `speak`, `memory.changed`, `subscription.item`.

`agent.state` (`thinking` / `working` / `verifying` / `blocked` / `done` /
`failed`) i `voice.state` (`listening` / `speaking`) napędzają animowaną kulę w
interfejsie — to one decydują, w jaki stan przechodzi wizualizacja.

Wolny klient nie zatrzyma silnika: kolejka gubi najstarsze zdarzenie, nie blokuje
producenta. Nieprawidłowa ramka (np. niezamaskowana) kończy połączenie, serwer
działa dalej.

## Przykład

```bash
TOKEN=$(garis token)
curl -s -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"goal":"sprawdź, ile miejsca zostało na dysku"}' \
     http://127.0.0.1:8756/api/tasks
```

## Zgodność

`protocol` w `/api/state` i w każdej ramce to wersja kontraktu. Zmiana
niekompatybilna podnosi numer; dopisanie pola albo tematu — nie. Klient powinien
sprawdzić `protocol` przy starcie i ignorować nieznane tematy.
