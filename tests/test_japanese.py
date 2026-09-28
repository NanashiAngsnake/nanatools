from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from nanatools.core import japanese as jp


def _has_mecab() -> bool:
    try:
        import fugashi
    except Exception:
        return False
    return True


def _real_dictionary() -> Path | None:
    provider = jp.DictionaryProvider()
    for _source, path in provider.candidates():
        if path.exists():
            return path
    return None


requires_mecab = unittest.skipUnless(_has_mecab(), "fugashi/unidic-lite not installed")
requires_dict = unittest.skipUnless(_real_dictionary() is not None, "no dictionary present")


class KanaTests(unittest.TestCase):
    def test_katakana_becomes_hiragana(self):
        self.assertEqual(jp.kata_to_hira("キノウ"), "きのう")
        self.assertEqual(jp.kata_to_hira("シブヤ"), "しぶや")

    def test_long_vowel_mark_survives(self):
        self.assertEqual(jp.kata_to_hira("ラーメン"), "らーめん")

    def test_vu_converts_because_hiragana_has_one(self):
        self.assertEqual(jp.kata_to_hira("ヴ"), "ゔ")
        self.assertEqual(jp.kata_to_hira("ヴァイオリン"), "ゔぁいおりん")

    def test_non_kana_is_untouched(self):
        self.assertEqual(jp.kata_to_hira("abc 123 漢字"), "abc 123 漢字")

    def test_kanji_detection(self):
        self.assertTrue(jp.has_kanji("食べる"))
        self.assertFalse(jp.has_kanji("たべる"))
        self.assertFalse(jp.has_kanji("ラーメン"))
        self.assertFalse(jp.has_kanji("hello"))

    def test_kanji_in_is_ordered_and_deduplicated(self):
        self.assertEqual(jp.kanji_in("日本語の日"), ["日", "本", "語"])


class FuriganaTests(unittest.TestCase):
    def test_only_words_containing_kanji_get_ruby(self):
        self.assertEqual(jp.ruby_for("昨日", "キノウ"), "きのう")
        self.assertEqual(jp.ruby_for("美味しい", "オイシイ"), "おいしい")
        self.assertEqual(jp.ruby_for("ラーメン", "ラーメン"), "")
        self.assertEqual(jp.ruby_for("たべる", "タベル"), "")

    def test_reading_equal_to_surface_gets_nothing(self):
        self.assertEqual(jp.ruby_for("ひらがな", "ヒラガナ"), "")

    def test_missing_reading_is_not_invented(self):
        self.assertEqual(jp.ruby_for("漢字", ""), "")


class ReadOnlyUriTests(unittest.TestCase):
    def test_special_characters_are_percent_encoded(self):
        uri = jp.ro_uri(Path("C:/tmp/a b/c?d#e.db"))
        self.assertTrue(uri.startswith("file:/"))
        self.assertTrue(uri.endswith("?mode=ro"))
        body = uri[len("file:/"):-len("?mode=ro")]
        self.assertNotIn("?", body)
        self.assertNotIn("#", body)
        self.assertIn("%3F", body)
        self.assertIn("%23", body)
        self.assertIn("%20", body)

    def test_uri_is_absolute(self):
        uri = jp.ro_uri(Path("relative.db"))
        self.assertTrue(uri.startswith("file:"), uri)

        path_part = urllib.parse.unquote(uri[len("file:"):].split("?", 1)[0])
        self.assertTrue(
            Path(path_part.lstrip("/")).is_absolute(),
            f"relative path was not resolved: {path_part}",
        )
        self.assertTrue(path_part.endswith("relative.db"), path_part)


class ReadOnlyEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nanatools-jp-"))
        self.db = self.dir / "dictionary.db"
        con = sqlite3.connect(self.db)
        con.executescript(
            "CREATE TABLE terms (id INTEGER PRIMARY KEY, expr TEXT, reading TEXT,"
            " def_tags TEXT, rules TEXT, score INTEGER, glosses TEXT, seq INTEGER,"
            " term_tags TEXT, dict TEXT);"
            "CREATE TABLE tags (name TEXT, dict TEXT, category TEXT, notes TEXT);"
            "CREATE INDEX idx_expr ON terms(expr);"
        )
        con.execute(
            "INSERT INTO terms (expr, reading, def_tags, rules, score, glosses,"
            " seq, term_tags, dict) VALUES (?,?,?,?,?,?,?,?,?)",
            ("猫", "ねこ", "n", "", 1999800, '["chat"]', 1, "\u2b50 ichi news4k", "fr"),
        )
        con.execute(
            "INSERT INTO terms (expr, reading, def_tags, rules, score, glosses,"
            " seq, term_tags, dict) VALUES (?,?,?,?,?,?,?,?,?)",
            ("猫", "ねこ", "forms", "", 1999799, '["\u732b\uff08\u2605\uff09"]',
             1, "\u2b50 ichi news4k", "en"),
        )
        con.commit()
        con.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_writes_are_refused(self):
        con = sqlite3.connect(jp.ro_uri(self.db), uri=True)
        try:
            for statement in (
                "INSERT INTO terms (expr) VALUES ('x')",
                "UPDATE terms SET expr = 'x'",
                "DELETE FROM terms",
                "CREATE TABLE nope (a)",
                "DROP INDEX idx_expr",
            ):
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.OperationalError):
                        con.execute(statement)
        finally:
            con.close()

    def test_journal_mode_cannot_be_switched(self):
        con = sqlite3.connect(jp.ro_uri(self.db), uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("PRAGMA journal_mode=WAL").fetchone()
        finally:
            con.close()

    def test_no_sidecar_files_are_created(self):
        provider = _provider_for(self.db)
        provider.resolve()
        provider.lookup("猫")
        provider.close()
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["dictionary.db"])

    def test_forms_rows_are_not_offered_as_meanings(self):
        provider = _provider_for(self.db)
        provider.resolve()
        entries = provider.lookup("猫")
        self.addCleanup(provider.close)
        self.assertTrue(entries)
        glosses = [g for e in entries for s in e.senses for g in s.glosses]
        self.assertIn("chat", glosses)
        self.assertNotIn("猫（★）", glosses)

    def test_commonness_comes_from_term_tags(self):
        provider = _provider_for(self.db)
        provider.resolve()
        self.addCleanup(provider.close)
        entry = provider.lookup("猫")[0]
        self.assertEqual(entry.commonness, 2)
        self.assertEqual(entry.commonness_label, "very common")
        self.assertIn("ichi", entry.tags)
        self.assertNotIn("\u2b50", entry.tags)


def _provider_for(path: Path) -> jp.DictionaryProvider:
    class Fixed(jp.DictionaryProvider):
        def candidates(self):
            return [("user", path)]

    return Fixed()


class DegradationTests(unittest.TestCase):
    def test_missing_database_is_reported_not_raised(self):
        provider = _provider_for(Path("C:/definitely/not/here.db"))
        status = provider.resolve()
        self.assertFalse(status.available)
        self.assertEqual(status.source, "none")
        self.assertIn("not", status.message.lower())
        self.assertIsNone(status.path)

    def test_a_file_that_is_not_sqlite_is_rejected_with_a_reason(self):
        directory = Path(tempfile.mkdtemp(prefix="nanatools-jp-"))
        self.addCleanup(shutil.rmtree, directory, True)
        junk = directory / "dict.db"
        junk.write_bytes(b"this is not a database" * 40)
        status = _provider_for(junk).resolve()
        self.assertFalse(status.available)
        self.assertIn("not a database", status.message)

    def test_a_sqlite_file_with_the_wrong_schema_is_rejected(self):
        directory = Path(tempfile.mkdtemp(prefix="nanatools-jp-"))
        self.addCleanup(shutil.rmtree, directory, True)
        path = directory / "dict.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE terms (id INTEGER PRIMARY KEY, expr TEXT)")
        con.commit()
        con.close()
        status = _provider_for(path).resolve()
        self.assertFalse(status.available)
        self.assertIn("missing", status.message)

    def test_lookup_without_a_dictionary_returns_nothing(self):
        provider = _provider_for(Path("C:/definitely/not/here.db"))
        provider.resolve()
        self.assertEqual(provider.lookup_many(["猫"]), {})

    @requires_mecab
    def test_segmentation_still_works_with_no_dictionary(self):
        provider = _provider_for(Path("C:/definitely/not/here.db"))
        analysis = jp.Analyzer(provider).analyze("猫が好きです。")
        self.assertFalse(analysis.dict_status.available)
        surfaces = [w.surface for w in analysis.words]
        self.assertIn("猫", surfaces)
        self.assertEqual(analysis.words[0].ruby, "ねこ")
        self.assertTrue(all(not w.entries for w in analysis.words))


class EntryBuildingTests(unittest.TestCase):
    @staticmethod
    def _row(expr, reading, def_tags, glosses, tags="", dictionary="en", score=0):
        return (expr, reading, def_tags, "", score, glosses, tags, dictionary)

    def test_sense_number_prefix_is_not_a_pos_code(self):
        self.assertEqual(jp._pos_codes("1 prt"), ("prt",))
        self.assertEqual(jp._pos_codes("n vs vt"), ("n", "vs", "vt"))
        self.assertEqual(jp._pos_codes(""), ())

    def test_forms_and_search_only_rows_are_dropped(self):
        rows = [
            self._row("話し合う", "はなしあう", "v5u vt", '["to discuss"]'),
            self._row("話し合う", "はなしあう", "forms", '["話合う"]'),
            self._row("話し合う", "はなしあう", "sk", '["はなしあふ"]'),
        ]
        entries = jp._build_entries(rows, matched_on="expr")
        self.assertEqual(len(entries), 1)
        self.assertEqual([s.text for s in entries[0].senses], ["to discuss"])

    def test_empty_and_malformed_glosses_are_skipped(self):
        rows = [
            self._row("x", "x", "n", "not json"),
            self._row("x", "x", "n", "[]"),
            self._row("x", "x", "n", '["  "]'),
            self._row("x", "x", "n", '["real"]'),
        ]
        entries = jp._build_entries(rows, matched_on="expr")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].senses[0].glosses, ("real",))

    def test_commonness_tiers(self):
        self.assertEqual(jp._commonness(""), 0)
        self.assertEqual(jp._commonness("\u2b50 ichi news4k"), 2)
        self.assertEqual(jp._commonness("\u2b50 spec"), 2)
        self.assertEqual(jp._commonness("news3k"), 2)
        self.assertEqual(jp._commonness("news20k"), 1)
        self.assertEqual(jp._commonness("gai"), 1)

    def test_score_is_a_sort_key_not_a_frequency(self):
        rows = [self._row("x", "x", "n", '["a"]', tags="", score=1999800)]
        entry = jp._build_entries(rows, matched_on="expr")[0]
        self.assertEqual(entry.commonness, 0)
        self.assertEqual(entry.score, 1999800)

    def test_particles_lose_incompatible_entries_entirely(self):
        rows = [
            self._row("は", "は", "int", '["yes"]', score=-20200),
            self._row("は", "は", "1 prt", '["indicates sentence topic"]', score=999800),
        ]
        entries = jp._filter_by_pos(jp._build_entries(rows, matched_on="expr"), "助詞")
        glosses = [g for e in entries for s in e.senses for g in s.glosses]
        self.assertIn("indicates sentence topic", glosses)
        self.assertNotIn("yes", glosses)

    def test_a_particle_with_no_compatible_entry_gets_no_gloss(self):
        rows = [self._row("は", "は", "int", '["yes"]')]
        entries = jp._filter_by_pos(jp._build_entries(rows, matched_on="expr"), "助詞")
        self.assertEqual(entries, [])

    def test_a_content_word_keeps_mismatched_entries_but_is_flagged(self):
        rows = [self._row("こと", "こと", "prt", '["particle indicating a command"]')]
        entries = jp._filter_by_pos(jp._build_entries(rows, matched_on="expr"), "名詞")
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].pos_match)

    def test_compatible_entries_rank_first(self):
        rows = [
            self._row("x", "x", "prt", '["particle"]', score=999),
            self._row("y", "y", "n", '["noun"]', score=1),
        ]
        entries = jp._filter_by_pos(jp._build_entries(rows, matched_on="expr"), "名詞")
        self.assertEqual(entries[0].expr, "y")
        self.assertTrue(entries[0].pos_match)
        self.assertFalse(entries[1].pos_match)

    def test_summary_prefers_the_chosen_language_but_falls_back(self):
        entry = jp.Entry(
            expr="x", reading="x", tags=(), commonness=0, senses=(
                jp.Sense(lang="en", pos_codes=("n",), glosses=("cat",)),
            ),
        )
        self.assertEqual(entry.summary("fr"), "cat")
        entry_fr = jp.Entry(
            expr="x", reading="x", tags=(), commonness=0, senses=(
                jp.Sense(lang="en", pos_codes=("n",), glosses=("cat",)),
                jp.Sense(lang="fr", pos_codes=("n",), glosses=("chat",)),
            ),
        )
        self.assertEqual(entry_fr.summary("fr"), "chat")


@requires_mecab
class SegmentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = jp.Analyzer(_provider_for(Path("C:/definitely/not/here.db")))

    def words(self, text: str) -> list[jp.Word]:
        return self.analyzer.analyze(text, lookup=False).words

    def find(self, text: str, surface: str) -> jp.Word:
        for word in self.words(text):
            if word.surface == surface:
                return word
        self.fail(f"{surface!r} not among {[w.surface for w in self.words(text)]}")

    def test_auxiliaries_are_glued_onto_the_verb(self):
        word = self.find("行きました", "行きました")
        self.assertEqual(word.lemma, "行く")
        self.assertEqual(word.tokens, ("行き", "まし", "た"))
        self.assertEqual(word.ruby, "いきました")

    def test_causative_passive_stays_one_word(self):
        word = self.find("読ませられた", "読ませられた")
        self.assertEqual(word.lemma, "読む")
        self.assertEqual(len(word.tokens), 4)

    def test_te_form_stays_attached(self):
        word = self.find("走って帰る", "走って")
        self.assertEqual(word.lemma, "走る")

    def test_compound_verb_keeps_its_lemma(self):
        word = self.find("話し合った", "話し合った")
        self.assertEqual(word.lemma, "話し合う")

    def test_adjective_takes_its_auxiliaries(self):
        word = self.find("美味しかったです", "美味しかったです")
        self.assertEqual(word.lemma, "美味しい")

    def test_negation_is_glued(self):
        word = self.find("食べたくなかった", "食べたくなかった")
        self.assertEqual(word.lemma, "食べる")

    def test_te_iru_is_one_word(self):
        word = self.find("食べている", "食べている")
        self.assertEqual(word.lemma, "食べる")

    def test_suru_noun_merges_and_keeps_a_lookup_fallback(self):
        word = self.find("勉強しています", "勉強しています")
        self.assertEqual(word.lemma, "勉強する")
        self.assertEqual(word.lemma_alt, "勉強")
        self.assertEqual(word.pos, "動詞")

    def test_a_noun_followed_by_an_unrelated_suru_does_not_merge(self):
        words = [w.surface for w in self.words("本をする")]
        self.assertIn("本", words)

    def test_particles_are_not_glued_to_the_previous_word(self):
        surfaces = [w.surface for w in self.words("猫が好き")]
        self.assertIn("が", surfaces)

    def test_suffixes_are_glued(self):
        word = self.find("田中さん", "田中さん")
        self.assertEqual(word.lemma, "田中")


@requires_mecab
class LemmaSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = jp.Analyzer(_provider_for(Path("C:/definitely/not/here.db")))

    def test_proper_noun_keeps_its_kanji(self):
        word = self.analyzer.analyze("渋谷", lookup=False).words[0]
        self.assertEqual(word.lemma, "渋谷")

    def test_loanword_has_no_etymology_suffix(self):
        word = self.analyzer.analyze("ラーメン", lookup=False).words[0]
        self.assertEqual(word.lemma, "ラーメン")
        self.assertNotIn("-", word.lemma)

    def test_unidic_feature_layout_is_asserted(self):
        self.assertIn("orthBase", jp._REQUIRED_FEATURES)
        self.assertIn("kanaBase", jp._REQUIRED_FEATURES)


@requires_mecab
class OffsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = jp.Analyzer(_provider_for(Path("C:/definitely/not/here.db")))

    def test_offsets_point_back_into_the_source(self):
        text = "昨日、友達と渋谷で ラーメンを食べた。"
        for word in self.analyzer.analyze(text, lookup=False).words:
            self.assertEqual(text[word.start:word.end], word.surface)

    def test_whitespace_is_preserved_for_the_renderer(self):
        analysis = self.analyzer.analyze("猫\n\n犬", lookup=False)
        dog = [w for w in analysis.words if w.surface == "犬"][0]
        self.assertEqual(dog.space_before.count("\n"), 2)

    def test_sentence_extraction(self):
        text = "猫が好きです。犬も好きです。"
        analysis = self.analyzer.analyze(text, lookup=False)
        dog = [w for w in analysis.words if w.surface == "犬"][0]
        self.assertEqual(analysis.sentence_at(dog), "犬も好きです。")

    def test_over_long_text_is_truncated_and_says_so(self):
        analysis = self.analyzer.analyze("猫" * (jp.MAX_ANALYSIS_CHARS + 50),
                                         lookup=False)
        self.assertTrue(analysis.truncated)
        self.assertEqual(len(analysis.text), jp.MAX_ANALYSIS_CHARS)

    def test_empty_text_is_harmless(self):
        analysis = self.analyzer.analyze("", lookup=False)
        self.assertEqual(analysis.words, [])
        self.assertEqual(analysis.vocabulary(), [])
        self.assertEqual(analysis.kanji(), [])


@requires_mecab
class VocabularyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = jp.Analyzer(_provider_for(Path("C:/definitely/not/here.db")))
        cls.analysis = cls.analyzer.analyze(
            "猫が好きです。猫と犬。昨日、友達と渋谷へ行きました。", lookup=False)

    def test_vocabulary_is_deduplicated_in_first_appearance_order(self):
        lemmas = [w.lemma for w in self.analysis.vocabulary()]
        self.assertEqual(len(lemmas), len(set(lemmas)))
        self.assertLess(lemmas.index("猫"), lemmas.index("犬"))

    def test_particles_and_punctuation_are_not_vocabulary(self):
        lemmas = [w.lemma for w in self.analysis.vocabulary()]
        for excluded in ("が", "と", "。", "、"):
            self.assertNotIn(excluded, lemmas)

    def test_kanji_view_maps_each_kanji_to_the_words_using_it(self):
        index = dict(self.analysis.kanji())
        self.assertIn("猫", index)
        self.assertIn("友", index)
        self.assertEqual([w.lemma for w in index["猫"]], ["猫"])
        self.assertNotIn("が", index)

    def test_kanji_view_lists_each_word_once_per_kanji(self):
        index = dict(self.analysis.kanji())
        for _char, words in index.items():
            lemmas = [w.lemma for w in words]
            self.assertEqual(len(lemmas), len(set(lemmas)))


@requires_mecab
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nanatools-jp-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        analyzer = jp.Analyzer(_provider_for(Path("C:/definitely/not/here.db")))
        self.analysis = analyzer.analyze("猫が好きです。犬も好きです。", lookup=False)
        self.rows = self.analysis.vocabulary()

    def test_tsv_has_no_header_by_default(self):
        path = self.dir / "vocab.tsv"
        count = jp.write_tsv(self.analysis, self.rows, path)
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(count, len(lines))
        self.assertNotIn("expression", lines[0])

    def test_tsv_header_is_opt_in(self):
        path = self.dir / "vocab.tsv"
        jp.write_tsv(self.analysis, self.rows, path, header=True)
        first = path.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first.split("\t"), list(jp.EXPORT_COLUMNS))

    def test_tsv_rows_never_contain_a_stray_tab_or_newline(self):
        path = self.dir / "vocab.tsv"
        count = jp.write_tsv(self.analysis, self.rows, path)
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), count)
        for line in lines:
            self.assertEqual(len(line.split("\t")), len(jp.EXPORT_COLUMNS))

    def test_csv_is_written_with_a_bom_so_excel_shows_kana(self):
        path = self.dir / "vocab.csv"
        jp.write_csv(self.analysis, self.rows, path)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_export_carries_the_sentence_the_word_came_from(self):
        rows = list(jp.export_rows(self.analysis, self.rows))
        by_lemma = {row[0]: row for row in rows}
        self.assertIn("犬", by_lemma)
        self.assertEqual(by_lemma["犬"][-1], "犬も好きです。")

    def test_export_creates_missing_directories(self):
        path = self.dir / "nested" / "deeper" / "vocab.tsv"
        jp.write_tsv(self.analysis, self.rows, path)
        self.assertTrue(path.exists())


@requires_mecab
@requires_dict
class RealDictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = jp.Analyzer()
        cls.status = cls.analyzer.warmup()

    def test_the_dictionary_resolves_and_is_described(self):
        self.assertTrue(self.status.available)
        self.assertGreater(self.status.entry_count, 1000)
        self.assertIn("read-only", self.status.message)

    def test_a_conjugated_verb_resolves_through_its_lemma(self):
        analysis = self.analyzer.analyze("昨日渋谷へ行きました。")
        word = [w for w in analysis.words if w.surface == "行きました"][0]
        self.assertEqual(word.lemma, "行く")
        self.assertTrue(word.entries)
        self.assertTrue(word.meaning("fr"))

    def test_a_suru_verb_resolves_through_the_bare_noun(self):
        analysis = self.analyzer.analyze("毎日勉強しています。")
        word = [w for w in analysis.words if w.lemma == "勉強する"][0]
        self.assertTrue(word.entries, "勉強する should fall back to 勉強")
        self.assertEqual(word.entries[0].expr, "勉強")

    def test_the_topic_particle_does_not_resolve_to_an_interjection(self):
        analysis = self.analyzer.analyze("彼は学生です。")
        word = [w for w in analysis.words if w.surface == "は"][0]
        meaning = word.meaning("fr").lower()
        self.assertNotIn("oui", meaning)
        if word.entries:
            codes = {c for s in word.entries[0].senses for c in s.pos_codes}
            self.assertIn("prt", codes)

    def test_a_common_word_is_badged_common(self):
        analysis = self.analyzer.analyze("猫")
        self.assertEqual(analysis.words[0].commonness, 2)

    def test_reading_the_dictionary_leaves_no_sidecar_files(self):
        path = self.status.path
        self.assertIsNotNone(path)
        self.analyzer.analyze("猫と犬と鳥。")
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(path) + suffix).exists(),
                             f"a {suffix} file appeared beside another project's db")

    def test_analysis_of_a_realistic_paragraph_is_fast(self):
        text = ("昨日、友達と渋谷でラーメンを食べに行きました。"
                "とても美味しかったです。彼は毎日日本語を勉強しています。") * 12
        analysis = self.analyzer.analyze(text)
        self.assertGreater(len(analysis.words), 250)
        self.assertLess(analysis.elapsed_ms, 1000)


if __name__ == "__main__":
    unittest.main()
