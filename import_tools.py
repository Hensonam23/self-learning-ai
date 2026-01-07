import os
import datetime

def normalize_topic(topic: str) -> str:
    return topic.strip().lower()

def parse_topic_blocks(text: str):
    """
    Simple format:

    Topic:
    answer line 1
    answer line 2

    Another Topic:
    answer...

    Topic line MUST end with ":".
    Everything until the next "Something:" line becomes the answer.
    """
    lines = text.splitlines()
    current_topic = None
    buf = []

    def flush():
        nonlocal current_topic, buf
        if current_topic:
            answer = "\n".join([x.rstrip() for x in buf]).strip()
            if answer:
                yield (current_topic.strip(), answer)
        current_topic = None
        buf = []

    for line in lines:
        stripped = line.strip()
        if stripped.endswith(":") and len(stripped) > 1:
            # new topic header
            if current_topic is not None:
                for item in flush():
                    yield item
            current_topic = stripped[:-1]
            buf = []
        else:
            if current_topic is not None:
                buf.append(line)

    if current_topic is not None:
        answer = "\n".join([x.rstrip() for x in buf]).strip()
        if answer:
            yield (current_topic.strip(), answer)

def import_into_knowledge(
    base_dir: str,
    filename: str,
    knowledge: dict,
    *,
    default_confidence: float = 0.95,
    overwrite: bool = False,
    note: str = "Imported from file"
):
    """
    Returns: (imported_count, skipped_count, not_found)
    """
    path = os.path.join(base_dir, filename)
    if not os.path.exists(path):
        return (0, 0, True)

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    imported = 0
    skipped = 0
    today = datetime.date.today().isoformat()

    for topic, answer in parse_topic_blocks(text):
        key = normalize_topic(topic)

        if key in knowledge and not overwrite:
            skipped += 1
            continue

        # Match your existing schema style: answer/confidence/last_updated/notes
        knowledge[key] = {
            "answer": answer,
            "confidence": float(default_confidence),
            "last_updated": today,
            "notes": note
        }
        imported += 1

    return (imported, skipped, False)
