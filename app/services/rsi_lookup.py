import re
import xml.etree.ElementTree as ET

import requests


DAILYMED_BASE_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
REQUEST_TIMEOUT_SECONDS = 20

REACTION_HEADINGS = (
    "adverse reactions",
    "adverse reaction",
    "undesirable effects",
    "side effects",
    "warnings and precautions",
)


def normalise_text(value):
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def search_dailymed_label(product_name):
    """Return the most suitable current DailyMed SPL for a product name."""
    response = requests.get(
        f"{DAILYMED_BASE_URL}/spls.json",
        params={"drug_name": product_name, "pagesize": 10},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    results = response.json().get("data", [])
    if not results:
        return None

    product = normalise_text(product_name)

    for result in results:
        title = normalise_text(result.get("title"))
        if product and product in title:
            return result

    return results[0]


def get_dailymed_label_text(set_id):
    """Download label XML and return readable text."""
    response = requests.get(
        f"{DAILYMED_BASE_URL}/spls/{set_id}.xml",
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    return "\n".join(
        text.strip()
        for text in root.itertext()
        if text and text.strip()
    )


def reaction_section(label_text):
    """Return the likely adverse-reaction section from a structured label."""
    text = re.sub(r"\n{2,}", "\n", label_text or "")
    lower_text = text.lower()

    positions = [
        lower_text.find(heading)
        for heading in REACTION_HEADINGS
        if lower_text.find(heading) >= 0
    ]

    if not positions:
        return text[:12000]

    start = min(positions)
    end = min(len(text), start + 12000)
    return text[start:end]


def event_matches_label(event_term, label_text):
    """
    Conservative automated listedness match.
    Returns matching label excerpts for the entered event term.
    """
    event = normalise_text(event_term)
    if len(event) < 3:
        return []

    section = reaction_section(label_text)
    sentences = re.split(r"(?<=[.!?;])\s+|\n", section)
    matches = []

    event_words = {
        word for word in re.findall(r"[a-z]{3,}", event)
        if word not in {"with", "and", "the", "from", "after"}
    }

    for sentence in sentences:
        candidate = normalise_text(sentence)
        if not candidate:
            continue

        if event in candidate:
            matches.append(sentence.strip())
            continue

        candidate_words = set(re.findall(r"[a-z]{3,}", candidate))
        if event_words and len(event_words.intersection(candidate_words)) >= min(
            2,
            len(event_words),
        ):
            matches.append(sentence.strip())

    return matches[:3]


def automatic_dailymed_assessment(product_name, event_term):
    """
    Find an official DailyMed label and return an automatic draft assessment.
    Raises requests errors so the route can give a clear message.
    """
    spl = search_dailymed_label(product_name)
    if not spl:
        return {
            "available": False,
            "message": "No DailyMed label was found for this product.",
        }

    set_id = spl.get("setid") or spl.get("set_id")
    label_text = get_dailymed_label_text(set_id)
    matches = event_matches_label(event_term, label_text)

    listed = bool(matches)

    return {
        "available": True,
        "source": "DailyMed official product label",
        "title": spl.get("title", "DailyMed label"),
        "set_id": set_id,
        "listedness_status": "Listed" if listed else "Not listed",
        "expectedness_status": "Expected" if listed else "Unexpected",
        "evidence": "\n\n".join(matches)
        if matches
        else (
            "No matching reaction term was identified in the adverse-reaction "
            "or warning sections of the retrieved DailyMed label."
        ),
    }