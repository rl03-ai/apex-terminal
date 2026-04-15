from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetEvent, AssetScoreDaily, ScannerResult


@dataclass(slots=True)
class ScannerProfile:
    scanner_type: str
    min_total: float
    min_growth: float | None = None
    min_quality: float | None = None
    min_narrative: float | None = None
    min_market: float | None = None
    max_risk: float | None = None


SCANNER_PROFILES = {
    'repricing': ScannerProfile('repricing', min_total=70, min_growth=65, min_market=55, max_risk=65),
    'early_growth': ScannerProfile('early_growth', min_total=64, min_growth=70, min_quality=45),
    'quality_compounder': ScannerProfile('quality_compounder', min_total=68, min_quality=75, max_risk=55),
    'narrative': ScannerProfile('narrative', min_total=60, min_narrative=72, min_growth=50),
    'speculative': ScannerProfile('speculative', min_total=55, min_narrative=60, max_risk=85),
}


def compute_priority_score(total_score: float, score_change_7d: float = 0.0, catalyst_proximity: float = 0.0, tradability: float = 50.0) -> float:
    return round((0.50 * total_score) + (0.20 * score_change_7d) + (0.15 * catalyst_proximity) + (0.15 * tradability), 2)


def sort_candidates(candidates: Iterable[dict]) -> list[dict]:
    return sorted(candidates, key=lambda item: item.get('priority_score', 0), reverse=True)


def _passes_profile(score: AssetScoreDaily, profile: ScannerProfile) -> bool:
    return all(
        [
            score.total_score >= profile.min_total,
            profile.min_growth is None or score.growth_score >= profile.min_growth,
            profile.min_quality is None or score.quality_score >= profile.min_quality,
            profile.min_narrative is None or score.narrative_score >= profile.min_narrative,
            profile.min_market is None or score.market_score >= profile.min_market,
            profile.max_risk is None or score.risk_score <= profile.max_risk,
        ]
    )


def _catalyst_proximity_score(events: list[AssetEvent]) -> float:
    from datetime import timezone
    now = datetime.now(tz=timezone.utc)
    def _aware(dt):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    upcoming = [e for e in events if now <= _aware(e.event_date) <= now + timedelta(days=45)]
    if not upcoming:
        return 35.0
    weighted = sum((e.importance_score or 50.0) for e in upcoming) / len(upcoming)
    return max(35.0, min(100.0, round(weighted, 2)))


def _tradability_score(asset: Asset) -> float:
    market_cap = asset.market_cap or 0
    if market_cap >= 50_000_000_000:
        return 90.0
    if market_cap >= 10_000_000_000:
        return 80.0
    if market_cap >= 1_000_000_000:
        return 70.0
    if market_cap >= 300_000_000:
        return 55.0
    return 40.0


def _score_change_7d(db: Session, asset_id: str, as_of: date) -> float:
    current = db.query(AssetScoreDaily).filter(AssetScoreDaily.asset_id == asset_id, AssetScoreDaily.date == as_of).first()
    if not current:
        return 0.0
    previous = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.asset_id == asset_id, AssetScoreDaily.date <= as_of - timedelta(days=7))
        .order_by(AssetScoreDaily.date.desc())
        .first()
    )
    return round(current.total_score - previous.total_score, 2) if previous else 0.0


def build_candidate_payload(db: Session, asset: Asset, score: AssetScoreDaily, as_of: date) -> dict:
    events = db.query(AssetEvent).filter(AssetEvent.asset_id == asset.id).order_by(AssetEvent.event_date.desc()).limit(20).all()
    change_7d = _score_change_7d(db, asset.id, as_of)
    catalyst_proximity = _catalyst_proximity_score(events)
    tradability = _tradability_score(asset)
    priority = compute_priority_score(score.total_score, change_7d, catalyst_proximity, tradability)
    why = {
        'score_change_7d': change_7d,
        'catalyst_proximity': catalyst_proximity,
        'tradability': tradability,
        'highlights': [
            f'Growth {score.growth_score:.1f}',
            f'Quality {score.quality_score:.1f}',
            f'Narrative {score.narrative_score:.1f}',
            f'Market {score.market_score:.1f}',
        ],
    }
    return {
        'asset_id': asset.id,
        'total_score': score.total_score,
        'priority_score': priority,
        'state': score.state,
        'why_selected': why,
    }


def _resolve_latest_score_date(db: Session, as_of: date | None = None) -> date:
    if as_of is not None:
        return as_of
    latest = db.query(AssetScoreDaily).order_by(AssetScoreDaily.date.desc()).first()
    return latest.date if latest else date.today()


def refresh_scanner(db: Session, scanner_type: str, as_of: date | None = None) -> list[ScannerResult]:
    as_of = _resolve_latest_score_date(db, as_of)
    profile = SCANNER_PROFILES.get(scanner_type)
    if not profile:
        raise ValueError(f'Unknown scanner type: {scanner_type}')

    db.query(ScannerResult).filter(ScannerResult.date == as_of, ScannerResult.scanner_type == scanner_type).delete()
    rows = db.query(Asset, AssetScoreDaily).join(AssetScoreDaily, AssetScoreDaily.asset_id == Asset.id).filter(AssetScoreDaily.date == as_of).all()
    candidates = []
    for asset, score in rows:
        if _passes_profile(score, profile):
            candidates.append(build_candidate_payload(db, asset, score, as_of))

    sorted_candidates = sort_candidates(candidates)
    results: list[ScannerResult] = []
    for idx, candidate in enumerate(sorted_candidates, start=1):
        result = ScannerResult(
            date=as_of,
            scanner_type=scanner_type,
            asset_id=candidate['asset_id'],
            rank=idx,
            priority_score=candidate['priority_score'],
            total_score=candidate['total_score'],
            state=candidate['state'],
            why_selected=candidate['why_selected'],
        )
        db.add(result)
        results.append(result)
    db.flush()
    return results


def refresh_all_scanners(db: Session, as_of: date | None = None) -> dict[str, int]:
    as_of = _resolve_latest_score_date(db, as_of)
    summary: dict[str, int] = {}
    for scanner_type in SCANNER_PROFILES:
        results = refresh_scanner(db, scanner_type, as_of=as_of)
        summary[scanner_type] = len(results)
    return summary
