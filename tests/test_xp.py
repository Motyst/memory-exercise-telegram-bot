from gamification.xp import (
    BASE_XP_PER_PAIR, EFFICIENCY_FLOOR, HARD_STREAK_BONUS_CAP, MIN_XP_SCORE_PCT,
    PERFECT_BONUS, challenge_rating, compute_test_xp, expected_challenge,
    level_from_xp, render_progress_bar, xp_for_next_level,
)


def test_level_curve_starts_at_80_and_grows():
    assert xp_for_next_level(1) == 80
    assert xp_for_next_level(2) > xp_for_next_level(1)
    assert xp_for_next_level(20) > xp_for_next_level(10)


def test_level_from_xp_boundaries():
    assert level_from_xp(0) == (1, 0, 80)
    assert level_from_xp(79) == (1, 79, 80)
    level, into, need = level_from_xp(80)
    assert (level, into) == (2, 0)
    assert need == xp_for_next_level(2)


def test_level_from_xp_is_monotonic():
    levels = [level_from_xp(x)[0] for x in range(0, 20_000, 250)]
    assert levels == sorted(levels)


def test_challenge_rating_multipliers():
    assert challenge_rating(10, "beginner", False) == 10
    assert challenge_rating(10, "advanced", False) == 15
    assert challenge_rating(10, "beginner", True) == 13
    assert challenge_rating(10, "unknown", False) == 10  # unknown difficulty = ×1


def test_failed_test_earns_nothing_and_breaks_streak():
    res = compute_test_xp(
        pairs=100, difficulty="advanced", speed_mode=True,
        score_pct=MIN_XP_SCORE_PCT - 1, level=1, hard_streak=4,
    )
    assert res.xp == 0
    assert res.new_hard_streak == 0
    assert res.is_hard is False


def test_perfect_small_test_at_level_one():
    res = compute_test_xp(
        pairs=10, difficulty="beginner", speed_mode=False,
        score_pct=100, level=1, hard_streak=0,
    )
    # CR 10 ≥ expected 5 → full efficiency; 4 × 10 × 1.0² × 1.2
    assert res.xp == round(BASE_XP_PER_PAIR * 10 * PERFECT_BONUS)
    assert res.is_hard is True
    assert res.new_hard_streak == 1
    assert res.streak_multiplier == 1.0
    assert res.efficiency == 1.0


def test_hard_streak_bonus_stacks_and_caps():
    second = compute_test_xp(
        pairs=10, difficulty="beginner", speed_mode=False,
        score_pct=100, level=1, hard_streak=1,
    )
    assert second.streak_multiplier == 1.1
    deep = compute_test_xp(
        pairs=10, difficulty="beginner", speed_mode=False,
        score_pct=100, level=1, hard_streak=50,
    )
    assert deep.streak_multiplier == 1.0 + HARD_STREAK_BONUS_CAP


def test_hard_streak_needs_performance_not_just_attempt():
    res = compute_test_xp(
        pairs=50, difficulty="advanced", speed_mode=True,
        score_pct=60, level=1, hard_streak=3,
    )
    assert res.xp > 0
    assert res.is_hard is False        # 60% < HARD_STREAK_MIN_PCT
    assert res.new_hard_streak == 0


def test_outgrown_exercise_hits_efficiency_floor():
    level = 50
    assert expected_challenge(level) > 100
    res = compute_test_xp(
        pairs=5, difficulty="beginner", speed_mode=False,
        score_pct=100, level=level, hard_streak=0,
    )
    assert res.efficiency == EFFICIENCY_FLOOR
    assert res.xp == round(BASE_XP_PER_PAIR * 5 * EFFICIENCY_FLOOR * PERFECT_BONUS)


def test_accuracy_is_superlinear():
    full = compute_test_xp(pairs=20, difficulty="beginner", speed_mode=False,
                           score_pct=100, level=1, hard_streak=0).xp
    eighty = compute_test_xp(pairs=20, difficulty="beginner", speed_mode=False,
                             score_pct=80, level=1, hard_streak=0).xp
    # 0.8² = 0.64 of the (non-perfect-bonus) base, well under 80%
    assert eighty < 0.7 * full / PERFECT_BONUS


def test_progress_bar_shape():
    assert render_progress_bar(0, 100) == "▱" * 10
    assert render_progress_bar(50, 100) == "▰" * 5 + "▱" * 5
    assert render_progress_bar(100, 100) == "▰" * 10
    assert len(render_progress_bar(7, 0)) == 10  # no division by zero
