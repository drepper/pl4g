"""Write the documents the `json` example is timed with.

Three kinds, each a few megabytes, made from one seed so that every run times
the same bytes:

- ``twitter.json``: an object holding an array of statuses shaped like the ones
  Twitter's API answers with -- nested users, entities, numbers of every kind,
  text in several scripts and escapes -- which is the document simdjson and
  yyjson report their speeds on;
- ``numbers.json``: an array of floating-point and whole numbers;
- ``strings.json``: an array of strings, some with escapes in them.

    python3 generate.py DIRECTORY [MEGABYTES]
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

WORDS = ("the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog", "naïve",
         "café", "日本語", "テキスト", "русский", "emoji 😀", "tab\there", "quote \"x\"",
         "back\\slash", "line\nbreak", "€100", "ok")


def text(rng: random.Random, words: int) -> str:
    """Some words, picked."""
    return " ".join(rng.choice(WORDS) for _ in range(words))


def user(rng: random.Random, at: int) -> dict[str, object]:
    """A user as a status carries it."""
    return {
        "id": rng.randrange(1, 1 << 62),
        "id_str": str(rng.randrange(1, 1 << 62)),
        "name": text(rng, 2),
        "screen_name": "user" + str(at),
        "location": text(rng, 2),
        "description": text(rng, 12),
        "url": None if rng.random() < 0.5 else "https://example.com/" + str(at),
        "protected": rng.random() < 0.1,
        "followers_count": rng.randrange(0, 1000000),
        "friends_count": rng.randrange(0, 5000),
        "listed_count": rng.randrange(0, 100),
        "created_at": "Sun Aug 31 00:29:15 +0000 2014",
        "favourites_count": rng.randrange(0, 100000),
        "utc_offset": None,
        "geo_enabled": rng.random() < 0.5,
        "verified": False,
        "statuses_count": rng.randrange(0, 100000),
        "lang": "ja",
        "profile_background_color": "C0DEED",
        "profile_image_url": "http://pbs.twimg.com/profile_images/" + str(at) + "/a.jpeg",
        "profile_use_background_image": True,
        "default_profile": rng.random() < 0.5,
    }


def status(rng: random.Random, at: int) -> dict[str, object]:
    """One status, with what it mentions."""
    return {
        "metadata": {"result_type": "recent", "iso_language_code": "ja"},
        "created_at": "Sun Aug 31 00:29:15 +0000 2014",
        "id": 505874924095815681 + at,
        "id_str": str(505874924095815681 + at),
        "text": text(rng, 20),
        "source": "<a href=\"http://twitter.com/download/iphone\" rel=\"nofollow\">Twitter for iPhone</a>",
        "truncated": False,
        "in_reply_to_status_id": None,
        "user": user(rng, at),
        "geo": None if rng.random() < 0.8 else {"type": "Point", "coordinates": [
            rng.uniform(-90, 90), rng.uniform(-180, 180)]},
        "retweet_count": rng.randrange(0, 1000),
        "favorite_count": rng.randrange(0, 1000),
        "entities": {
            "hashtags": [{"text": text(rng, 1), "indices": [rng.randrange(140), rng.randrange(140)]}
                         for _ in range(rng.randrange(3))],
            "symbols": [],
            "urls": [],
            "user_mentions": [{"screen_name": "user" + str(rng.randrange(1000)),
                               "name": text(rng, 2), "id": rng.randrange(1 << 40),
                               "indices": [0, 12]} for _ in range(rng.randrange(3))],
        },
        "favorited": False,
        "retweeted": rng.random() < 0.3,
        "lang": "ja",
    }


def twitter(rng: random.Random, size: int) -> object:
    """Statuses until the document is about *size* bytes."""
    statuses: list[object] = []
    written = 0
    while written < size:
        one = status(rng, len(statuses))
        written += len(json.dumps(one, ensure_ascii=False).encode()) + 1
        statuses.append(one)
    return {"statuses": statuses, "search_metadata": {"count": len(statuses)}}


def numbers(rng: random.Random, size: int) -> object:
    """Numbers until the document is about *size* bytes."""
    found: list[object] = []
    written = 0
    while written < size:
        pick = rng.random()
        one: object = (rng.uniform(-1000, 1000) if pick < 0.5
                       else rng.randrange(-(1 << 40), 1 << 40) if pick < 0.8
                       else rng.uniform(0, 1) * 10 ** rng.randrange(-30, 30))
        written += len(json.dumps(one)) + 1
        found.append(one)
    return found


def strings(rng: random.Random, size: int) -> object:
    """Strings until the document is about *size* bytes."""
    found: list[object] = []
    written = 0
    while written < size:
        one = text(rng, rng.randrange(1, 30))
        written += len(json.dumps(one, ensure_ascii=False).encode()) + 1
        found.append(one)
    return found


def main() -> None:
    """Write the three documents into the directory named."""
    where = Path(sys.argv[1])
    size = int(float(sys.argv[2]) * 1000000) if len(sys.argv) > 2 else 4000000
    where.mkdir(parents=True, exist_ok=True)
    for name, made in (("twitter", twitter), ("numbers", numbers), ("strings", strings)):
        rng = random.Random(name)
        (where / (name + ".json")).write_text(
            json.dumps(made(rng, size), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
