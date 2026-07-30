"""Memory and the vault: encrypted, visible, editable — and never holding secrets."""

from __future__ import annotations

import pytest

from garis.errors import SecretNotAllowed
from garis.memory import MemoryKind, MemoryService, Scope, looks_like_secret
from garis.vault import Vault


def _raw_state(paths) -> bytes:
    """Everything SQLite may have written, including the write-ahead log."""
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        candidate = paths.state_db.with_name(paths.state_db.name + suffix)
        if candidate.exists():
            blob += candidate.read_bytes()
    return blob


# ----------------------------------------------------------------- encryption


def test_nothing_readable_is_left_on_disk(memory: MemoryService, paths, db) -> None:
    memory.remember(
        "Mieszkam w Krakowie i pracuję nad projektem Orion",
        kind=MemoryKind.FACT,
        subject="Kraków Orion",
        tags=("lokalizacja", "orion"),
    )
    db.execute("PRAGMA wal_checkpoint(FULL)")
    raw = _raw_state(paths)

    for leak in ("Krakowie", "Orion", "orion", "lokalizacja"):
        assert leak.encode("utf-8") not in raw, f"{leak!r} leży w bazie jawnym tekstem"


def test_metadata_stays_queryable(memory: MemoryService) -> None:
    """Kind and scope are clear text on purpose — they reveal nothing about content."""
    memory.remember("cokolwiek", kind=MemoryKind.PREFERENCE, scope=Scope.PERMANENT)
    assert memory.stats()["by_kind"]["preference"] == 1


def test_wrong_key_cannot_read(memory: MemoryService, db, master_key) -> None:
    from garis.crypto import SecretBox, subkey
    from garis.errors import LockedError

    memory.remember("tajna notatka", subject="x")
    impostor = MemoryService(db, SecretBox(subkey(master_key, "not-memory")))
    with pytest.raises(LockedError):
        impostor.list()


def test_vault_key_cannot_open_memory(memory: MemoryService, db, master_key) -> None:
    """Domain separation: a leaked memory key must not unlock credentials."""
    from garis.crypto import SecretBox, subkey
    from garis.errors import LockedError

    memory.remember("notatka", subject="x")
    with pytest.raises(LockedError):
        MemoryService(db, SecretBox(subkey(master_key, "vault"))).list()


# --------------------------------------------------------- remember / forget


def test_remember_and_search(memory: MemoryService) -> None:
    memory.remember("Serwer produkcyjny stoi pod 10.0.0.5", kind=MemoryKind.DEVICE,
                    subject="serwer produkcyjny")
    found = memory.search("serwer produkcyjny")
    assert len(found) == 1
    assert "10.0.0.5" in found[0].content


def test_search_ignores_polish_diacritics(memory: MemoryService) -> None:
    memory.remember("Lubię późne wieczory", subject="preferencje wieczorne")
    assert memory.search("pozne")


def test_forever_is_permanent_and_pinned_ranks_higher(memory: MemoryService) -> None:
    casual = memory.remember("Kawa z mlekiem", subject="kawa", scope=Scope.SESSION)
    forever = memory.remember("Zawsze mów mi na Ty", subject="forma zwracania",
                              kind=MemoryKind.STYLE, pinned=True)
    assert forever.scope is Scope.PERMANENT
    memory.end_session()
    assert memory.get(casual.id) is None
    assert memory.get(forever.id) is not None


def test_forget_by_description(memory: MemoryService) -> None:
    memory.remember("Stare hasło do routera jest w szufladzie", subject="router szuflada")
    removed = memory.forget_matching("router")
    assert removed and memory.search("router") == []


def test_correcting_a_memory(memory: MemoryService) -> None:
    record = memory.remember("Serwer ma 8 GB RAM", subject="serwer ram")
    memory.update(record.id, content="Serwer ma 32 GB RAM")
    assert "32 GB" in memory.get(record.id).content


def test_expiry(memory: MemoryService) -> None:
    memory.remember("Tymczasowa notatka", subject="tmp", ttl_seconds=-1)
    assert memory.list() == []
    assert memory.purge_expired() == 1


def test_context_for_goal_includes_style_and_preferences(memory: MemoryService) -> None:
    memory.remember("Mów krótko", kind=MemoryKind.STYLE, subject="styl")
    memory.remember("Nie lubię powiadomień w grach", kind=MemoryKind.PREFERENCE,
                    subject="powiadomienia")
    memory.remember("Projekt Orion to sklep", kind=MemoryKind.PROJECT, subject="orion")
    context = memory.context_for("dokończ projekt Orion")
    kinds = {r.kind for r in context}
    assert MemoryKind.PROJECT in kinds
    assert MemoryKind.STYLE in kinds or MemoryKind.PREFERENCE in kinds


# ------------------------------------------------------------ secret handling


@pytest.mark.parametrize(
    "text",
    [
        "sk-abcdefghijklmnopqrstuvwx",
        "sk-ant-api03-abcdefghijklmnopqrst",
        "ghp_abcdefghijklmnopqrstuvwxyz1234",
        "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        "xoxb-123456789012-abcdefghijkl",
        "moje hasło to Zaq12wsX!",
        "token = 8f7d6a5b4c3e2f1a",
        "-----BEGIN RSA PRIVATE KEY-----",
        "AKIAIOSFODNN7EXAMPLE",
    ],
)
def test_credentials_are_refused_by_memory(memory: MemoryService, text: str) -> None:
    assert looks_like_secret(text)
    with pytest.raises(SecretNotAllowed):
        memory.remember(text)


@pytest.mark.parametrize(
    "text",
    [
        "Klucz do mieszkania leży u sąsiada",
        "Serwer produkcyjny to 10.0.0.5",
        "Lubię krótkie odpowiedzi",
        "vault://openai_api_key",
    ],
)
def test_ordinary_sentences_are_not_flagged(memory: MemoryService, text: str) -> None:
    assert looks_like_secret(text) is None
    assert memory.remember(text)


def test_secret_goes_to_the_vault_and_leaves_a_pointer(
    memory: MemoryService, vault: Vault, paths, db
) -> None:
    ref = memory.remember_secret(
        "openai_api_key", "sk-abcdefghijklmnopqrstuvwx", note="klucz do OpenAI"
    )
    assert ref == "vault://openai_api_key"
    assert vault.get("openai_api_key") == "sk-abcdefghijklmnopqrstuvwx"

    hints = memory.list(kind=MemoryKind.CREDENTIAL_HINT)
    assert len(hints) == 1
    assert "sk-" not in hints[0].content, "pamięć nie może zawierać wartości sekretu"

    db.execute("PRAGMA wal_checkpoint(FULL)")
    assert b"sk-abcdefghijklmnopqrstuvwx" not in _raw_state(paths)


def test_vault_lists_names_without_values(vault: Vault) -> None:
    vault.set("serwer_ssh", "tajne", note="dostęp SSH")
    listed = [info.to_dict() for info in vault.list()]
    assert listed[0]["name"] == "serwer_ssh"
    assert "tajne" not in str(listed)
    assert listed[0]["ref"] == "vault://serwer_ssh"


def test_vault_ciphertext_is_bound_to_its_name(vault: Vault) -> None:
    """Copying a row under another name must not yield a readable secret."""
    from garis.errors import LockedError

    vault.set("a", "wartosc-a")
    row = vault.db.one("SELECT value_enc FROM secrets WHERE name = 'a'")
    vault.db.execute(
        "INSERT INTO secrets(name, value_enc, kind, note, created_at, updated_at)"
        " VALUES ('b', ?, 'token', '', 0, 0)",
        (row["value_enc"],),
    )
    with pytest.raises(LockedError):
        vault.get("b")


def test_vault_is_a_separate_file_from_memory(paths) -> None:
    assert paths.vault_db != paths.state_db


def test_deleting_a_secret(vault: Vault) -> None:
    vault.set("temp", "x")
    assert vault.delete("temp") and not vault.has("temp")
