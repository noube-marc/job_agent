"""Recruiters' replies read in a dedicated Gmail label (no real Gmail, no AI)."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

from openpyxl import load_workbook
from test_gmail_lock import MESSAGES, FakeGmail

from conftest import NOW, ROOT, make_offer
from job_agent.gmail.reader import AlertEmail, GmailReader
from job_agent.gmail.replies import classify, match_offer, process_replies, telegram_text
from job_agent.processing.pipeline import process_offers
from job_agent.processing.sectors import SectorIndex
from job_agent.storage import Storage
from job_agent.tracking.excel import SHEET_REPLIES, SHEET_TRACKING, append_offers

RECEIVED = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


def test_meaning_of_replies() -> None:
    assert classify("Votre candidature", "Malheureusement, nous ne pouvons pas donner suite.")[1] == "refusé"
    assert (
        classify("Suite de votre candidature", "Pouvez-vous nous indiquer vos disponibilités pour un entretien ?")[1]
        == "entretien"
    )
    assert classify("Accusé de réception", "Nous avons bien reçu votre candidature.")[1] == "postulé"
    assert (
        classify("Bonne nouvelle", "Nous avons le plaisir de vous faire une promesse d'embauche.")[1] == "offre reçue"
    )
    # A refusal after an interview is a refusal.
    assert classify("Suite à notre entretien", "Malheureusement, nous avons retenu un autre candidat.")[1] == "refusé"
    assert classify("Question", "Pouvez-vous me rappeler ?")[1] == "à lire"


def _tracked(config, tmp_path) -> Storage:
    storage = Storage(tmp_path / "t.db")
    process_offers(
        [
            make_offer(source_offer_id="1", title="Data Analyst H/F", company="AXA France"),
            make_offer(source_offer_id="2", title="Data Engineer H/F", company="AXA France"),
            make_offer(source_offer_id="3", title="Data Scientist H/F", company="Natixis"),
        ],
        config,
        storage,
        SectorIndex.build(config, ROOT / "sectors.yaml"),
        NOW,
    )
    storage.mark_tracked([1, 2, 3])
    return storage


def _reply(subject: str, body: str, sender: str = "Recrutement AXA <talents@axa.fr>") -> AlertEmail:
    return AlertEmail("r1", "reply", sender, subject, RECEIVED, "", body)


def test_reply_is_matched_to_the_tracked_offer(config, tmp_path) -> None:
    storage = _tracked(config, tmp_path)
    offer = match_offer(storage, "Recrutement AXA <talents@axa.fr>", "Votre candidature : Data Engineer", "Bonjour,")
    assert offer["title"] == "Data Engineer H/F"
    assert match_offer(storage, "Inconnu <rh@ailleurs.fr>", "Votre candidature", "Bonjour,") is None
    storage.close()


def test_replies_go_to_telegram_and_the_replies_sheet_only(config, tmp_path) -> None:
    storage = _tracked(config, tmp_path)
    path = tmp_path / "suivi.xlsx"
    append_offers(path, storage.pending_tracking(), storage.group_members, keep_rejected=True)

    [reply] = process_replies(storage, [_reply("Votre candidature Data Analyst", "Malheureusement, pas retenue.")])
    text = telegram_text(reply)
    assert "Data Analyst H/F (AXA France)" in text and "« refusé »" in text
    assert "Malheureusement" not in text  # the body is never sent nor stored
    assert "Malheureusement" not in storage.unwritten_replies()[0]["reply"]

    append_offers(path, [], storage.group_members, keep_rejected=True, replies=storage.unwritten_replies())
    workbook = load_workbook(path)
    row = [cell.value for cell in workbook[SHEET_REPLIES][2]]
    assert row[1:4] == [reply.offer_id, "AXA France", "Data Analyst H/F"] and row[-1] == "refusé"
    status_column = workbook[SHEET_TRACKING].cell(row=1, column=18).value
    assert status_column == "Statut" and all(
        workbook[SHEET_TRACKING].cell(row=r, column=18).value is None for r in range(2, 5)
    )  # the user's status column is never written
    storage.close()


def test_only_the_dedicated_label_and_dates_are_read() -> None:
    gmail = FakeGmail()
    lock = SimpleNamespace(gmail_label="Alertes-emploi", start_date=date(2026, 11, 9))
    messages = GmailReader(gmail, lock).read_labelled()
    # Any sender of the label is read (recruiters), but never outside the label nor before the date.
    assert sorted(m.message_id for m in messages) == ["ok", "other-sender"]
    assert set(MESSAGES) - {"ok", "other-sender"} == {"no-label", "too-old"}
    lists = [kw for name, kw in gmail.calls if name == "list" and "labelIds" in kw]
    assert lists and all(kw["labelIds"] == ["Label_42"] and kw["q"].startswith("after:2026/11/09") for kw in lists)
