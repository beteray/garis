# GARIS — instrukcje dla modeli pracujących nad tym repo

Przeczytaj `docs/HANDOFF.md` przed pierwszą zmianą. Zawiera stan projektu,
zasady architektury i pułapki, w które już wpadliśmy.

## Nienaruszalne zasady

1. Nic nie wykonuje się poza `Runtime.perform()`. Narzędzie wołające narzędzie
   używa `ctx.perform()`. Zero `subprocess` w agencie i zadaniach.
2. Efekty deklaruje `ToolSpec`, nie wołający.
3. Osobowość nie ma ścieżki do `PolicyEngine`.
4. Poświadczenia nigdy nie są pamięcią — zawsze sejf i `vault://`.
5. Warstwa niżej nie importuje wyższej (`models` nie wie o `agent`).
6. Awaria narzędzia to `ActionResult`; brak zgody to wyjątek.
7. Cisza domyślna: `ctx.progress()` do dziennika, `ctx.note()` wyjątkowo.
8. Raport buduje się z faktów (`agent/report.py`), nie z modelu.

Każda z nich ma test. Jeśli test się przewraca, to nie test jest zły.

## Praca

```bash
.venv/bin/python -m pytest -q      # musi być zielone przed commitem
.venv/bin/ruff check .
```

- Komentarze mówią **dlaczego**, nie **co**.
- Komunikaty dla użytkownika: polski, jedno zdanie, bez żargonu.
- Kod i docstringi: angielski.
- Nazwa testu to zdanie o gwarancji produktu, nie o implementacji.
- Commit opisuje powód zmiany; jeśli naprawia błąd — opisuje błąd.

## GUI (etap 2)

`docs/UI.md` to kontrakt, nie sugestia: Liquid Glass, framer-motion,
**wszystko animowane**, animowana kula ze stanami, `prefers-reduced-motion`
respektowane.
