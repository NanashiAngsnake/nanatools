from __future__ import annotations

import csv
import dataclasses
import json
import logging
import re
import sqlite3
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from .. import config

log = logging.getLogger(__name__)

MAX_ANALYSIS_CHARS = 100_000

_BATCH = 400

_KANJI_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
_KANA_RE = re.compile(r"[぀-ゟ゠-ヿ]")
_JP_RE = re.compile(r"[぀-ヿ一-鿿㐀-䶿豈-﫿ｦ-ﾟ]")


def kata_to_hira(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def has_kanji(text: str) -> bool:
    return _KANJI_RE.search(text) is not None


def has_kana(text: str) -> bool:
    return _KANA_RE.search(text) is not None


def is_japanese(text: str) -> bool:
    return _JP_RE.search(text) is not None


def kanji_in(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for ch in text:
        if _KANJI_RE.match(ch):
            seen.setdefault(ch, None)
    return list(seen)


def ruby_for(surface: str, kana: str) -> str:
    if not kana or not has_kanji(surface):
        return ""
    reading = kata_to_hira(kana)
    return "" if reading == surface else reading


def ro_uri(path: Path) -> str:
    posix = Path(path).resolve().as_posix().lstrip("/")
    return "file:/" + urllib.parse.quote(posix) + "?mode=ro"


@dataclass(frozen=True)
class Sense:
    lang: str
    pos_codes: tuple[str, ...]
    glosses: tuple[str, ...]

    @property
    def text(self) -> str:
        return "; ".join(self.glosses)


@dataclass(frozen=True)
class Entry:
    expr: str
    reading: str
    tags: tuple[str, ...]
    commonness: int
    senses: tuple[Sense, ...]
    score: int = 0
    matched_on: str = "expr"
    pos_match: bool = True

    @property
    def commonness_label(self) -> str:
        return ("", "common", "very common")[self.commonness]

    def summary(self, lang: str = "fr", *, limit: int = 3) -> str:
        preferred = [s for s in self.senses if s.lang == lang]
        chosen = preferred or list(self.senses)
        glosses: list[str] = []
        for sense in chosen:
            for gloss in sense.glosses:
                if gloss not in glosses:
                    glosses.append(gloss)
                if len(glosses) >= limit:
                    break
            if len(glosses) >= limit:
                break
        return "; ".join(glosses)


@dataclass(frozen=True)
class DictStatus:
    available: bool
    path: Optional[Path]
    source: str
    message: str
    entry_count: int = 0


_POS_COMPATIBLE: dict[str, frozenset[str]] = {
    "名詞": frozenset({"n", "pn", "n-suf", "n-pref", "n-t", "adj-no", "adj-na",
                       "adj-f", "ctr", "num", "vs", "exp", "unc"}),
    "代名詞": frozenset({"pn", "n"}),
    "動詞": frozenset({"v1", "v1-s", "v2a-s", "v4r", "v5aru", "v5b", "v5g", "v5k",
                       "v5k-s", "v5m", "v5n", "v5r", "v5r-i", "v5s", "v5t", "v5u",
                       "v5u-s", "vi", "vk", "vn", "vr", "vs", "vs-c", "vs-i",
                       "vs-s", "vt", "vz", "aux-v", "exp"}),
    "形容詞": frozenset({"adj-i", "adj-ix", "exp"}),
    "形状詞": frozenset({"adj-na", "adj-no", "adj-t", "adj-f", "n"}),
    "副詞": frozenset({"adv", "adv-to", "n", "exp"}),
    "連体詞": frozenset({"adj-pn", "adj-f"}),
    "接続詞": frozenset({"conj", "exp"}),
    "感動詞": frozenset({"int", "exp"}),
    "助詞": frozenset({"prt", "conj", "exp", "aux"}),
    "助動詞": frozenset({"aux-v", "aux", "aux-adj", "cop", "exp", "suf"}),
    "接頭辞": frozenset({"pref", "n-pref"}),
    "接尾辞": frozenset({"suf", "n-suf", "ctr", "adj-i", "adj-na"}),
}

_FUNCTION_POS = frozenset({"助詞", "助動詞"})

_VERY_COMMON_NEWS = 6


def _pos_codes(def_tags: str) -> tuple[str, ...]:
    return tuple(t for t in def_tags.split() if not t.isdigit())


def _commonness(term_tags: str) -> int:
    tags = set(term_tags.split())
    news = [int(t[4:-1]) for t in tags
            if t.startswith("news") and t.endswith("k") and t[4:-1].isdigit()]
    if "ichi" in tags or "spec" in tags or "spec1" in tags:
        return 2
    if news and min(news) <= _VERY_COMMON_NEWS:
        return 2
    if news or "gai" in tags or "spec2" in tags or "⭐" in tags:
        return 1
    return 0


def _clean_tags(term_tags: str) -> tuple[str, ...]:
    return tuple(t for t in term_tags.split() if t != "⭐")


class DictionaryProvider:
    REQUIRED_COLUMNS = frozenset(
        {"expr", "reading", "def_tags", "rules", "score", "glosses", "term_tags", "dict"}
    )

    def __init__(self, extra: Optional[Path] = None) -> None:
        self._extra = Path(extra) if extra else None
        self._status = DictStatus(
            available=False, path=None, source="none",
            message="The dictionary has not been looked for yet.",
        )
        self._conns: dict[int, sqlite3.Connection] = {}
        self._lock = threading.Lock()


    def candidates(self) -> list[tuple[str, Path]]:
        found: list[tuple[str, Path]] = []
        if self._extra is not None:
            found.append(("user", self._extra))
        found.append(("nanatools", config.DATA_DIR / "dict.db"))
        found.append((
            "dnareader",
            Path.home() / "Documents" / "dnareader" / "dicts" / "dictionary.db",
        ))
        return found


    @property
    def status(self) -> DictStatus:
        return self._status

    def resolve(self) -> DictStatus:
        problems: list[str] = []
        for source, path in self.candidates():
            if not path.exists():
                continue
            try:
                count = self._validate(path)
            except Exception as exc:
                problems.append(f"{path}: {exc}")
                log.warning("dictionary candidate rejected: %s (%s)", path, exc)
                continue
            self._status = DictStatus(
                available=True, path=path, source=source, entry_count=count,
                message=f"Dictionary: {path} ({count:,} entries, read-only).",
            )
            return self._status

        message = (
            "No dictionary found, so words are segmented and given readings but "
            "no meanings. Looked in:\n  "
            + "\n  ".join(str(p) for _, p in self.candidates())
        )
        if problems:
            message += "\n\nRejected:\n  " + "\n  ".join(problems)
        self._status = DictStatus(
            available=False, path=None, source="none", message=message,
        )
        return self._status

    def _validate(self, path: Path) -> int:
        con = sqlite3.connect(ro_uri(path), uri=True)
        try:
            columns = {row[1] for row in con.execute("PRAGMA table_info(terms)")}
            if not columns:
                raise ValueError("no 'terms' table")
            missing = self.REQUIRED_COLUMNS - columns
            if missing:
                raise ValueError("terms is missing " + ", ".join(sorted(missing)))
            return int(con.execute("SELECT count(*) FROM terms").fetchone()[0])
        finally:
            con.close()


    def _con(self) -> sqlite3.Connection:
        if not self._status.available or self._status.path is None:
            raise RuntimeError("no dictionary resolved")
        ident = threading.get_ident()
        with self._lock:
            con = self._conns.get(ident)
            if con is None:
                self._sweep_locked()
                con = sqlite3.connect(ro_uri(self._status.path), uri=True)
                self._conns[ident] = con
            return con

    def _sweep_locked(self) -> None:
        alive = {t.ident for t in threading.enumerate()}
        for ident in [i for i in self._conns if i not in alive]:
            self._conns.pop(ident, None)

    def close(self) -> None:
        ident = threading.get_ident()
        with self._lock:
            con = self._conns.pop(ident, None)
            self._conns.clear()
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass


    _SELECT = (
        "SELECT expr, reading, def_tags, rules, score, glosses, term_tags, dict "
        "FROM terms WHERE {column} IN ({marks})"
    )

    def lookup(self, key: str, *, pos: Optional[str] = None,
               limit: int = 8) -> list[Entry]:
        if not key:
            return []
        found = self.lookup_many([key], pos_by_key={key: pos} if pos else None)
        return found.get(key, [])[:limit]

    def lookup_many(
        self,
        keys: Sequence[str],
        *,
        pos_by_key: Optional[dict[str, Optional[str]]] = None,
        limit: int = 8,
    ) -> dict[str, list[Entry]]:
        wanted = [k for k in dict.fromkeys(keys) if k]
        if not wanted or not self._status.available:
            return {}
        pos_by_key = pos_by_key or {}

        rows_by_key = self._fetch(wanted, "expr")
        missing = [k for k in wanted if k not in rows_by_key]
        if missing:
            for key, rows in self._fetch(missing, "reading").items():
                rows_by_key[key] = rows

        result: dict[str, list[Entry]] = {}
        for key, rows in rows_by_key.items():
            entries = _build_entries(rows, matched_on=("expr" if rows and rows[0][0] == key
                                                       else "reading"))
            entries = _filter_by_pos(entries, pos_by_key.get(key))
            if entries:
                result[key] = entries[:limit]
        return result

    def _fetch(self, keys: Sequence[str], column: str) -> dict[str, list[tuple]]:
        con = self._con()
        rows_by_key: dict[str, list[tuple]] = {}
        for start in range(0, len(keys), _BATCH):
            chunk = keys[start:start + _BATCH]
            sql = self._SELECT.format(column=column, marks=",".join("?" * len(chunk)))
            for row in con.execute(sql, chunk):
                rows_by_key.setdefault(row[0] if column == "expr" else row[1], []).append(row)
        return rows_by_key


def _build_entries(rows: Iterable[tuple], *, matched_on: str) -> list[Entry]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for expr, reading, def_tags, _rules, score, glosses, term_tags, dictionary in rows:
        codes = _pos_codes(def_tags or "")
        if "forms" in codes or "sk" in codes or "rk" in codes:
            continue
        try:
            parsed = json.loads(glosses) if glosses else []
        except ValueError:
            continue
        texts = tuple(g for g in parsed if isinstance(g, str) and g.strip())
        if not texts:
            continue
        bucket = grouped.setdefault(
            (expr, reading or "", dictionary or ""),
            {"score": score or 0, "tags": term_tags or "", "senses": []},
        )
        bucket["score"] = max(bucket["score"], score or 0)
        bucket["senses"].append(Sense(lang=dictionary or "", pos_codes=codes, glosses=texts))

    entries = [
        Entry(
            expr=expr,
            reading=reading,
            tags=_clean_tags(bucket["tags"]),
            commonness=_commonness(bucket["tags"]),
            senses=tuple(bucket["senses"]),
            score=bucket["score"],
            matched_on=matched_on,
        )
        for (expr, reading, _dict), bucket in grouped.items()
    ]
    entries.sort(key=lambda e: (-e.score, len(e.expr)))
    return entries


def _filter_by_pos(entries: list[Entry], pos: Optional[str]) -> list[Entry]:
    if not pos:
        return entries
    allowed = _POS_COMPATIBLE.get(pos)
    if allowed is None:
        return entries

    def compatible(entry: Entry) -> bool:
        return any(set(sense.pos_codes) & allowed for sense in entry.senses)

    good = [e for e in entries if compatible(e)]
    if pos in _FUNCTION_POS:
        pruned: list[Entry] = []
        for entry in good:
            senses = tuple(s for s in entry.senses if set(s.pos_codes) & allowed)
            if senses:
                pruned.append(dataclasses.replace(entry, senses=senses))
        return pruned
    rest = [dataclasses.replace(e, pos_match=False) for e in entries if not compatible(e)]
    return good + rest


_REQUIRED_FEATURES = ("pos1", "pos2", "lemma", "orth", "orthBase", "kana",
                      "kanaBase", "goshu")

_GLUE_HOSTS = frozenset({"動詞", "形容詞", "名詞", "形状詞"})

_GLUE_POS = frozenset({"助動詞", "接尾辞"})

_NON_CONTENT_POS = frozenset({"補助記号", "記号", "空白"})

_NON_VOCABULARY_POS = _NON_CONTENT_POS | {"助詞", "助動詞"}


class FeatureLayoutError(RuntimeError):
    pass


@dataclass
class Word:
    surface: str
    start: int
    end: int
    reading: str
    ruby: str
    lemma: str
    lemma_reading: str
    pos: str
    pos2: str
    lemma_alt: str = ""
    space_before: str = ""
    tokens: tuple[str, ...] = ()
    entries: tuple[Entry, ...] = ()

    @property
    def is_content(self) -> bool:
        return self.pos not in _NON_CONTENT_POS and is_japanese(self.surface)

    @property
    def is_vocabulary(self) -> bool:
        return self.pos not in _NON_VOCABULARY_POS and is_japanese(self.surface)

    @property
    def best(self) -> Optional[Entry]:
        return self.entries[0] if self.entries else None

    @property
    def commonness(self) -> int:
        return self.entries[0].commonness if self.entries else 0

    def meaning(self, lang: str = "fr") -> str:
        entry = self.best
        return entry.summary(lang) if entry else ""


@dataclass
class Analysis:
    text: str
    words: list[Word]
    dict_status: DictStatus
    truncated: bool = False
    elapsed_ms: float = 0.0

    def vocabulary(self, *, min_commonness: int = 0) -> list[Word]:
        seen: dict[str, Word] = {}
        for word in self.words:
            if not word.is_vocabulary or not word.lemma:
                continue
            if word.commonness < min_commonness:
                continue
            seen.setdefault(word.lemma, word)
        return list(seen.values())

    def kanji(self) -> list[tuple[str, list[Word]]]:
        index: dict[str, list[Word]] = {}
        for word in self.words:
            if not word.is_content:
                continue
            for char in kanji_in(word.surface):
                bucket = index.setdefault(char, [])
                if all(w.lemma != word.lemma for w in bucket):
                    bucket.append(word)
        return list(index.items())

    def sentence_at(self, word: Word) -> str:
        breaks = "。．.!?！？\n"
        start = word.start
        while start > 0 and self.text[start - 1] not in breaks:
            start -= 1
        end = word.end
        while end < len(self.text) and self.text[end] not in breaks:
            end += 1
        if end < len(self.text) and self.text[end] in "。．.!?！？":
            end += 1
        return self.text[start:end].strip()


class Analyzer:
    def __init__(self, provider: Optional[DictionaryProvider] = None) -> None:
        self.provider = provider or DictionaryProvider()
        self._tagger: Any = None


    def warmup(self) -> DictStatus:
        self._ensure_tagger()
        if self.provider.status.source == "none" and self.provider.status.path is None:
            self.provider.resolve()
        return self.provider.status

    def _ensure_tagger(self) -> Any:
        if self._tagger is None:
            import fugashi

            tagger = fugashi.Tagger()
            probe = next(iter(tagger("日本語")), None)
            if probe is not None:
                fields = set(getattr(probe.feature, "_fields", ()))
                missing = set(_REQUIRED_FEATURES) - fields
                if missing:
                    raise FeatureLayoutError(
                        "This unidic build does not expose "
                        + ", ".join(sorted(missing))
                        + ". The analyzer reads orthBase and kana; without them "
                        "it would silently produce blank readings."
                    )
            self._tagger = tagger
        return self._tagger


    def analyze(self, text: str, *, lookup: bool = True,
                cancel: Any = None) -> Analysis:
        import time

        started = time.perf_counter()
        truncated = len(text) > MAX_ANALYSIS_CHARS
        if truncated:
            text = text[:MAX_ANALYSIS_CHARS]

        tagger = self._ensure_tagger()
        status = self.warmup()

        words = _glue(_tokenize(tagger, text))
        _check(cancel)

        if lookup and status.available and words:
            keys = {w.lemma: w.pos for w in words if w.is_content and w.lemma}
            try:
                found = self.provider.lookup_many(list(keys), pos_by_key=keys)
                alts = {w.lemma_alt: w.pos for w in words
                        if w.lemma_alt and w.lemma not in found}
                if alts:
                    found.update(self.provider.lookup_many(list(alts), pos_by_key=alts))
            except sqlite3.Error as exc:
                log.warning("dictionary lookup failed: %s", exc)
                found = {}
            for word in words:
                entries = found.get(word.lemma) or found.get(word.lemma_alt)
                if entries:
                    word.entries = tuple(entries)

        _check(cancel)
        return Analysis(
            text=text, words=words, dict_status=status, truncated=truncated,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )


def _check(cancel: Any) -> None:
    if cancel is not None and getattr(cancel, "cancelled", False):
        from ..jobs import JobCancelled

        raise JobCancelled()


@dataclass
class _Token:
    surface: str
    start: int
    space_before: str
    pos1: str
    pos2: str
    pos3: str
    orth_base: str
    kana: str
    kana_base: str


def _tokenize(tagger: Any, text: str) -> list[_Token]:
    tokens: list[_Token] = []
    cursor = 0
    for node in tagger(text):
        surface = node.surface
        if not surface:
            continue
        try:
            start = text.index(surface, cursor)
        except ValueError:
            start = cursor
        feature = node.feature
        tokens.append(_Token(
            surface=surface,
            start=start,
            space_before=text[cursor:start],
            pos1=getattr(feature, "pos1", "") or "",
            pos2=getattr(feature, "pos2", "") or "",
            pos3=getattr(feature, "pos3", "") or "",
            orth_base=getattr(feature, "orthBase", "") or surface,
            kana=getattr(feature, "kana", "") or "",
            kana_base=getattr(feature, "kanaBase", "") or "",
        ))
        cursor = start + len(surface)
    return tokens


_TE_HELPERS = frozenset({"いる", "ある", "おく", "しまう", "みる", "くる", "いく",
                         "ゆく", "やる", "もらう", "くれる", "あげる"})


def _glues_onto(token: _Token, host_pos: str, after_te: bool) -> bool:
    if host_pos not in _GLUE_HOSTS:
        return False
    if token.pos1 in _GLUE_POS or token.pos2 == "接尾":
        return True
    if token.pos1 == "形容詞" and token.pos2 == "非自立可能":
        return True
    if after_te and token.pos1 == "動詞" and token.orth_base in _TE_HELPERS:
        return True
    return (token.pos1 == "助詞" and token.pos2 == "接続助詞"
            and token.surface in ("て", "で"))


def _is_te(token: _Token) -> bool:
    return (token.pos1 == "助詞" and token.pos2 == "接続助詞"
            and token.surface in ("て", "で"))


def _is_suru(token: _Token) -> bool:
    return token.pos1 == "動詞" and token.orth_base == "する"


def _is_suru_noun(token: _Token) -> bool:
    return token.pos1 == "名詞" and "サ変" in (token.pos3 + token.pos2)


def _glue(tokens: list[_Token]) -> list[Word]:
    words: list[Word] = []
    head: Optional[_Token] = None
    parts: list[_Token] = []
    suru_noun = False

    def flush() -> None:
        nonlocal head, parts, suru_noun
        if head is None or not parts:
            head, parts, suru_noun = None, [], False
            return
        surface = "".join(p.surface for p in parts)
        reading = kata_to_hira("".join(p.kana for p in parts))
        lemma = head.orth_base + ("する" if suru_noun else "")
        lemma_reading = kata_to_hira(head.kana_base + ("スル" if suru_noun else ""))
        words.append(Word(
            surface=surface,
            start=parts[0].start,
            end=parts[-1].start + len(parts[-1].surface),
            reading=reading,
            ruby=ruby_for(surface, reading),
            lemma=lemma,
            lemma_reading=lemma_reading,
            pos="動詞" if suru_noun else head.pos1,
            pos2=head.pos2,
            lemma_alt=head.orth_base if suru_noun else "",
            space_before=parts[0].space_before,
            tokens=tuple(p.surface for p in parts),
        ))
        head, parts, suru_noun = None, [], False

    for token in tokens:
        if head is not None:
            if token.space_before:
                flush()
            else:
                host_pos = "動詞" if suru_noun else head.pos1
                if _glues_onto(token, host_pos, after_te=_is_te(parts[-1])):
                    parts.append(token)
                    continue
                if (not suru_noun and len(parts) == 1
                        and _is_suru_noun(head) and _is_suru(token)):
                    parts.append(token)
                    suru_noun = True
                    continue
                flush()
        head = token
        parts = [token]
    flush()
    return words


EXPORT_COLUMNS = ("expression", "reading", "meaning", "part of speech",
                  "commonness", "sentence")


def export_rows(analysis: Analysis, words: Sequence[Word], *,
                lang: str = "fr") -> Iterator[tuple[str, ...]]:
    for word in words:
        entry = word.best
        yield (
            word.lemma or word.surface,
            entry.reading if entry else word.lemma_reading or word.reading,
            word.meaning(lang),
            word.pos,
            entry.commonness_label if entry else "",
            analysis.sentence_at(word),
        )


def write_tsv(analysis: Analysis, words: Sequence[Word], path: Path, *,
              lang: str = "fr", header: bool = False) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        if header:
            fh.write("\t".join(EXPORT_COLUMNS) + "\n")
        for row in export_rows(analysis, words, lang=lang):
            fh.write("\t".join(c.replace("\t", " ").replace("\n", " ") for c in row) + "\n")
            count += 1
    return count


def write_csv(analysis: Analysis, words: Sequence[Word], path: Path, *,
              lang: str = "fr") -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(EXPORT_COLUMNS)
        for row in export_rows(analysis, words, lang=lang):
            writer.writerow(row)
            count += 1
    return count


__all__ = [
    "Analyzer", "Analysis", "Word", "Entry", "Sense", "DictStatus",
    "DictionaryProvider", "FeatureLayoutError",
    "kata_to_hira", "has_kanji", "has_kana", "is_japanese", "kanji_in",
    "ruby_for", "ro_uri", "export_rows", "write_tsv", "write_csv",
    "EXPORT_COLUMNS", "MAX_ANALYSIS_CHARS",
]
