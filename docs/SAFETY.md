# Bezpieczeństwo i zgody

GARIS ma pełny dostęp do środowiska użytkownika. To działa tylko wtedy, gdy
zabezpieczenia są **pod spodem**: niewidoczne przy zwykłej pracy, nieprzekraczalne
przy skutkach, których nie da się cofnąć.

Reguły są w kodzie w jednym miejscu — `core/src/garis/runtime/policy.py` — i
pilnowane testami w `tests/test_policy.py`.

## Bez pytania

GARIS nie prosi o zgodę na zwykłą pracę. Odczyt plików, zapis, uruchamianie
programów, sieć, odwracalne zmiany konfiguracji, zrzut ekranu, sterowanie myszą,
instalacja darmowego oprogramowania z zaufanego źródła — wykonuje i tyle. Każda
z tych operacji ląduje w audycie.

Powód: agent, który pyta o wszystko, jest gorszy od braku agenta. Użytkownik
przestaje czytać pytania po trzecim.

## Zawsze z potwierdzeniem

Pięć skutków wymaga „tak" **niezależnie od konfiguracji**:

| Skutek | Dlaczego |
|---|---|
| `payment` | wydaje pieniądze użytkownika |
| `publish` | staje się publiczne i nie da się tego odzobaczyć |
| `send_message` | mówi w imieniu użytkownika do innych ludzi |
| `credentials` | zmienia dostęp do kont |
| `delete_permanent` | dane przestają istnieć |

Konfiguracja może **dodać** kolejne bramki (`autonomy.confirm_effects`), nigdy
usunąć te. Test `test_config_cannot_remove_a_non_negotiable_gate` to sprawdza.

GARIS może przygotować całą operację — napisać wiadomość, przygotować wpis,
skonfigurować płatność — i zatrzymać się dopiero przed finalnym skutkiem.

## Krótka informacja przed dużą decyzją

Nie pytanie o pozwolenie, ale uczciwe uprzedzenie:

- pobranie powyżej `autonomy.download_notice_bytes` (domyślnie 2 GB) —
  *„Do wykonania zadania potrzebne jest pobranie około 20.0 GB. Kontynuować?"*
- jakikolwiek koszt — *„To zadanie będzie kosztować około 4.20. Kontynuować?"*
- operacja zadeklarowana jako nieodwracalna
- instalacja, która nie jest darmowa albo nie pochodzi z zaufanego repozytorium
- podniesienie uprawnień, jeśli użytkownik je wyłączył

## Czego GARIS nie zrobi nawet za zgodą

To nie jest lista „groźnych komend" — sformatowanie dysku jest decyzją
użytkownika i przechodzi normalną bramkę potwierdzenia. Zabronione jest wyłącznie
to, czym GARIS usunąłby własną rozliczalność:

1. modyfikacja albo usunięcie klucza głównego, sejfu i bazy stanu
   (reguła `guardrails`) — sprawdzane rekurencyjnie we wszystkich parametrach,
   także zagnieżdżonych,
2. zmiana własnych reguł zgody z poziomu narzędzia (reguła `policy_self_edit`) —
   te ustawienia zmienia człowiek w interfejsie.

Narzędzia sejfu są wyjątkiem od punktu 1: zapisywanie poświadczeń to ich zadanie,
więc podlegają bramce `credentials`, a nie odmowie.

## Osobowość nie dotyka bezpieczeństwa

Osobowość zmienia ton, humor i długość wypowiedzi. Nie ma **żadnej** ścieżki z
`PersonaConfig` do decyzji polityki — `PolicyEngine.__init__` przyjmuje wyłącznie
`autonomy` i `paths`, a test `test_persona_cannot_reach_the_policy_layer`
sprawdza sygnaturę i treść modułu. Gdyby było inaczej, prośba o „luźniejszą
osobowość" byłaby ścieżką eskalacji uprawnień.

## Poświadczenia

- Hasła, tokeny i klucze API **nie mogą** być zwykłą pamięcią. Detektor wzorców
  (OpenAI, Anthropic, Google, GitHub, Slack, JWT, klucze prywatne, AWS, frazy typu
  „hasło to …") odrzuca je i kieruje do sejfu.
- Sejf: osobny plik, osobny klucz wyprowadzony z klucza głównego, szyfrogram
  wiązany z nazwą wpisu (skopiowany wiersz pod inną nazwą się nie otworzy).
- Wartości sekretów nie wchodzą do promptu modelu, planu, zdarzenia ani audytu.
  Narzędzia widzą `vault://nazwa`; runtime podstawia wartość bezpośrednio przed
  wywołaniem handlera.
- Audyt dodatkowo maskuje wartości parametrów o nazwach wyglądających na
  poświadczenia oraz łańcuchy o kształcie tokenów.

## Audyt

Każda akcja, która dotarła do runtime — również odrzucona i ta, która nigdy nie
wystartowała — zapisuje: czas, zadanie, narzędzie, intencję, efekty, decyzję
polityki, regułę, zredagowane parametry, wynik i czas trwania.

Audyt jest bezpieczny do pokazania użytkownikowi, dołączenia do zgłoszenia błędu
i przeczytania na głos. Odpowiada też na pytanie „co robiłeś przez ostatnią
godzinę?" liczbami z bazy, nie opowieścią modelu.

## Model zagrożeń — uczciwie

GARIS chroni pamięć i poświadczenia przed **czytaniem plików**: kopiami
zapasowymi, klientami synchronizacji, innymi kontami na tym komputerze,
skradzionym dyskiem.

Nie chroni przed kodem działającym już jako ten użytkownik z dostępem do klucza
głównego. Nic lokalnego nie chroni. Kto ma sesję użytkownika, ma GARIS-a — dlatego
autostart, dostęp do klucza i zakres uprawnień konta są częścią modelu
bezpieczeństwa, a nie szczegółem instalacji.
