# Recovery — co robić z efektem, za który nikt nie ręczy

Awaria zostawia efekt w stanie `UNCERTAIN`: zarezerwowany, nierozliczony, świat
nieznany. GARIS już wcześniej odmawiał powtórzenia takiej operacji. To jest
bezpieczne i niepełne — „nie wiem" ma być **początkiem** ustalania, nie końcem.

Recovery ogląda świat. Nigdy nie powtarza operacji.

```
GARIS zaczął operację
→ awaria
→ skutek UNKNOWN
→ bezpieczna inspekcja przez CapabilityRunner
→ werdykt zapisany, dopisywalny, odtwarzalny
```

## Dwie prawdy, dwa pola

To jest rozróżnienie, dla którego ta warstwa istnieje, i to, które najłatwiej
zgubić:

```
zlecenie:      audio.set(volume=30)
po awarii:     audio.get() == 30
```

To dowodzi, że głośność wynosi 30. **Nie dowodzi, że ustawił ją GARIS** —
człowiek mógł sięgnąć po pokrętło, kiedy silnik nie działał. Sklejenie tych
dwóch rzeczy pozwoliłoby zapisać zbieg okoliczności jako wykonaną operację, a
każde późniejsze recovery — plików, wiadomości, instalacji — odziedziczyłoby to
pomieszanie.

Dlatego `RecoveryAssessment` niesie obie osobno:

| Pole | Odpowiada na |
|---|---|
| `disposition` | Czy **ta** operacja zmieniła świat? (sprawstwo) |
| `verification` | Czy cel jest osiągnięty i kto to sprawdził? (stan) |

I to jest pełnoprawne rozstrzygnięcie:

```python
disposition            = UNKNOWN
verification.goal_met  = True
verification.checked   = True
verification.uncertain = False
```

Czytane jako: *głośność wynosi 30 i zmierzyłem to; czy zrobiła to moja
wcześniejsza próba — nie wiem.* Zadanie może się na tym zakończyć. Efekt **nie**
może na tym dostać `APPLIED`.

`APPLIED` wymaga śladu związanego z samą operacją: identyfikatora wysłanej
wiadomości, potwierdzenia płatności, pliku, który tylko ta operacja mogła
zapisać. `RecoveryAssessment.__post_init__` odrzuca kombinacje kasujące to
rozróżnienie — autor zdolności popełnia ten błąd w dobrej wierze, a komentarz by
go nie powstrzymał.

## Stany, do których recovery może dojść

| Status | Efekt po | Dyspozycja | Co to znaczy |
|---|---|---|---|
| `RESOLVED_APPLIED` | `DONE` | `APPLIED` | Znaleziono ślad tej operacji |
| `RESOLVED_APPLIED` + `goal_met=False` | `DONE` | `APPLIED` | Zadziałało i chybiło: prosiliśmy o 30, wyszło 33 |
| `RESOLVED_GOAL_ONLY` | `UNCERTAIN` | `UNKNOWN` | Cel zmierzony, sprawca nieznany |
| `RESOLVED_NOT_APPLIED` | `FAILED` | `NOT_APPLIED` | Na pewno nie doszło do skutku |
| `STILL_UNKNOWN` | `UNCERTAIN` | `UNKNOWN` | Inspekcja nic nie ustaliła |
| `MANUAL_REQUIRED` | `UNCERTAIN` | `UNKNOWN` | Brak reconcilera, niebezpieczny inspektor albo koperta prosi o człowieka |

Próba, która niczego nie ustaliła, **nie dotyka oryginału ani jednym słowem** —
zostaje wiersz w historii i zdarzenie.

## `retry_requires_confirmation`

`NOT_APPLIED` uzyskane przez recovery jest uczciwe i z drugiej ręki. Operacja za
nim to dokładnie ta, której nikt nie chce wykonać dwa razy przez przypadek.

Flaga siedzi **wewnątrz** `EffectRecord.repeatable`, nie obok:

```python
@property
def repeatable(self) -> bool:
    if self.retry_requires_confirmation:
        return False
    return self.state is EffectState.FAILED and self.disposition.permits_retry
```

Powód jest mechaniczny: `EffectStore.reserve_in` **sam** re-klaimuje każdy
wiersz, dla którego `repeatable` jest prawdą. Flaga, którą trzeba pamiętać, żeby
sprawdzić, nie chroniłaby niczego — a operacja, której by nie ochroniła, była już
raz niepewna.

`NOT_APPLIED` zaobserwowane na bieżąco zachowuje starą regułę: wolno ponowić.

## Jedna koperta, jedna polityka

Inspekcja to zwykłe wywołanie:

```
TargetResolver → CapabilityRunner → polityka → dzierżawa → efekt
→ wykonawca → dowody → weryfikator → zapis → outbox
```

z własnym świeżym identyfikatorem efektu, `parent_effect_id` wskazującym
niepewny efekt, odziedziczonym zadaniem, własnym kluczem kroku, własnym wierszem
audytu i własnymi zdarzeniami.

Świeży identyfikator nie jest wyjątkiem dla recovery — inspektor nie deklaruje
skutków, więc `derive_effect_id` daje mu nowy. Odtworzenie zapisanego pomiaru
oddałoby odczyt sprzed awarii, czyli dokładnie tę liczbę, którą recovery ma
odświeżyć.

**Drugiej polityki nie ma.** Statycznie odpada tylko to, z czym żaden silnik
polityki by się nie spierał:

- inspektor deklarujący jakikolwiek skutek,
- nieznany albo nieobsługiwany na tym systemie,
- atrapa (`fixture=True`) w profilu PRODUCTION,
- zdolność mówiąca wprost, że nikt jej nie sprawdza (`always_unchecked`).

Reszta należy do koperty. Gdy runner zwróci prośbę o zgodę albo odmowę, recovery
kończy się `MANUAL_REQUIRED`, a inspektor się **nie wykonuje**.

Pusty schemat dowodów **nie** dyskwalifikuje inspektora. Rozstrzyga
`Verification`: zdolność może zwrócić sprawdzony, ustrukturyzowany odczyt bez
osobnego `evidence_id`, a odrzucanie tego byłoby dyskwalifikacją za formalność.

## Historia

`effect_recoveries` jest dopisywalna. Jeden niepewny efekt może być oglądany
wiele razy — komputer bywa uśpiony, plik zablokowany, usługa jeszcze nie wstała —
i każda próba jest faktem o pewnej chwili. Nadpisanie poprzedniej skasowałoby
powód, dla którego potrzebna była następna.

Numeracja prób jest monotoniczna, identyfikator stabilny, tożsamość inspektora
zapisana **kanonicznie** (z resolvera), żeby dwa zapisy jednej nazwy nie
odczytały się jako dwa różne recovery.

Rozliczenie efektu, wiersz historii, audyt i zdarzenie idą w jednej transakcji.

## Zdarzenia

`effect.recovery.started` · `resolved` · `still_unknown` · `manual_required`

Niosą kody, flagi i identyfikatory: `recovery_id`, `effect_id`,
`attempt_number`, `status`, `disposition`, `inspector`, `goal_met`, `checked`,
`uncertain`, `evidence_ids`. Zmierzona wartość zostaje w wierszu efektu, który
otwiera się świadomie — zdarzenie idzie do każdego, kto słucha magistrali.

## CLI

```
$ garis effects list --uncertain
? eff-demo  windows.process.list unknown

$ garis effects show eff-demo
windows.process.list
eff-demo · uncertain · skutek: unknown

Nie umiem tego sprawdzić automatycznie — musisz ocenić sam.

$ garis effects reconcile eff-demo
manual_required
Ta zdolność nie umie sprawdzić się po fakcie — musisz to ocenić sam.
skutek: unknown · cel: nieosiągnięty · sprawdzone: nie
Ta decyzja należy do Ciebie.
```

`reconcile` idzie przez `EffectRecoveryService`, nie obok niego. Wykonywania
ponowienia w tej fazie nie ma — flaga jest ustawiana, ekran do jej zdjęcia
powstanie później.

## Wznawianie zadań

`TaskSupervisor.recover()` ustala niepewne efekty, **zanim** odda zadanie pętli.
Pętla ma robić postęp, a postęp po nierozstrzygniętym efekcie to właśnie to
powtórzenie, któremu ta warstwa zapobiega.

Zadanie, którego nie da się rozstrzygnąć — brak reconcilera, `STILL_UNKNOWN`
albo odzyskane `NOT_APPLIED` — zostaje `BLOCKED` z powodem. Bez podłączonego
serwisu wznowienie działa jak dotąd i nadal nie potrafi powtórzyć niepewnej
operacji, bo rezerwacja jej nie przyzna.

Werdykt jest deterministyczny. Weryfikator na modelu, który biegnie później i
powie „wygląda dobrze", pisze do raportu — nie do księgi efektów.

## Czego tu jeszcze nie ma

- **Wykonania ponowienia po odzyskanym `NOT_APPLIED`.** Flaga jest ustawiana,
  decyzja użytkownika nie ma jeszcze drogi wejścia.
- **Recovery opartego na modelu.** Model może kiedyś pomóc *wyjaśnić* dowody.
  Werdyktu nie produkuje.
- **Prawdziwej zdolności zmieniającej Windows.** Testy chodzą na dwunastu
  atrapach (`capabilities/fixtures.py`, wszystkie `fixture=True`), które
  `profiles.validate` odrzuca w PRODUCTION. **Nic z tego nie zostało sprawdzone
  na Windows** — pierwszym prawdziwym przypadkiem będzie `audio.set`.
