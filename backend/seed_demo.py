from __future__ import annotations

from datetime import date

from app.core.database import Base, SessionLocal, engine
from app.jobs.daily_ingestion import run as run_ingestion
from app.jobs.daily_portfolio_snapshots import run as run_portfolios
from app.jobs.daily_scanner import run as run_scanners
from app.jobs.daily_scoring import run as run_scoring
from app.models.portfolio import Portfolio, Position, PositionLot
from app.models.user import User
from app.core.security import get_password_hash
from app.models.asset import Asset


Base.metadata.create_all(bind=engine)


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == 'demo@example.com').first()
        if not user:
            user = User(email='demo@example.com', name='Demo User', hashed_password=get_password_hash('demo1234'))
            db.add(user)
            db.commit()
            db.refresh(user)

        portfolio = db.query(Portfolio).filter(Portfolio.user_id == user.id, Portfolio.name == 'Main Portfolio').first()
        if not portfolio:
            portfolio = Portfolio(user_id=user.id, name='Main Portfolio', base_currency='USD')
            db.add(portfolio)
            db.commit()
            db.refresh(portfolio)

        run_ingestion()
        run_scoring()
        run_scanners()

        sofi = db.query(Asset).filter(Asset.ticker == 'SOFI').first()
        if sofi and not db.query(Position).filter(Position.portfolio_id == portfolio.id, Position.asset_id == sofi.id).first():
            position = Position(
                portfolio_id=portfolio.id,
                asset_id=sofi.id,
                first_buy_date=date(2026, 4, 1),
                avg_cost=12.45,
                quantity=100,
                invested_amount=1245,
                position_type='repricing_growth',
                horizon='6_24_months',
                thesis='Improving margins and customer growth',
                invalidation_rules='Guidance cut or structural deterioration',
            )
            db.add(position)
            db.flush()
            db.add(PositionLot(position_id=position.id, buy_date=date(2026, 4, 1), quantity=100, price=12.45, fees=0, notes='Initial lot'))
            db.commit()

        run_portfolios()
        print('Demo data loaded successfully.')
    finally:
        db.close()


if __name__ == '__main__':
    main()
