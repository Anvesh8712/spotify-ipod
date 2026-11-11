"""YouTube Music matching adapted from CSVMusic 1.7.7 scoring."""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

CONFIDENCE_MIN = 0.6
SEARCH_LIMIT = 12
RATE_LIMIT_S = 0.35
DURATION_TOLERANCE_RATIO = 0.10
SHORT_TRACK_MAX_SECONDS = 45
SHORT_TRACK_TOLERANCE_SECONDS = 8
SEARCH_RETRY_COUNT = 2
SEARCH_RETRY_SLEEP_S = 0.9

_PENALTY_TERMS = {
    "cover",
    "sped",
    "slowed",
    "nightcore",
    "8d",
    "reverb",
    "mashup",
    "karaoke",
    "instrumental",
    "demo",
    "tribute",
    "soundalike",
}
_VERSION_PATTERNS = {
    "live": r"\blive\b",
    "acoustic": r"\bacoustic\b",
    "extended": r"\bextended\b|\blong version\b",
    "remix": r"\bremix(?:ed)?\b|\bmix\b",
    "edit": r"\bedit\b|\bradio version\b",
    "single": r"\bsingle version\b|\boriginal single\b|\bu\.?s\.? single\b",
    "remaster": r"\bremaster(?:ed)?\b",
    "album": r"\balbum version\b",
}
_CAST_PENALTY_TERMS = {"cast", "original cast", "tribute band", "musical", "orchestra"}


def _norm_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", (value or "").casefold())
    return re.sub(r"\s+", " ", text).strip()


def _toks(value: str) -> set[str]:
    text = _norm_text(value)
    return {tok for tok in re.findall(r"\w+", text, flags=re.UNICODE) if any(ch.isalnum() for ch in tok)}


def _candidate_artist_text(cand: dict[str, Any]) -> str:
    if cand.get("artists"):
        try:
            return ", ".join(a.get("name", "") for a in cand["artists"])
        except Exception:
            pass
    return cand.get("author", "") or ""


def _candidate_album_text(cand: dict[str, Any]) -> str:
    album = cand.get("album")
    if isinstance(album, dict):
        return str(album.get("name") or album.get("title") or "").strip()
    if isinstance(album, str):
        return album.strip()
    return ""


def _version_markers(value: str) -> set[str]:
    text = _norm_text(value)
    return {marker for marker, pattern in _VERSION_PATTERNS.items() if re.search(pattern, text, flags=re.I)}


def _candidate_version_markers(cand: dict[str, Any]) -> set[str]:
    return _version_markers(" ".join((str(cand.get("title") or ""), _candidate_album_text(cand))))


def _artist_names(artists: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(artists, list):
        return names
    for artist in artists:
        if not isinstance(artist, dict):
            continue
        name = (artist.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _result_author(result: dict[str, Any], artists: Any) -> str:
    for key in ("author", "channel", "uploader", "name"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    artist_names = _artist_names(artists)
    return ", ".join(artist_names)


def _overlap_ratio(needle: set[str], haystack: set[str]) -> float:
    return len(needle & haystack) / max(1, len(needle))


def _contains_non_latin_letters(value: str) -> bool:
    for char in value or "":
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        if "LATIN" not in name:
            return True
    return False


def _duration_s(value: int | str | None) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return 0
            if ":" in text:
                total = 0
                for part in text.split(":"):
                    total = total * 60 + int(part)
                return total
            return int(float(text))
        return int(value)
    except Exception:
        return 0


def _track_duration_s(track: dict[str, Any]) -> int:
    try:
        return int(round((track.get("duration_ms") or 0) / 1000))
    except Exception:
        return 0


def duration_within_tolerance(track: dict[str, Any], cand: dict[str, Any]) -> bool:
    track_s = _track_duration_s(track)
    cand_s = _duration_s(cand.get("duration_seconds"))
    if track_s <= 0 or cand_s <= 0:
        return True
    if track_s <= SHORT_TRACK_MAX_SECONDS:
        return abs(track_s - cand_s) <= SHORT_TRACK_TOLERANCE_SECONDS
    return abs(track_s - cand_s) <= max(1.0, track_s * DURATION_TOLERANCE_RATIO)


def score_candidate(track: dict[str, Any], cand: dict[str, Any]) -> float:
    track_title_tokens = _toks(track.get("title", ""))
    track_artist_tokens = _toks(track.get("artists", ""))
    cand_title = cand.get("title") or ""
    cand_art = _candidate_artist_text(cand)
    title_overlap = _overlap_ratio(track_title_tokens, _toks(cand_title))
    artist_overlap = _overlap_ratio(track_artist_tokens, _toks(cand_art))

    sp_s = _track_duration_s(track)
    yt_s = _duration_s(cand.get("duration_seconds"))
    if sp_s > 0 and yt_s > 0:
        delta = abs(sp_s - yt_s)
        if delta <= 6:
            d_score = 1.0
        elif delta <= 12:
            d_score = 0.9
        elif delta <= 20:
            d_score = 0.78
        elif delta <= 30:
            d_score = 0.62
        else:
            d_score = 0.45
    else:
        d_score = 0.7

    channel = _norm_text(cand.get("author") or "")
    ch_boost = 0.15 if ("topic" in channel or "official" in channel) else 0.0

    titleblob = _norm_text(cand_title + " " + cand_art)
    requested_title = _norm_text(track.get("title") or "")
    requested_versions = _version_markers(requested_title)
    candidate_versions = _candidate_version_markers(cand)
    p_pen = 0.0
    for term in _PENALTY_TERMS:
        if term in titleblob:
            p_pen += 0.10
    if requested_versions:
        for marker in requested_versions - candidate_versions:
            p_pen += 0.08 if marker == "remaster" else 0.34
        for marker in candidate_versions - requested_versions:
            p_pen += 0.03 if marker == "remaster" else 0.26
    else:
        p_pen += 0.30 * len(candidate_versions - {"remaster"})
    if artist_overlap == 0.0:
        for term in _CAST_PENALTY_TERMS:
            if term in titleblob:
                p_pen += 0.18
    if "tribute" in titleblob and artist_overlap < 0.5:
        p_pen += 0.25
    version_boost = 0.14 if requested_versions and requested_versions == candidate_versions else 0.0
    total = max(0.0, d_score * 0.35 + title_overlap * 0.35 + artist_overlap * 0.25 + ch_boost + version_boost - p_pen)
    translated_catalog_match = (
        _contains_non_latin_letters(track.get("title") or "")
        and cand.get("source") == "music"
        and sp_s > 0
        and yt_s > 0
        and abs(sp_s - yt_s) <= 3
        and requested_versions == candidate_versions
        and not any(term in titleblob for term in _PENALTY_TERMS)
    )
    if translated_catalog_match:
        total = max(total, 0.68)
    return min(total, 0.99)


def _strip_noise(title: str) -> str:
    text = re.sub(r"[\(\[][^\)\]]*[Ff]eat[^\)\]]*[\)\]]", " ", title)
    text = re.sub(r"[\(\[][Oo]fficial[^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"[\(\[][Ll]ive[^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"[\(\[][Rr]emix[^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"[\(\)\[\]]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_title_artist(title: str, artists: str) -> str:
    query = f"{title} {artists}".strip()
    query = re.sub(r"\s+", " ", query)
    return query.replace(" - ", " ").replace("–", " ").replace("—", " ").strip()


def query_variants(track: dict[str, Any]) -> list[str]:
    title = track.get("title", "") or ""
    artists = track.get("artists", "") or ""
    isrc = track.get("isrc")
    clean_title = _strip_noise(title)
    base = _clean_title_artist(title, artists)
    clean = _clean_title_artist(clean_title, artists)
    variants: list[str] = []
    if isrc:
        variants.append(f"{isrc} {clean}")
    variants.append(base)
    if clean != base:
        variants.append(clean)
    if "&" in base:
        variants.append(base.replace("&", "and"))
    if re.search(r"\band\b", base, flags=re.I):
        variants.append(re.sub(r"\band\b", "&", base, flags=re.I))
    if "-" in base:
        variants.append(base.replace("-", " "))
    seen: set[str] = set()
    out: list[str] = []
    for query in variants:
        cleaned = re.sub(r"\s+", " ", query).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def _search_filter(yt: Any, query: str, search_filter: str, limit: int) -> list[dict[str, Any]]:
    results = yt.search(query, filter=search_filter, limit=limit) or []
    candidates: list[dict[str, Any]] = []
    source = "music" if search_filter == "songs" else "videos"
    for result in results:
        video_id = result.get("videoId")
        if not video_id:
            continue
        artists = result.get("artists")
        author = _result_author(result, artists)
        candidates.append(
            {
                "videoId": video_id,
                "title": result.get("title"),
                "artists": artists if search_filter == "songs" else None,
                "author": author,
                "channel": author,
                "duration_seconds": _duration_s(result.get("duration_seconds") or result.get("duration")),
                "album": result.get("album"),
                "source": source,
            }
        )
    return candidates


def rank_candidates(
    yt: Any,
    track: dict[str, Any],
    *,
    limit: int = SEARCH_LIMIT,
    confidence_min: float = CONFIDENCE_MIN,
    enforce_duration: bool = True,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    all_cands: list[dict[str, Any]] = []
    for query in query_variants(track):
        for cand in _search_filter(yt, query, "songs", limit) + _search_filter(yt, query, "videos", limit):
            video_id = cand.get("videoId")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            all_cands.append(cand)
    scored: list[dict[str, Any]] = []
    for cand in all_cands:
        if enforce_duration and not duration_within_tolerance(track, cand):
            continue
        item = dict(cand)
        item["score"] = score_candidate(track, cand)
        scored.append(item)

    def priority(candidate: dict[str, Any]) -> tuple[int, int, float, int]:
        score = float(candidate.get("score") or 0.0)
        is_music = 1 if candidate.get("source") == "music" else 0
        return (1 if score >= confidence_min else 0, is_music if score >= confidence_min else 0, score, is_music)

    return sorted(scored, key=priority, reverse=True)


def find_best(yt: Any, track: dict[str, Any], *, confidence_min: float = CONFIDENCE_MIN) -> tuple[dict[str, Any] | None, float, list[dict[str, Any]]]:
    options = rank_candidates(yt, track, confidence_min=confidence_min)
    if not options:
        last_exc: Exception | None = None
        for _ in range(SEARCH_RETRY_COUNT):
            time.sleep(SEARCH_RETRY_SLEEP_S)
            try:
                from ytmusicapi import YTMusic

                options = rank_candidates(YTMusic(), track, confidence_min=confidence_min)
            except Exception as exc:
                last_exc = exc
                continue
            if options:
                break
        if not options and last_exc is not None:
            raise last_exc
    if not options:
        return None, 0.0, []
    best = options[0]
    return (best if best["score"] >= confidence_min else None, float(best["score"]), options)
