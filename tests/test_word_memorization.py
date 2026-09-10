from exercises import Difficulty, ExerciseRegistry
from exercises.word_memorization import (
    NEXT_COUNT, _answer_note, get_placement_recommendation, is_fuzzy_match,
    levenshtein_distance, should_offer_speed_run,
)
from bot.word_memo import _build_list_quiz_items, _build_quiz_items


# ---- fuzzy matching --------------------------------------------------------

def test_levenshtein_basics():
    assert levenshtein_distance("", "abc") == 3
    assert levenshtein_distance("kitten", "sitting") == 3
    assert levenshtein_distance("same", "same") == 0


def test_fuzzy_accepts_typos_within_two_edits():
    assert is_fuzzy_match("elephnat", "elephant")      # transposition = 2 edits
    assert is_fuzzy_match("  Elephant ", "elephant")   # case + whitespace
    assert not is_fuzzy_match("elefantt", "elephant")  # 3 edits


def test_fuzzy_disabled_for_short_words():
    assert not is_fuzzy_match("cat", "cot")
    assert is_fuzzy_match("cat", "cat")


# ---- quiz item builders -----------------------------------------------------

def test_pairs_quiz_follows_study_order_and_shows_one_side():
    pairs = [("a", "b"), ("c", "d"), ("e", "f")]
    items = _build_quiz_items(pairs)
    assert [i["pair_index"] for i in items] == [0, 1, 2]
    for item, pair in zip(items, pairs):
        assert {item["shown_word"], item["expected"]} == set(pair)
        assert item["shown_word"] != item["expected"]


def test_list_quiz_chains_every_position():
    words = ["w0", "w1", "w2", "w3"]
    items = _build_list_quiz_items(words)
    assert len(items) == len(words)
    assert items[0] == {"pair_index": 0, "direction": "first",
                        "shown_word": None, "expected": "w0"}
    for i in range(1, len(words)):
        assert items[i]["direction"] == "next"
        assert items[i]["shown_word"] == words[i - 1]
        assert items[i]["expected"] == words[i]
        assert items[i]["pair_index"] == i


# ---- progression helpers ----------------------------------------------------

def test_placement_thresholds():
    assert get_placement_recommendation(0) == (Difficulty.BEGINNER, 5)
    assert get_placement_recommendation(49.9) == (Difficulty.BEGINNER, 5)
    assert get_placement_recommendation(50) == (Difficulty.BEGINNER, 10)
    assert get_placement_recommendation(75) == (Difficulty.INTERMEDIATE, 10)
    assert get_placement_recommendation(90) == (Difficulty.ADVANCED, 10)


def test_speed_run_only_at_top_of_ladder():
    assert NEXT_COUNT.get(100) is None
    assert should_offer_speed_run(100, 95, speed_mode=False)
    assert not should_offer_speed_run(100, 95, speed_mode=True)   # already fast
    assert not should_offer_speed_run(100, 85, speed_mode=False)  # below 90
    assert not should_offer_speed_run(10, 95, speed_mode=False)   # more pairs first


def test_answer_note_wording():
    assert _answer_note(None) == ""
    assert _answer_note({"correct": True, "answer": "x"}) == ""
    assert "_skipped_" in _answer_note({"correct": False, "answer": "(skipped)"})
    assert "_timed out_" in _answer_note({"correct": False, "answer": "(timed out)"})
    assert "you said: _wrong_" in _answer_note({"correct": False, "answer": "wrong"})
    assert "no answer" in _answer_note({"correct": False, "answer": ""})


# ---- generation -------------------------------------------------------------

async def test_generate_avoids_recent_words_and_respects_count():
    ex = ExerciseRegistry.get("word_memo")
    first = await ex.generate(Difficulty.BEGINNER, {"count": 10, "recent_words": []})
    pairs = first.additional_data["pairs"]
    assert len(pairs) == 10
    used = [w for p in pairs for w in p]
    assert len(set(used)) == 20  # no word twice inside one test

    second = await ex.generate(Difficulty.BEGINNER, {"count": 10, "recent_words": used})
    again = {w for p in second.additional_data["pairs"] for w in p}
    assert not again & set(used)


async def test_generate_list_format():
    ex = ExerciseRegistry.get("word_memo")
    res = await ex.generate(Difficulty.ADVANCED, {"count": 7, "format": "list"})
    words = res.additional_data["words"]
    assert len(words) == 7 and len(set(words)) == 7
    assert "order matters" in res.text_content


def test_results_text_marks_fuzzy_and_legend():
    ex = ExerciseRegistry.get("word_memo")
    pairs = [("apple", "river"), ("candle", "stone")]
    results = [
        {"pair_index": 0, "correct": True, "fuzzy": True, "answer": "rivr"},
        {"pair_index": 1, "correct": False, "fuzzy": False, "answer": "(timed out)"},
    ]
    text = ex.format_test_results(pairs, results, Difficulty.BEGINNER)
    assert "Score: *1/2*" in text
    assert "✅~" in text and "minor typo" in text
    assert "❌" in text and "_timed out_" in text
