"""Recruiters' replies to the user's applications, read in a dedicated Gmail label.

The user files the replies himself (by hand or with a Gmail filter) in a
label such as "Candidatures" (`application_replies.gmail_label`). Only this
label is read, read-only, and only messages received after `start_date`:
the rest of the mailbox is never touched (same lock as the alerts, without
the sender list since recruiters write from any address).

For each new reply, the agent:
- finds the offer of the tracking file it answers (company, then title);
- guesses its meaning with key words, LOCALLY (no AI: these e-mails hold
  personal data): refusal, interview, acknowledgement, job offer;
- appends a row to the "Réponses" sheet and sends a Telegram message
  suggesting a status. It never writes the user's "Statut" column.
Only the subject and the guessed meaning are stored, never the body.
"""

import json
import re
from dataclasses import dataclass
from email.utils import parseaddr

from rapidfuzz import fuzz

from job_agent.collectors.base import html_to_text
from job_agent.gmail.reader import AlertEmail
from job_agent.storage import Storage
from job_agent.text import contains_phrase, plain, tokens

# Meaning -> (suggested status, key phrases). The first meaning found wins,
# in this order: a job offer or a refusal says more than an acknowledgement.
MEANINGS = {
    "offre": (
        "offre reçue",
        [
            "promesse d'embauche",
            "proposition d'embauche",
            "offre d'embauche",
            "vous proposer le poste",
            "proposition de contrat",
            "job offer",
            "offer letter",
        ],
    ),
    "refus": (
        "refusé",
        [
            "malheureusement",
            "pas été retenue",
            "pas été retenu",
            "pas retenue",
            "pas retenu",
            "ne pas donner suite",
            "ne pouvons pas donner suite",
            "pas donner une suite favorable",
            "autre candidat",
            "autre candidature",
            "regret de vous informer",
            "nous regrettons",
            "unfortunately",
            "not been selected",
            "not to move forward",
            "other candidates",
        ],
    ),
    "entretien": (
        "entretien",
        [
            "entretien",
            "échange téléphonique",
            "echange telephonique",
            "vous rencontrer",
            "vos disponibilités",
            "vos disponibilites",
            "rendez-vous",
            "visioconférence",
            "visio",
            "interview",
            "your availability",
            "schedule a call",
        ],
    ),
    "accusé de réception": (
        "postulé",
        [
            "bien reçu votre candidature",
            "bien recu votre candidature",
            "accusé de réception",
            "accuse de reception",
            "nous avons reçu votre",
            "nous avons recu votre",
            "merci pour votre candidature",
            "thank you for applying",
            "we have received your application",
        ],
    ),
}
UNKNOWN = ("à lire", "réponse à lire")


@dataclass
class Reply:
    message_id: str
    received_at: str
    sender_name: str
    subject: str
    meaning: str
    suggested_status: str
    offer_id: int | None
    company: str | None
    title: str | None


def classify(subject: str, body: str) -> tuple[str, str]:
    """(meaning, suggested status) from the key words of the message."""
    words = tokens(f"{subject} {body}")
    for meaning, (status, phrases) in MEANINGS.items():
        if any(contains_phrase(words, phrase) for phrase in phrases):
            return meaning, status
    return UNKNOWN[1], UNKNOWN[0]


def _sender_domain(address: str) -> str:
    domain = address.rsplit("@", 1)[-1].lower()
    parts = [part for part in domain.split(".") if part not in {"com", "fr", "net", "org", "eu", "io", "co", "mail"}]
    return " ".join(parts[-1:])


def match_offer(storage: Storage, sender: str, subject: str, body: str):
    """The tracked offer the reply is about (or None): company named in the
    sender, the subject or the text, then the closest title."""
    name, address = parseaddr(sender)
    haystack = tokens(f"{name} {_sender_domain(address)} {subject} {body[:3000]}")
    best, best_score = None, 0.0
    for row in storage.tracked_offers():
        names = {name for name in (row["company_key"], row["company"]) if name}  # "AXA" or "AXA France"
        if not any(contains_phrase(haystack, name) for name in names):
            continue
        score = fuzz.token_set_ratio(plain(row["title"]), plain(f"{subject} {body[:1500]}"))
        if score > best_score:
            best, best_score = row, score
    return best


def process_replies(storage: Storage, messages: list[AlertEmail]) -> list[Reply]:
    replies = []
    for message in messages:
        body = message.text or html_to_text(message.html)
        meaning, status = classify(message.subject, body)
        offer = match_offer(storage, message.sender, message.subject, body)
        name, address = parseaddr(message.sender)
        reply = Reply(
            message_id=message.message_id,
            received_at=message.received_at.isoformat(),
            sender_name=name or address,
            subject=message.subject,
            meaning=meaning,
            suggested_status=status,
            offer_id=offer["id"] if offer is not None else None,
            company=(offer["company"] or offer["company_key"]) if offer is not None else None,
            title=offer["title"] if offer is not None else None,
        )
        storage.save_reply(reply.message_id, json.dumps(reply.__dict__, ensure_ascii=False))
        replies.append(reply)
    return replies


def telegram_text(reply: Reply) -> str:
    about = f"{reply.title} ({reply.company})" if reply.company else "offre non retrouvée dans votre suivi"
    icon = {"refus": "📭", "entretien": "📅", "offre": "🎉", "accusé de réception": "📨"}.get(reply.meaning, "✉️")
    lines = [f"{icon} Réponse à votre candidature : {about}"]
    if reply.meaning == UNKNOWN[1]:
        lines.append("Sens non reconnu : lisez-la dans votre libellé Gmail.")
    else:
        lines.append(f"Sens probable : {reply.meaning}. Statut suggéré : « {reply.suggested_status} ».")
    lines.append("Mettez à jour la colonne « Statut » de votre fichier de suivi (l'agent n'y touche jamais).")
    return re.sub(r"[ \t]+", " ", "\n".join(lines))
