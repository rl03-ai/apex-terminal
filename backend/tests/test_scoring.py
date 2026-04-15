from app.services.scoring.engine import compute_total_score, derive_state


def test_total_score_range() -> None:
    score = compute_total_score(growth=80, quality=70, narrative=60, market=55, risk=25)
    assert 0 <= score <= 100


def test_state_mapping() -> None:
    assert derive_state(82) == 'active_setup'
    assert derive_state(60) == 'emerging'
