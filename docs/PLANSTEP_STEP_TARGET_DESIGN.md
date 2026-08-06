# PlanStep → StepTarget — dokument projektowy migracji

Status: **Etapy A, B i C wykonane.** Migracja zamknięta. Ten plik powstał przed
commitem 4 po to, żeby kontrakt był ustalony, zanim struktury zaczną się zmieniać
w siedmiu miejscach naraz — i po ustaleniach §3.3 i §3.4 pozostaje wiążący.

| Etap | Stan | Gdzie |
|---|---|---|
| A — model i zgodność | ✅ | `tests/test_step_target.py` |
| B — przepięcie decyzji (reflex → verify → report → planner → CLI) | ✅ | `tests/test_target_resolver.py` |
| C — usunięcie starego | ✅ | `tests/test_capabilities.py`, `tests/test_step_target.py` |

Decyzje właściciela projektu, przyjęte przed Etapem A:

- **§3.3** — cienka warstwa nad `ResolvedTarget` (`runtime/resolve.py`),
  **nie** wystawiony pierwszy krok runnera. Gdyby planner wołał do runnera,
  kolejność szesnastu kroków stałaby się kontraktem publicznym i zmiana kroku 4
  mogłaby przewrócić walidację planu. Żadnych osobnych implementacji w plannerze
  i w CLI.
- **§3.4** — zgodność przejściowa: API na zewnątrz trzyma `tool: str`, silnik w
  środku przechodzi na `StepTarget` i **głośno** przewraca się na ukrytym użyciu
  starej ścieżki. Etap C zdjął głośną właściwość razem z resztą przejściowego
  rusztowania: nie ma czego wyciszać, gdy nie ma po czym kluczować.
- **`Action.target` nie powstaje.** `Action.tool` zostaje źródłem tożsamości
  także dla zdolności.

Numeracja etapów (A/B/C) jest wiążąca — kolejność wynika z zależności, nie z
wygody.

---

## 0. Najważniejsze ustalenie na wejściu

**`StepTarget` już istnieje.** `kernel/contracts.py:505-556` definiuje
`ToolTarget`, `CapabilityTarget`, alias `StepTarget` oraz parę
`target_from_dict` / `target_to_dict`. Powstały w commicie 3, bo `CapabilityRunner`
musiał przyjmować obie rzeczy jednym wejściem.

Commit 4 nie polega więc na *zaprojektowaniu* modelu. Polega na **podłączeniu
istniejącego modelu do planu, dziennika i weryfikacji**. To zawęża zakres i
zmienia listę ryzyk: ryzykiem nie jest zły kształt `StepTarget`, tylko to, co się
stanie z siedmioma miejscami, które dziś czytają `str`.

```python
# kernel/contracts.py:526 — już w repo, przetestowane
StepTarget = ToolTarget | CapabilityTarget
```

`target_from_dict` (`contracts.py:529-547`) wymusza już regułę „dokładnie jedno z
`tool` / `capability`" i rzuca `CapabilityError(Failure.INVALID_INPUT)`, gdy
model wypełni oba. To zachowanie jest gotowe i **nie wolno go rozluźnić** przy
podłączaniu do `Plan.from_dict` — patrz §5, ryzyko R4.

---

## 1. Mapa użycia `PlanStep.tool` — stan faktyczny

Zweryfikowane `grep`-em po `core/src/garis`, nie z pamięci.

### 1.1 Definicja

| Plik | Linia | Co |
|---|---|---|
| `agent/goal.py` | 42 | `tool: str` — pole |
| `agent/goal.py` | 51 | `to_dict()` — klucz `"tool"` |
| `agent/goal.py` | 63 | `from_dict()` — `str(data.get("tool", ""))` |

### 1.2 Czytelnicy `PlanStep.tool` (7 miejsc, 4 moduły)

| Plik:linia | Użycie | Co się stanie po migracji |
|---|---|---|
| `agent/loop.py:170` | `StepEvidence(tool=step.tool, …)` — krok wznowiony | → `target=` |
| `agent/loop.py:217` | `StepEvidence(tool=step.tool, …)` — krok udany | → `target=` |
| `agent/loop.py:232` | `failures.append(f"{step.tool}: …")` — tekst dla plannera | → `target.name` |
| `agent/loop.py:236,273` | `StepEvidence(step.tool, …)` — pominięty / nieudany | → `target=` |
| `agent/loop.py:316` | `Action(tool=step.tool, …)` | **rdzeń zmiany** — §3 |
| `agent/loop.py:327` | `bus.emit(Topic.TASK_STEP, tool=step.tool, …)` | payload zdarzenia — §4.3 |
| `agent/planner.py:201-213` | `_sanitise` — walidacja wobec `ToolRegistry` | **rdzeń zmiany** — §3.3 |
| `agent/planner.py:232` | `describe_plan` — tekst dla `--dry-run` | → `target.name` |
| `agent/reflex.py:456` | `PlanStep(tool=reflex.tool, …)` | → `target=ToolTarget(...)` |
| `tasks/supervisor.py:46` | `store.step_started(…, step.tool, step.params)` | schemat bazy — §4.1 |
| `cli.py:85-86` | podgląd skutków w `--dry-run` | → rozgałęzienie po typie |

### 1.3 Czytelnicy `StepEvidence.tool` (drugi, osobny łańcuch)

`StepEvidence` (`agent/verify.py:59-75`) niesie `tool: str` niezależnie od
`PlanStep`. To **osobna migracja o tym samym kształcie** i nie da się jej
odłożyć, bo `verify` i `report` kluczują po tym polu:

| Plik:linia | Użycie | Waga |
|---|---|---|
| `agent/verify.py:95-96` | tekst powodu i `unmet` | kosmetyka |
| `agent/verify.py:128` | payload `"tool"` dla modelu weryfikującego | kontrakt promptu |
| `agent/verify.py:184-186` | **`reflex.check(e.tool, e.value)`** | **krytyczne — R1** |
| `agent/report.py:65,104` | linie raportu | kosmetyka |
| `agent/report.py:123` | `developer["steps"][]["tool"]` | kontrakt API |
| `agent/report.py:186` | **`reflex.answer(item.tool, item.value)`** | **krytyczne — R1** |

### 1.4 Trzeci łańcuch: `Action.tool`

Tu leży prawdziwe sprzężenie i najgłębsze pytanie projektowe.

`Action.tool: str` (`runtime/action.py:30`) jest czytane przez:
`approvals.py:113,128,141` (treść pytania o zgodę), `audit.py:124,197` (wiersz
audytu i statystyki), `action.py:52` (**`fingerprint()`** — dopasowanie zgody do
ponowienia po restarcie), `runner.py:417,595,636,753,768,892,906` (payloady
zdarzeń i audyt), `executor.py:109,124,136`.

**Zdolności już dziś wpisują się w to pole.** `capabilities/registry.py:169-173`
buduje `Action(tool=capability.id, …)`, czyli `Action.tool` przenosi już nazwy
typu `windows.process.list`. Przestrzeń nazw jest zatem **już wymieszana i już
nośna** — audyt i odciski zgód opierają się na niej od commitu 3.

To ma konsekwencję, którą trzeba zapisać wprost: **`Action.tool` nie może zmienić
znaczenia w commicie 4**, bo `fingerprint()` jest zapisany w bazie zgód. Zmiana
tego pola unieważniłaby zapisane zgody i kazała użytkownikowi potwierdzać
drugi raz coś, na co już się zgodził.

---

## 2. Proponowany model

### 2.1 `PlanStep`

```python
@dataclass(slots=True)
class PlanStep:
    key: str
    target: StepTarget
    params: dict[str, Any] = field(default_factory=dict)
    purpose: str = ""
    optional: bool = False
    expects: str = ""

    @property
    def name(self) -> str:
        """Nazwa do prozy, logów i tekstu dla modelu."""
        return self.target.name
```

`target` jest polem wymaganym i **nie ma wartości domyślnej**. Krok bez celu nie
jest krokiem, a domyślny `ToolTarget("")` przeszedłby cicho przez `_sanitise` i
umarł dopiero w runnerze.

### 2.2 Serializacja

`to_dict()` → `{"key":…, **target_to_dict(self.target), "params":…, …}`, czyli
**oba klucze zawsze obecne, dokładnie jeden wypełniony** — dokładnie tak, jak
opisuje `target_to_dict` (`contracts.py:549-554`). Format przyszły i format
dzisiejszy różnią się wtedy tylko obecnością pustego `"capability": ""`, co
starszy czytelnik zignoruje.

`from_dict()` → `target=target_from_dict(data)`, z jednym wyjątkiem opisanym w
R4.

### 2.3 `StepEvidence`

```python
target: StepTarget
@property
def name(self) -> str: ...     # etykieta; `.tool` zniknął w Etapie C
```

### 2.4 Czego świadomie **nie** wprowadzamy

- **Nie** dodajemy `Action.target`. `Action.tool` już niesie identyfikator obu
  rodzajów, a `fingerprint()` jest trwały. Dublowanie tej informacji dałoby dwa
  źródła prawdy o tym, co się wykonuje — dokładnie ten błąd, który commit 3
  usuwał z warstwy wykonania.
- **Nie** wprowadzamy trzeciego wariantu `StepTarget` (np. `RemoteTarget`).
  Zdalność to `Goal.target` / `Action.target: str` i to jest inny wymiar:
  *gdzie*, nie *co*.
- **Nie** ruszamy `Plan.origin`. Reguła z `goal.py:108-112` — „plan sparsowany z
  JSON-a modelu jest planem modelu, cokolwiek JSON twierdzi" — obowiązuje tak
  samo dla nowego pola.

---

## 3. Zmiany w rdzeniu, po kolei

### 3.1 `Runtime.perform_step`

```python
async def perform_step(self, step: PlanStep, action: Action,
                       context: ExecutionContext | None = None) -> ActionResult:
    return self._translate(action, await self.runner.run(step.target, action, ...))
```

`Runtime.perform` zostaje bez zmian jako fasada dla `ToolTarget`. Nowa metoda to
**druga fasada nad tą samą kopertą**, nie druga ścieżka — test strukturalny z
commitu 3 (`test_runner.py`) trzeba rozszerzyć tak, by pilnował obu.

### 3.2 `AgentLoop._run_step`

Jedna linia zmiany istoty: `self.runtime.perform(action)` →
`self.runtime.perform_step(step, action)`. Reszta pętli (backoff, `retryable`,
dziennik) zostaje.

### 3.3 `Planner._sanitise` — najbardziej podstępne miejsce

Dziś (`planner.py:200-213`) walidacja pyta wyłącznie `ToolRegistry`:
`has()`, `supported_here()`, `spec.validate(params)`. Dla `CapabilityTarget`
żadna z tych metod nie istnieje.

Potrzebny jest **jeden protokół rozstrzygania dla obu rodzajów** — inaczej
`_sanitise` urośnie w `if isinstance(...)` i to samo rozgałęzienie pojawi się
później w `cli.py` i w `describe_plan`. Propozycja: mała funkcja rozstrzygająca w
warstwie runtime (nie w `agent/`, bo agent nie może sięgać do dwóch rejestrów):

```python
def resolve_step(target: StepTarget) -> StepResolution   # known / runs_here / validate(params)
```

`CapabilityRunner` już ma taki krok (`resolve` → `Resolved`, `runner.py`), więc
prawdopodobnie chodzi o **udostępnienie tego, co runner robi w kroku 1**, a nie o
napisanie nowego kodu. Do rozstrzygnięcia w Etapie B; jest to jedyne miejsce, w
którym spodziewam się realnego projektowania, a nie przepisywania.

### 3.4 Zgodność `.tool` — decyzja do podjęcia

Trzy warianty i ich cena:

| Wariant | Zachowanie dla `CapabilityTarget` | Cena |
|---|---|---|
| A. `.tool` → `target.name` | zwraca `windows.process.list` | **cicha katastrofa** — `reflex.check` dostaje nazwę, której nie zna, i zwraca „nie umiem sprawdzić", co przewraca `goal_met` na `False`. Zielone testy, fałszywe raporty. |
| B. `.tool` → `""` | pusty string | głośniej, ale nadal błędnie: `reflex.check("")` też daje `goal_met=False` |
| C. `.tool` → `raise` | wyjątek przy dostępie | najgłośniej; wymusza migrację wszystkich czytelników w Etapie B |

**Rekomendacja: C** dla kodu wewnętrznego i **A tylko w warstwie serializacji**
(API/dziennik), gdzie odbiorcą jest frontend, który ma nie widzieć różnicy.
Uzasadnienie: wariant A wewnątrz silnika jest dokładnie tym rodzajem błędu,
którego szuka `docs/CURRENT_STATE.md` — „kompiluje się" i „daje złą odpowiedź"
wyglądają identycznie.

---

## 4. Trwałość i kontrakty zewnętrzne

### 4.1 Baza — migracja 5

`task_steps.tool TEXT NOT NULL` (`store.py:55`). Migracja addytywna, w stylu
migracji 4:

```sql
ALTER TABLE task_steps ADD COLUMN capability TEXT NOT NULL DEFAULT '';
```

Istniejące wiersze pozostają narzędziami — i to jest prawda, a nie wygodne
założenie: w chwili ich zapisu żadna zdolność nie mogła trafić do planu.

`tool` **nie może** dostać `NOT NULL DEFAULT ''` zdjętego ani zostać usunięte w
commicie 4. SQLite i tak nie usuwa kolumn bezboleśnie, a `StepRecord.to_dict()`
(`tasks/models.py:150-161`) jedzie prosto do API.

### 4.2 API i frontend — zero zmian po stronie okna

`api/protocol.py:86-87` (`step_view`) zwraca `step.to_dict()` bez tłumaczenia.
Frontend czyta klucz `tool` — sprawdzone w
`apps/desktop/src/components/TaskCard.test.tsx:44-46`.

**Warunek nienegocjowalny: `StepRecord.to_dict()` nadal zawiera klucz `tool` ze
stringiem.** Dla zdolności wypełniamy go jej identyfikatorem (wariant A z §3.4 —
tu jest właściwy, bo odbiorca chce etykiety, nie klucza logiki) i dokładamy
`capability`. Frontend nie wymaga zmiany, zgodnie z ograniczeniem projektu.

To samo dotyczy `report.py:123` (`developer["steps"][]["tool"]`).

### 4.3 Zdarzenia

`Topic.TASK_STEP` niesie dziś `tool=step.tool` (`loop.py:327`). Runner już
publikuje **oba** pola — `"tool": action.tool` i `"target": target.name`
(`runner.py:594-596`). Pętla powinna dopasować się do wzoru runnera, nie
odwrotnie: dołożyć `target`, zostawić `tool`.

---

## 5. Ryzyka

### R1 — reflex kluczowany po stringu (najwyższe)

`agent/reflex.py:406`: `BY_TOOL: dict[str, Reflex] = {r.tool: r for r in REFLEXES}`.
`check()` i `answer()` przyjmują `tool: str` i przy nieznanej nazwie zwracają
odpowiednio komunikat błędu i `""`.

Skutek: **przepięcie jednego kroku na zdolność bez przepięcia reflexu zamienia
zweryfikowany sukces w porażkę** — cicho, bez wyjątku, z zielonym pakietem
testów, bo dziś żaden test nie stawia zdolności w planie.

Zabezpieczenie — **bramka sekwencyjna**: żadna zdolność nie może być osiągalna z
plannera ani z `reflex.plan_for`, dopóki `verify`, `report` i `reflex` nie
kluczują po `StepTarget`. To jest powód, dla którego Etap C nie może wyprzedzić
Etapu B, i powód, dla którego przepięcie reflexów (`process_find` →
`windows.process.list`) pozostaje w commicie 5, a nie w 4.

### R2 — `fingerprint()` i zapisane zgody

`Action.fingerprint()` (`action.py:46-57`) miesza `tool`, `params`, `target` i
jest zapisany w bazie zgód. Każda zmiana `Action.tool` unieważnia zgody
oczekujące na odpowiedź użytkownika. **Mitygacja: nie ruszać `Action` w commicie
4.** Test: zgoda zapisana przed migracją nadal dopasowuje się po niej.

### R3 — dwa łańcuchy, jedna migracja

`PlanStep.tool` i `StepEvidence.tool` to niezależne pola. Zmigrowanie tylko
pierwszego zostawia `verify.py` i `report.py` na stringach, a `loop.py` w roli
tłumacza — czyli dokładnie ten stan pośredni, którego ten dokument ma uniknąć.
**Oba w Etapie B albo żadne.**

### R4 — model emitujący `tool` i `capability` naraz

`target_from_dict` rzuca `CapabilityError`. `Plan.from_dict` dziś **nie rzuca
niczego** — jest odporny na śmieci z modelu z założenia (`goal.py:99-113`).

Decyzja: `Plan.from_dict` **łapie** `CapabilityError` z pojedynczego kroku,
pomija ten krok i dopisuje powód do `plan.notes` — spójnie z tym, co
`_sanitise` robi z nieznanym narzędziem (`planner.py:202`). Wyjątek z parsowania
planu wywróciłby całe zadanie z powodu jednej halucynacji, a to nie jest
zachowanie, którego chcemy od plannera.

Uwaga: to jest jedyne miejsce, gdzie *świadomie* rozluźniamy kontrakt
`target_from_dict`, i tylko w jednym kierunku — wewnątrz silnika, przy odczycie
wiersza z bazy, twardość zostaje.

### R5 — planner nie wie o zdolnościach

`SYSTEM_PROMPT` (`planner.py:24-58`) opisuje wyłącznie
`{"tool": "nazwa_narzedzia"}` i katalog budowany z `ToolRegistry`. Dopóki to się
nie zmieni, **model nigdy nie wyprodukuje kroku ze zdolnością** — co jest dobrą
wiadomością dla commitu 4: cała migracja może przejść przy zerowym ruchu w
zachowaniu, a zdolności w planach włączamy osobno, po R1.

### R6 — `cli.py --dry-run`

`cli.py:85-86` liczy skutki wyłącznie z `ToolRegistry`. Dla zdolności podgląd
pokazałby *pusty* zestaw skutków, czyli „nic nie dotknie" o operacji, która
dotyka. Skutek jest kosmetyczny dziś, groźny po R5. Do domknięcia razem z §3.3.

---

## 6. Etapy

### Etap A — model i zgodność, zero zmian zachowania ✅

Zrobione. Odstępstwa od planu, które wyszły dopiero przy pisaniu kodu:

- `Runtime.perform_step` przyjmuje `StepTarget`, **nie** `PlanStep` — `runtime`
  leży poniżej `agent` i nie wolno mu go zaimportować. `Runtime.perform` jest
  teraz napisana *przez* `perform_step`, więc kopertę woła dokładnie jedno
  miejsce; test strukturalny tego pilnuje.
- `StepRecord.tool` **nie** jest głośne. Wiersz dziennika to etykieta dla
  człowieka i nic w silniku po niej nie rozgałęzia; głośna wersja zepsułaby
  wyłącznie listę zadań. Głośne są `PlanStep.tool` i `StepEvidence.tool` — tam,
  gdzie kluczuje logika.
- `Plan.from_dict` dostał obsługę odrzuconych kroków (R4) już w Etapie A, bo
  `target_from_dict` rzuca także na kroku pustym, a wcześniej `tool=""`
  przechodziło do `_sanitise`.


- `PlanStep.target: StepTarget` + `to_dict` / `from_dict` przez
  `target_to_dict` / `target_from_dict`
- `StepEvidence.target: StepTarget`
- migracja 5: `task_steps.capability`
- `StepRecord.target`, `to_dict()` nadal z kluczem `tool`
- warstwa zgodności wg §3.4 (C wewnątrz, A na wyjściu)
- **testy:** plan zapisany w starym formacie wczytuje się; wiersz `task_steps`
  sprzed migracji nadal wznawia zadanie; `step_view` nadal ma `tool`

**Kryterium wyjścia:** pełny pakiet zielony bez zmiany ani jednej asercji
opisującej zachowanie produktu. Jeśli test zachowania wymaga zmiany, Etap A
zrobił za dużo.

### Etap B — przepięcie decyzji ✅

Zrobione, w kolejności narzuconej przez ryzyko R1:

1. **`reflex.py`** — `Reflex.target: StepTarget`, `BY_TOOL` → `BY_TARGET`,
   `check(target, value)` / `answer(target, value)`. Klucz to cel, nie napis,
   więc zdolność o tej samej końcówce nazwy jest po prostu innym kluczem, a nie
   trafieniem w nieistniejący wpis.
2. **`verify.py`** — `_check_reflex` woła `reflex.check(e.target, …)`.
3. **`report.py`** — `_measured_answer` woła `reflex.answer(item.target, …)`.
4. **`planner.py`** — `_sanitise` i `_runs_here` przez `TargetResolver`;
   konstruktor przyjmuje `resolver=`, domyślnie tylko-narzędziowy.
5. **`cli.py --dry-run`** — podgląd skutków przez ten sam resolver. Wcześniej
   czytał rejestr narzędzi wprost, więc o kroku ze zdolnością powiedziałby
   „nie dotknie niczego".

`app.build` składa jeden resolver znający oba katalogi i podaje go plannerowi;
CLI sięga po `garis.planner.resolver`, żeby podgląd i planowanie odpowiadały tak
samo.

**Nowy moduł:** `runtime/resolve.py` — `ResolvedTarget` (`known`, `runs_here`,
`reason`, `effects`, `validate`) i `TargetResolver`. Test strukturalny czyta jego
źródło z pominięciem docstringów i przewraca się, jeśli pojawi się w nim runner,
polityka, księga efektów albo cokolwiek asynchronicznego.

**Czego Etap B nie zrobił:** planner nadal nie widzi zdolności w prompcie
(R5) — to świadome. Bramka z R1 jest już zamknięta, więc odblokowanie ich w
katalogu modelu jest teraz zmianą jednego miejsca, a nie skokiem na głęboką
wodę.

### Etap C — usunięcie starego ✅

- `PlanStep.tool` i `StepEvidence.tool` **usunięte**. Nie zdeprecjonowane —
  usunięte; głośna właściwość miała sens dopóki coś jeszcze kluczowało po
  nazwie, a od Etapu B nic nie kluczuje.
- `CapabilityRegistry.perform` **usunięte** razem z `forget_effects`. Żaden kod
  produkcyjny go nie wołał; przetrwało, bo z nim testy były krótsze — dokładnie
  tak druga ścieżka zostaje przy życiu. Tłumaczenie wyniku zostało jako
  `performed_from()`, bo to tłumaczenie, nie wykonanie.
- `capabilities/legacy_effects.py` **skasowane**. Zdolność zmieniająca stan bez
  zadeklarowanych skutków jest odrzucana przy rejestracji. `windows.process.list`
  deklaruje `effects=frozenset()` wprost.
- Planner widzi oba katalogi przez `TargetResolver.menu()` i może wskazać
  `capability`. Nadal nic nie wykonuje — pilnuje tego test strukturalny.
- Kolumna `task_steps.tool`, `StepRecord.tool` i klucz `tool` w API **zostają**.

**Czego Etap C nie zrobił:** filtrowania zdolności po trafności. `ToolSpec` ma
kategorię, `Capability` nie — `menu(categories=…)` zawęża więc tylko narzędzia,
a zdolności filtruje sam katalog do `exposed and supported_here`. Punkt
rozszerzenia jest jeden i opisany w `resolve.py`; budowanie systemu selekcji
byłoby osobnym zadaniem, nie sprzątaniem po migracji.

---

## 7. Strategia migracji testów

| Plik:linia | Asercja | Traktowanie |
|---|---|---|
| `test_agent.py:60,87,139` | `[e.tool for e in outcome.evidence]` | Etap B → `e.target.name` |
| `test_truthfulness.py:59,121,163-164,218,268` | `PlanStep(key=…, tool=…)`, `s.tool` | Etap A dla konstruktorów, B dla odczytów |
| `test_tasks.py:238` | `[s.tool for s in steps]` — `StepRecord` | **zostaje bez zmian** — dowód, że API się nie ruszyło |
| `test_capabilities.py:293` | `listing.steps[0].tool == "process_find"` | Etap B → `target` |
| `test_effect_safety.py:525,537` | `StepEvidence(tool=…)` | Etap A |

Zasada: test opisujący **gwarancję produktu** (wznawialność, kontrakt API,
prawdomówność raportu) nie zmienia treści — najwyżej sposób zapisu. Test, który
trzeba przepisać co do sensu, sygnalizuje, że migracja zmienia zachowanie, i
wtedy zmienia się plan, nie test.

Testy do **dopisania**, po jednym na ryzyko:

1. plan sprzed migracji (sam `tool`) wczytuje się i wykonuje — R3
2. wiersz `task_steps` sprzed migracji 5 wznawia zadanie — §4.1
3. `step_view` nadal zawiera `tool` — R/§4.2, kontrakt frontendu
4. zgoda zapisana przed migracją dopasowuje się po niej — R2
5. krok z `tool` i `capability` naraz jest pomijany z notatką, nie wywraca planu — R4
6. `PlanStep.tool` dla zdolności nie zwraca cicho nazwy zdolności — R1

---

## 8. Co ten dokument rozstrzyga, a co zostawia

**Rozstrzygnięte:** kształt `PlanStep` i `StepEvidence`; brak `Action.target`;
migracja addytywna; `tool` zostaje w API; kolejność A→B→C; traktowanie kroku z
dwoma polami; zakaz przepinania reflexów przed Etapem B.

**Do rozstrzygnięcia przed Etapem B:** kształt wspólnego rozstrzygania kroku
(§3.3) — czy jest to nowa funkcja w `runtime/`, czy udostępnienie kroku 1
runnera. To jedyne miejsce, w którym spodziewam się projektowania; reszta jest
przepisywaniem pod nadzorem testów.

**Poza zakresem commitu 4:** przepięcie reflexów na zdolności, rozszerzenie
promptu plannera o zdolności, usunięcie `capabilities/legacy_effects.py`,
katalog `~/.garis/plugins/`.
