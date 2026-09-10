"""
Quiz engine under concurrency. These replay the races fixed on 2026-09-10:
the final-question grace window, a timer firing while an answer holds the
lock, and duplicate study countdowns. Fake bot latency is what makes the
races reproducible.
"""

import asyncio

from exercises import Difficulty
from bot import quiz_engine as qe
from bot.word_memo import generate_word_memo_test
from tests.conftest import fresh_telegram_id
from tests.fakes import FakeBot, FakeContext, FakeQuery


def _two_question_state(index: int, results: list) -> dict:
    return {
        "test_active": True, "test_exercise_type": "word_memo",
        "test_pairs": [("apple", "river"), ("candle", "stone")],
        "test_format": "pairs",
        "test_quiz_items": [
            {"pair_index": 0, "shown_word": "apple", "expected": "river"},
            {"pair_index": 1, "shown_word": "candle", "expected": "stone"},
        ],
        "test_current_index": index, "test_results": results,
        "test_difficulty": Difficulty.BEGINNER, "baseline_results": [],
        "test_round_mode": "test",
    }


def _answered(pair_index, shown, expected, answer, correct=True):
    return {"pair_index": pair_index, "shown_word": shown, "expected": expected,
            "answer": answer, "correct": correct, "fuzzy": False, "direction": None}


async def test_happy_path_renders_results():
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    ctx.state = _two_question_state(0, [])
    await qe.record_answer(ctx, uid, "river", user_id=uid)
    await qe.record_answer(ctx, uid, "stoen", user_id=uid)   # fuzzy
    st = ctx.state
    assert st["test_active"] is False
    assert [r["correct"] for r in st["last_test_results"]] == [True, True]
    assert st["last_test_results"][1]["fuzzy"] is True
    assert "Score: *2/2*" in ctx.bot.sent[-1]


async def test_final_question_grace_credits_late_answer(monkeypatch):
    monkeypatch.setattr(qe, "ANSWER_TIMEOUT_GRACE", 0.6)
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    ctx.state = _two_question_state(1, [_answered(0, "apple", "river", "river")])

    timeout = asyncio.create_task(qe._question_timeout_callback(ctx.timeout_job(uid, 1)))
    await asyncio.sleep(0.2)
    late = asyncio.create_task(qe.record_answer(ctx, uid, "stone", user_id=uid))
    await asyncio.gather(timeout, late)

    final = ctx.state["last_test_results"]
    assert final[1]["answer"] == "stone" and final[1]["correct"] is True
    assert "Score: *2/2*" in ctx.bot.sent[-1]


async def test_grace_is_disabled_for_list_format(monkeypatch):
    monkeypatch.setattr(qe, "ANSWER_TIMEOUT_GRACE", 0.4)
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    st = _two_question_state(1, [_answered(0, "apple", "river", "river")])
    st["test_format"] = "list"
    st["test_pairs"] = ["apple", "river"]
    st["test_quiz_items"][1]["direction"] = "next"
    ctx.state = st
    timeout = asyncio.create_task(qe._question_timeout_callback(ctx.timeout_job(uid, 1)))
    await asyncio.sleep(0.1)
    await qe.record_answer(ctx, uid, "river", user_id=uid)
    await timeout
    assert ctx.state["last_test_results"][1]["answer"] == "(timed out)"


async def test_timer_firing_during_inflight_answer_is_discarded():
    uid = fresh_telegram_id()
    ctx = FakeContext(uid, bot=FakeBot(delete_delay=0.3))
    ctx.state = _two_question_state(0, [])

    answer = asyncio.create_task(
        qe.record_answer(ctx, uid, "river", user_id=uid, answer_message_id=555)
    )
    await asyncio.sleep(0.05)   # answer holds the lock, awaiting delete_message
    timeout = asyncio.create_task(qe._question_timeout_callback(ctx.timeout_job(uid, 0)))
    await asyncio.gather(answer, timeout)

    st = ctx.state
    assert [(r["pair_index"], r["answer"]) for r in st["test_results"]] == [(0, "river")]
    assert st["test_current_index"] == 1 and st["test_active"] is True
    assert sum("Question 2/2" in t for t in ctx.bot.sent) == 1


async def test_stale_timeout_after_answer_is_ignored():
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    ctx.state = _two_question_state(0, [])
    await qe.record_answer(ctx, uid, "river", user_id=uid)
    await qe._question_timeout_callback(ctx.timeout_job(uid, 0))   # armed for Q1, Q2 live
    assert len(ctx.state["test_results"]) == 1


async def test_answers_outside_a_test_are_ignored():
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    ctx.state = {"test_active": False}
    await qe.record_answer(ctx, uid, "anything", user_id=uid)
    assert ctx.bot.sent == []


async def test_fresh_mind_flag_is_consumed_even_when_no_xp():
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    st = _two_question_state(2, [
        _answered(0, "apple", "river", "x", correct=False),
        _answered(1, "candle", "stone", "y", correct=False),
    ])
    st["fresh_mind_pending"] = True
    ctx.state = st
    await qe._show_test_results(ctx, uid, st)
    assert "fresh_mind_pending" not in st


async def test_only_one_study_timer_survives_double_start():
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    ctx.state = {"format": "pairs", "speed_mode": False}
    q = FakeQuery(uid)
    await generate_word_memo_test(q, ctx, Difficulty.BEGINNER, 5)
    await generate_word_memo_test(q, ctx, Difficulty.BEGINNER, 5)
    assert len(ctx.job_queue.jobs) == 2
    assert len(ctx.job_queue.live("quiz_timer_")) == 1


async def test_study_timer_firing_twice_starts_quiz_once():
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    ctx.state = {"format": "pairs", "speed_mode": False}
    await generate_word_memo_test(FakeQuery(uid), ctx, Difficulty.BEGINNER, 5)
    ctx.job = ctx.job_queue.jobs[0]
    await qe.start_quiz_after_timer(ctx)
    await qe.start_quiz_after_timer(ctx)
    assert sum("Question 1/5" in t for t in ctx.bot.sent) == 1
    assert len(ctx.job_queue.live("question_timer_")) == 1


async def test_retry_round_reasks_only_mistakes_in_study_order():
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    ctx.state = {
        "test_format": "pairs", "test_difficulty": Difficulty.BEGINNER,
        "last_test_pairs": [("a", "b"), ("c", "d"), ("e", "f")],
        "last_test_results": [
            _answered(2, "e", "f", "zzz", correct=False),
            _answered(0, "a", "b", "b"),
            _answered(1, "c", "d", "qqq", correct=False),
        ],
    }
    await qe.start_retry_mistakes(FakeQuery(uid), ctx)
    st = ctx.state
    assert [i["pair_index"] for i in st["test_quiz_items"]] == [1, 2]
    assert st["test_round_mode"] == "retry" and st["test_retry_rounds"] == 1
    assert len(st["baseline_results"]) == 1


async def test_reverse_round_flips_columns_and_throttles_second_time():
    uid = fresh_telegram_id()
    ctx = FakeContext(uid)
    ctx.state = {
        "test_format": "pairs", "test_difficulty": Difficulty.BEGINNER,
        "last_test_pairs": [("a", "b")],
        "last_test_results": [_answered(0, "a", "b", "b")],
        "test_reverse_rounds": 0,
    }
    await qe.start_reverse_quiz(FakeQuery(uid), ctx)
    st = ctx.state
    assert st["test_quiz_items"][0] == {"pair_index": 0, "shown_word": "b", "expected": "a"}
    assert st["test_round_mode"] == "reverse"
    await qe.start_reverse_quiz(FakeQuery(uid), ctx)
    assert ctx.state["test_round_mode"] == "reverse_extra"
