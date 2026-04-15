from __future__ import annotations


def build_scenarios(
    *,
    current_price: float,
    total_score: float,
    growth_score: float | None = None,
    quality_score: float | None = None,
    risk_score: float | None = None,
) -> dict:
    growth_score = growth_score if growth_score is not None else total_score
    quality_score = quality_score if quality_score is not None else total_score
    risk_score = risk_score if risk_score is not None else 50.0

    upside_factor = 1 + max(0.05, (total_score - 50) / 100) + max(0.0, (growth_score - 55) / 200)
    downside_factor = max(0.65, 1 - ((risk_score + 20) / 200))
    quality_buffer = 1 + max(0.0, (quality_score - 50) / 250)

    bear_low = round(current_price * downside_factor * 0.92, 2)
    bear_high = round(current_price * downside_factor * 1.00, 2)
    base_low = round(current_price * 0.97 * quality_buffer, 2)
    base_high = round(current_price * (1.08 * quality_buffer + max(0, (total_score - 60) / 300)), 2)
    bull_low = round(current_price * (1.10 * upside_factor), 2)
    bull_high = round(current_price * (1.28 * upside_factor), 2)

    bull_probability = max(0.15, min(0.45, 0.20 + (total_score - 60) / 200 - max(0, risk_score - 60) / 400))
    bear_probability = max(0.15, min(0.40, 0.20 + max(0, risk_score - 55) / 180 - max(0, total_score - 70) / 250))
    base_probability = round(1 - bull_probability - bear_probability, 2)

    return {
        'bear_low': bear_low,
        'bear_high': bear_high,
        'base_low': base_low,
        'base_high': base_high,
        'bull_low': bull_low,
        'bull_high': bull_high,
        'bear_probability': round(bear_probability, 2),
        'base_probability': round(base_probability, 2),
        'bull_probability': round(bull_probability, 2),
    }
