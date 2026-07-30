"""Voice: who gets to talk, and when.

These tests describe the conversation, not the audio stack. Every one of them
would still have to pass if whisper were swapped for a provider's realtime API —
which is the point of keeping the decisions out of the engines.
"""

from __future__ import annotations

import pytest

from garis.config import ModelsConfig, VoiceConfig
from garis.models import Capability, Job, ModelRouter, ModelSpec, Privacy
from garis.models.providers.fake import FakeProvider
from garis.voice import VoiceService
from garis.voice.audio import (
    Calibrator,
    Device,
    DeviceKind,
    DeviceRegistry,
    dbfs,
    normalised_level,
)
from garis.voice.engines import (
    RecordingTextToSpeech,
    Road,
    ScriptedSpeechToText,
    choose_road,
)
from garis.voice.session import Trigger, VoiceSession, VoiceState
from garis.voice.wake import WakeWordMatcher, normalise


class Clock:
    """Milliseconds under test control — barge-in timing must not need sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, ms: float) -> None:
        self.now += ms


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def session(clock: Clock) -> VoiceSession:
    return VoiceSession(clock=clock)


# --------------------------------------------------------------------- barge-in


def test_sustained_speech_cuts_garis_off_mid_sentence(session: VoiceSession, clock: Clock):
    """The rule the whole voice layer exists to honour."""
    session.wake()
    session.start_speaking("Zaraz wyjaśnię, co znalazłem w logach…")
    assert session.speaking

    session.audio_tick(speaking=True)
    clock.advance(250)                      # past the barge-in threshold
    session.audio_tick(speaking=True)

    assert not session.speaking
    assert session.listening, "po przerwaniu GARIS musi słuchać, nie milczeć"
    assert session.interruptions == 1
    assert session.spoken_cut_short


def test_a_cough_does_not_interrupt(session: VoiceSession, clock: Clock):
    """A single short sound is not a turn; cutting off on one would be unusable."""
    session.wake()
    session.start_speaking("Długa odpowiedź")

    session.audio_tick(speaking=True)
    clock.advance(80)                       # shorter than the threshold
    session.audio_tick(speaking=False)
    clock.advance(400)
    session.audio_tick(speaking=False)

    assert session.speaking
    assert session.interruptions == 0


def test_barge_in_can_be_switched_off(clock: Clock):
    session = VoiceSession(barge_in=False, clock=clock)
    session.wake()
    session.start_speaking("Mówię do końca")

    session.audio_tick(speaking=True)
    clock.advance(2000)
    session.audio_tick(speaking=True)

    assert session.speaking
    assert session.interruptions == 0


def test_push_to_talk_interrupts_immediately(session: VoiceSession):
    """Holding the key is an explicit "stop" — no threshold applies."""
    session.start_speaking("cokolwiek")
    session.wake(Trigger.PUSH_TO_TALK)
    assert session.listening and not session.speaking


# ------------------------------------------------------------------ turn taking


def test_silence_ends_the_turn_and_hands_over_the_text(session: VoiceSession, clock: Clock):
    session.wake()
    session.audio_tick(speaking=True)
    session.heard("sprawdź miejsce na dysku")
    session.audio_tick(speaking=False)
    clock.advance(800)                      # past end-of-turn silence
    event = session.audio_tick(speaking=False)

    assert event is not None and event.kind == "turn"
    assert event.detail["text"] == "sprawdź miejsce na dysku"
    assert session.state is VoiceState.THINKING


def test_a_short_pause_does_not_end_the_turn(session: VoiceSession, clock: Clock):
    session.wake()
    session.audio_tick(speaking=True)
    session.heard("sprawdź")
    session.audio_tick(speaking=False)
    clock.advance(300)                      # thinking mid-sentence
    assert session.audio_tick(speaking=False) is None

    session.audio_tick(speaking=True)
    session.heard("dysk")
    session.audio_tick(speaking=False)
    clock.advance(800)
    event = session.audio_tick(speaking=False)

    assert event is not None and event.detail["text"] == "sprawdź dysk"


def test_waking_with_nothing_said_goes_back_to_sleep(session: VoiceSession, clock: Clock):
    """Otherwise a false wake leaves the microphone open indefinitely."""
    session.wake()
    session.audio_tick(speaking=False)
    clock.advance(6500)
    event = session.audio_tick(speaking=False)

    assert event is not None and event.kind == "timeout"
    assert session.state is VoiceState.ASLEEP


def test_empty_turn_does_not_reach_the_agent(session: VoiceSession):
    session.wake()
    event = session.end_turn()
    assert event.kind == "empty_turn"
    assert session.state is VoiceState.ASLEEP


def test_keeping_the_floor_makes_it_a_conversation(session: VoiceSession):
    session.start_speaking("Gotowe.")
    session.finished_speaking(keep_floor=True)
    assert session.listening, "po odpowiedzi można od razu dopowiedzieć"


def test_paused_microphone_ignores_everything(session: VoiceSession):
    session.pause()
    session.wake()
    assert session.state is VoiceState.PAUSED


def test_states_reach_the_bus_for_the_orb(bus):
    session = VoiceSession(bus=bus)
    session.wake()
    session.start_speaking("cześć")
    states = [e.payload["state"] for e in bus.recent("voice.state")]
    assert "listening" in states and "speaking" in states


# ------------------------------------------------------------------ wake word


@pytest.mark.parametrize(
    "heard",
    ["Garis", "garys, sprawdź dysk", "Gariś?", "charis pokaż zadania", "GARRIS"],
)
def test_recognisers_mangle_the_name_and_it_still_wakes(heard: str):
    """Being deaf to your own name is how an assistant gets abandoned."""
    assert WakeWordMatcher().match(heard)


@pytest.mark.parametrize(
    "heard",
    ["parasol", "graj muzykę", "napisz do Ani", "co słychać"],
)
def test_ordinary_speech_does_not_wake(heard: str):
    assert not WakeWordMatcher().match(heard)


def test_the_rest_of_the_sentence_survives_the_wake_word():
    """"Garis, sprawdź dysk" is one sentence, not a wake word and then a pause."""
    match = WakeWordMatcher().match("Garis sprawdź miejsce na dysku")
    assert match and match.remainder == "sprawdz miejsce na dysku"


def test_the_wake_word_is_the_users_choice():
    matcher = WakeWordMatcher()
    matcher.set_phrase("Jarvis")
    assert matcher.match("jarvis co nowego")
    assert not matcher.match("garis co nowego")


def test_being_talked_about_is_not_being_talked_to():
    assert not WakeWordMatcher().match(
        "powiedziałem koledze że garis sam wybiera model"
    )


def test_normalise_strips_polish_diacritics():
    assert normalise("Zażółć GĘŚLĄ jaźń!") == "zazolc gesla jazn"


# ---------------------------------------------------------------- calibration


def test_calibration_derives_a_threshold_above_the_room():
    """A fixed threshold is wrong in every room; this is why calibration exists."""
    calibrator = Calibrator()
    for value in [-52, -50, -51, -49, -53, -50, -48, -51, -50, -52]:
        calibrator.add_dbfs(value)
    result = calibrator.result()

    assert result.usable
    assert -53 <= result.noise_floor_dbfs <= -49
    assert result.threshold_dbfs > result.noise_floor_dbfs
    assert not result.is_speech(-48), "szum pokoju nie może być mową"
    assert result.is_speech(-25), "normalna mowa musi przechodzić"


def test_one_freakishly_quiet_frame_does_not_set_the_floor():
    calibrator = Calibrator()
    calibrator.add_dbfs(-90)                       # a single dropout
    for _ in range(12):
        calibrator.add_dbfs(-40)
    assert calibrator.result().noise_floor_dbfs > -60


def test_a_loud_room_is_reported_as_such():
    calibrator = Calibrator()
    for _ in range(10):
        calibrator.add_dbfs(-22)
    assert calibrator.result().noisy_room


def test_levels_map_onto_the_orb_range():
    assert normalised_level(-90) == 0.0
    assert 0.0 < normalised_level(-30) < 1.0
    assert normalised_level(0) == 1.0
    assert dbfs([]) == -90.0
    assert dbfs([0.5, -0.5]) > -10


# -------------------------------------------------------------------- devices


def _registry() -> DeviceRegistry:
    return DeviceRegistry(
        devices=[
            Device("1", "Mikrofon wbudowany", DeviceKind.INPUT, default=True),
            Device("2", "Zestaw słuchawkowy", DeviceKind.INPUT),
            Device("3", "Głośniki", DeviceKind.OUTPUT, default=True),
        ]
    )


def test_the_users_choice_wins():
    registry = _registry()
    registry.preferred_input = "2"
    assert registry.resolve(DeviceKind.INPUT).name == "Zestaw słuchawkowy"


def test_unplugging_the_headset_falls_back_instead_of_going_deaf():
    registry = _registry()
    registry.preferred_input = "999"
    assert registry.resolve(DeviceKind.INPUT).default
    assert registry.missing_preferred(DeviceKind.INPUT)


def test_no_audio_devices_at_all_is_not_a_crash():
    empty = DeviceRegistry()
    assert empty.resolve(DeviceKind.INPUT) is None
    assert empty.to_dict()["inputs"] == []


# ----------------------------------------------------------- choosing the road


def realtime_router(*, has_realtime: bool) -> ModelRouter:
    capabilities = {Capability.REALTIME_VOICE} if has_realtime else {Capability.TOOLS}
    jobs = {Job.VOICE, Job.CHAT} if has_realtime else {Job.CHAT}
    spec = ModelSpec(
        name="voice-1", provider="test", jobs=frozenset(jobs),
        capabilities=frozenset(capabilities), quality=0.8,
    )

    class Stub(FakeProvider):
        name = "test"

        def models(self):  # type: ignore[no-untyped-def]
            return (spec,)

    return ModelRouter([Stub()], ModelsConfig())


def test_a_realtime_provider_is_used_when_there_is_one():
    plan = choose_road(realtime_router(has_realtime=True), local_ready=True)
    assert plan.road is Road.PROVIDER_REALTIME
    assert plan.model == "voice-1"


def test_without_a_realtime_provider_it_falls_back_to_local():
    plan = choose_road(realtime_router(has_realtime=False), local_ready=True)
    assert plan.road is Road.LOCAL


def test_no_network_means_local():
    plan = choose_road(realtime_router(has_realtime=True), online=False, local_ready=True)
    assert plan.road is Road.LOCAL
    assert "sieci" in plan.reason


def test_private_conversations_never_reach_a_provider():
    plan = choose_road(
        realtime_router(has_realtime=True), privacy=Privacy.LOCAL_ONLY, local_ready=True
    )
    assert plan.road is Road.LOCAL


def test_nothing_available_is_an_honest_answer_not_a_crash():
    plan = choose_road(realtime_router(has_realtime=False), local_ready=False)
    assert plan.road is Road.UNAVAILABLE
    assert not plan.usable
    assert "silnika mowy" in plan.reason


# ------------------------------------------------------------------- service


@pytest.fixture
def service(bus) -> VoiceService:
    return VoiceService(config=VoiceConfig(), bus=bus)


def test_wake_word_then_speech_produces_a_goal(service: VoiceService):
    goals: list[str] = []
    service.on_turn = goals.append

    service.feed_transcript("Garis, sprawdź miejsce na dysku")
    assert service.session.listening
    assert service.finish_turn() == "sprawdz miejsce na dysku"
    assert goals == ["sprawdz miejsce na dysku"]


def test_speech_without_the_wake_word_is_ignored(service: VoiceService):
    goals: list[str] = []
    service.on_turn = goals.append
    service.feed_transcript("muszę pamiętać o zakupach")
    assert service.session.state is VoiceState.ASLEEP
    assert goals == []


def test_changing_the_wake_word_takes_effect_immediately(service: VoiceService):
    service.set_wake_word("komputer")
    assert service.feed_transcript("komputer, co słychać")
    assert service.session.listening


def test_calibration_updates_the_saved_noise_floor(service: VoiceService):
    service.start_calibration()
    for _ in range(12):
        service.calibration_frame([0.004, -0.004, 0.003, -0.003])
    result = service.finish_calibration()

    assert result.usable
    assert service.config.noise_floor_dbfs == result.noise_floor_dbfs


def test_service_reports_everything_the_settings_screen_needs(service: VoiceService):
    described = service.describe()
    for key in ("enabled", "state", "wake", "barge_in", "calibration", "plan", "devices"):
        assert key in described


async def test_scripted_engines_drive_a_whole_exchange():
    """The doubles have to be good enough to run the loop end to end."""
    stt = ScriptedSpeechToText(["garis", "sprawdź dysk"])
    tts = RecordingTextToSpeech()

    await stt.start()
    assert await stt.feed(b"x") == "garis"
    assert await stt.feed(b"x") == "sprawdź dysk"
    assert await stt.finish() == "garis sprawdź dysk"

    async for _ in tts.synthesise("Gotowe."):
        pass
    assert tts.said == ["Gotowe."]

    await tts.stop()
    assert tts.stopped == 1
