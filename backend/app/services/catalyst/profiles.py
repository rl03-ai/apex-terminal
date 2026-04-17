"""Sector-specific catalyst profiles.

Each sector has different signal weights because the same event
means different things in different industries.

Examples:
  - Insider buying in Biotech = very strong (management knows trial results)
  - Insider buying in Large Cap Tech = weaker (executives sell routinely)
  - Earnings beat in Energy = discounted when crude is high (mechanical)
  - FDA approval in Healthcare = dominates everything else
  - Revenue acceleration in Growth Tech > EPS beat

Profiles define:
  w_earnings  : weight for earnings surprise/guidance signal
  w_insider   : weight for insider buying/selling
  w_news      : weight for news sentiment
  earnings_threshold : min EPS surprise % to be considered a beat
  insider_min_importance : min importance score for insider buy to register
  guidance_multiplier : how much to amplify guidance revision signal
  description : human-readable explanation of the profile
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CatalystProfile:
    name: str
    w_earnings: float           # 0-1, weight for earnings signal
    w_insider: float            # 0-1, weight for insider signal
    w_news: float               # 0-1, weight for news sentiment
    earnings_threshold: float   # min surprise % to count as meaningful beat
    insider_min_importance: float  # min importance for insider to register
    guidance_multiplier: float  # amplify guidance revisions (1.0 = no change)
    revenue_vs_eps: float       # >1.0 means revenue beat matters more than EPS
    description: str
    sector_keywords: list[str] = field(default_factory=list)  # extra keywords

    def __post_init__(self):
        total = self.w_earnings + self.w_insider + self.w_news
        if abs(total - 1.0) > 0.01:
            # Normalise
            self.w_earnings /= total
            self.w_insider  /= total
            self.w_news     /= total


# ─────────────────────────────────────────────────────────────────────────────
# Sector profiles
# ─────────────────────────────────────────────────────────────────────────────

CATALYST_PROFILES: dict[str, CatalystProfile] = {

    # ── Technology: Large Cap (AAPL, MSFT, GOOGL, META, AMZN) ──────────────
    # Guidance revision and cloud/AI metrics are primary signals.
    # Insider buying is very weak signal — executives sell routinely.
    # Analyst upgrades only matter with significant price target revision.
    'large_cap_tech': CatalystProfile(
        name='large_cap_tech',
        w_earnings=0.55,
        w_insider=0.10,
        w_news=0.35,
        earnings_threshold=3.0,    # needs >3% beat to matter at this scale
        insider_min_importance=80, # only very large insider buys register
        guidance_multiplier=2.0,   # guidance revision is very high signal
        revenue_vs_eps=1.5,        # revenue acceleration > EPS beat
        description='Large Cap Tech: guidance + revenue acceleration dominate. Insider signal discounted.',
        sector_keywords=['cloud revenue', 'ai monetization', 'margin expansion',
                        'guidance raised', 'revenue acceleration', 'operating leverage'],
    ),

    # ── Technology: Growth / Semiconductor (NVDA, AMD, PLTR, SMCI, AVGO) ──
    # Momentum + earnings acceleration. Revenue beat > EPS.
    # Insider buying is medium signal. Data center / AI demand is key.
    'growth_tech': CatalystProfile(
        name='growth_tech',
        w_earnings=0.50,
        w_insider=0.20,
        w_news=0.30,
        earnings_threshold=5.0,    # growth companies should beat more
        insider_min_importance=65,
        guidance_multiplier=2.5,   # forward guidance is everything
        revenue_vs_eps=1.8,        # revenue beat >> EPS beat
        description='Growth Tech: earnings acceleration + revenue beat dominate. Strong guidance amplified.',
        sector_keywords=['data center', 'ai chip', 'hyperscaler', 'backlog',
                        'supply constraints', 'design wins', 'gaming', 'automotive'],
    ),

    # ── Technology: Software / SaaS (CRM, NOW, DDOG, SNOW, HUBS) ──────────
    # ARR/NRR growth and net revenue retention are primary.
    # EPS less relevant for high-growth SaaS — look at FCF and ARR.
    'saas_software': CatalystProfile(
        name='saas_software',
        w_earnings=0.40,
        w_insider=0.20,
        w_news=0.40,
        earnings_threshold=4.0,
        insider_min_importance=65,
        guidance_multiplier=2.2,
        revenue_vs_eps=2.0,        # ARR/revenue is everything in SaaS
        description='SaaS: ARR growth and NRR dominate. Revenue retention > EPS.',
        sector_keywords=['arr', 'nrr', 'net revenue retention', 'customer count',
                        'enterprise', 'platform', 'subscription', 'churn'],
    ),

    # ── Healthcare: Biotech / Pharma R&D (MRNA, RXRX, BEAM, CRSP, BIIB) ──
    # FDA decisions and clinical trial results dominate everything.
    # Insider buying is maximum signal — management knows trial status.
    # Earnings almost irrelevant if pre-revenue.
    'biotech': CatalystProfile(
        name='biotech',
        w_earnings=0.20,
        w_insider=0.45,
        w_news=0.35,
        earnings_threshold=10.0,   # earnings barely matter pre-revenue
        insider_min_importance=55, # any insider buy is meaningful
        guidance_multiplier=1.0,
        revenue_vs_eps=1.0,
        description='Biotech: FDA/trial events + insider buying dominate. Earnings discounted pre-revenue.',
        sector_keywords=['fda approval', 'phase 3', 'phase 2', 'trial results',
                        'nda', 'bla', 'clinical data', 'efficacy', 'safety',
                        'breakthrough therapy', 'orphan drug', 'partnership deal'],
    ),

    # ── Healthcare: Services / Devices (UNH, ABT, MDT, SYK, BSX, EW) ─────
    # Earnings and guidance are meaningful here — these are real businesses.
    # Regulatory approvals still matter but less than pure biotech.
    'healthcare_services': CatalystProfile(
        name='healthcare_services',
        w_earnings=0.45,
        w_insider=0.25,
        w_news=0.30,
        earnings_threshold=3.0,
        insider_min_importance=65,
        guidance_multiplier=1.5,
        revenue_vs_eps=1.2,
        description='Healthcare Services/Devices: balanced signals. Regulatory approvals amplified.',
        sector_keywords=['medicare', 'medicaid', 'reimbursement', 'procedure volumes',
                        'fda clearance', '510k', 'product launch', 'market share'],
    ),

    # ── Energy: Oil & Gas (XOM, CVX, SLB, HAL, EOG, DVN, OXY) ────────────
    # Macro (crude price trend) dominates earnings.
    # Earnings beats are mechanical when crude is high — discount them.
    # Capex discipline, dividend coverage, and FCF yield are key.
    'energy': CatalystProfile(
        name='energy',
        w_earnings=0.35,
        w_insider=0.30,
        w_news=0.35,
        earnings_threshold=8.0,    # high bar — beats are expected in bull cycle
        insider_min_importance=60,
        guidance_multiplier=1.8,   # capex/production guidance matters
        revenue_vs_eps=0.8,        # FCF and margin more important than revenue
        description='Energy: capex discipline + FCF yield + dividend coverage. Earnings discounted vs crude cycle.',
        sector_keywords=['crude oil', 'natural gas', 'production guidance',
                        'capex', 'free cash flow', 'dividend', 'buyback',
                        'opec', 'refining margin', 'breakeven cost'],
    ),

    # ── Financials: Banks / Insurance (BAC, USB, GS, JPM, MS, WFC, C) ────
    # NIM (net interest margin), loan growth, credit quality.
    # Earnings beats are very predictable — less signal.
    # Book value growth and capital return (buybacks, dividends) matter.
    'financials': CatalystProfile(
        name='financials',
        w_earnings=0.40,
        w_insider=0.25,
        w_news=0.35,
        earnings_threshold=5.0,    # higher threshold — earnings are formulaic
        insider_min_importance=65,
        guidance_multiplier=1.5,
        revenue_vs_eps=0.9,
        description='Financials: NIM + credit quality + capital return. Earnings predictable — discounted.',
        sector_keywords=['net interest margin', 'nim', 'loan growth', 'credit quality',
                        'provisions', 'book value', 'buyback', 'dividend',
                        'fed rate', 'yield curve', 'delinquencies'],
    ),

    # ── Industrials (CAT, GE, HON, GWW, DE, EMR, ITW, ROK) ───────────────
    # Order backlog, contract wins, margin expansion.
    # Earnings beats meaningful if driven by pricing power, not volume.
    'industrials': CatalystProfile(
        name='industrials',
        w_earnings=0.45,
        w_insider=0.25,
        w_news=0.30,
        earnings_threshold=3.0,
        insider_min_importance=65,
        guidance_multiplier=1.6,
        revenue_vs_eps=1.1,
        description='Industrials: backlog + margin expansion + pricing power. Guidance revision amplified.',
        sector_keywords=['backlog', 'order intake', 'pricing power', 'margin expansion',
                        'infrastructure', 'reshoring', 'automation', 'contract win'],
    ),

    # ── Defense & Aerospace (NOC, LMT, RTX, GD, L3, HII) ─────────────────
    # Government contracts and DoD budget are primary.
    # Very predictable earnings — less signal from beats.
    # Contract wins and program status are key catalysts.
    'defense': CatalystProfile(
        name='defense',
        w_earnings=0.35,
        w_insider=0.25,
        w_news=0.40,
        earnings_threshold=4.0,
        insider_min_importance=65,
        guidance_multiplier=1.4,
        revenue_vs_eps=1.0,
        description='Defense: DoD contracts + budget allocation dominate. Earnings predictable.',
        sector_keywords=['dod contract', 'pentagon', 'defense budget', 'program award',
                        'hypersonic', 'f-35', 'missile', 'satellite', 'cybersecurity contract'],
    ),

    # ── Consumer Discretionary (MCD, NKE, SBUX, AMZN, BKNG, HLT, MAR) ──
    # Same-store sales, comp growth, and consumer spending trends.
    # Brand momentum and international expansion.
    'consumer_discretionary': CatalystProfile(
        name='consumer_discretionary',
        w_earnings=0.45,
        w_insider=0.20,
        w_news=0.35,
        earnings_threshold=3.0,
        insider_min_importance=65,
        guidance_multiplier=1.5,
        revenue_vs_eps=1.3,        # comp sales > EPS
        description='Consumer Discretionary: comp sales + consumer sentiment + brand momentum.',
        sector_keywords=['same-store sales', 'comparable sales', 'traffic',
                        'international expansion', 'loyalty program', 'digital',
                        'consumer spending', 'travel demand', 'occupancy'],
    ),

    # ── Consumer Staples (KO, PEP, PG, CL, KMB, MO, PM) ──────────────────
    # Pricing power and volume retention are key.
    # Very stable earnings — beats are minor signal.
    # Dividend growth is a major catalyst for institutional buying.
    'consumer_staples': CatalystProfile(
        name='consumer_staples',
        w_earnings=0.40,
        w_insider=0.20,
        w_news=0.40,
        earnings_threshold=2.0,    # lower threshold — staples should be consistent
        insider_min_importance=70,
        guidance_multiplier=1.3,
        revenue_vs_eps=1.0,
        description='Consumer Staples: pricing power + volume + dividend growth. Defensive signal profile.',
        sector_keywords=['pricing power', 'volume growth', 'market share',
                        'dividend increase', 'emerging markets', 'organic growth'],
    ),

    # ── Real Estate (PLD, SPG, VICI, CCI, WELL, AVB, EQR) ─────────────────
    # FFO (funds from operations) > EPS.
    # Occupancy rates, rent growth, lease renewals.
    # Interest rate sensitivity — Fed policy is a mega catalyst.
    'real_estate': CatalystProfile(
        name='real_estate',
        w_earnings=0.35,
        w_insider=0.25,
        w_news=0.40,
        earnings_threshold=3.0,
        insider_min_importance=65,
        guidance_multiplier=1.4,
        revenue_vs_eps=1.4,        # FFO/revenue > EPS
        description='REITs: FFO + occupancy + rent growth + rate sensitivity.',
        sector_keywords=['ffo', 'funds from operations', 'occupancy', 'rent growth',
                        'lease renewal', 'cap rate', 'interest rate', 'noi'],
    ),

    # ── Utilities (NEE, DUK, SO, AEP, PCG, ED, EXC) ──────────────────────
    # Regulatory decisions and rate cases dominate.
    # Dividend yield and growth are primary investment thesis.
    # Rate environment is a macro catalyst.
    'utilities': CatalystProfile(
        name='utilities',
        w_earnings=0.30,
        w_insider=0.20,
        w_news=0.50,
        earnings_threshold=2.0,
        insider_min_importance=70,
        guidance_multiplier=1.2,
        revenue_vs_eps=0.9,
        description='Utilities: regulatory + dividend + rate environment. Defensive, news-driven.',
        sector_keywords=['rate case', 'regulatory approval', 'renewable energy',
                        'solar', 'wind', 'grid', 'dividend', 'rate hike', 'capex plan'],
    ),

    # ── Materials (LIN, DOW, NEM, FCX, APD, SHW, PPG) ─────────────────────
    # Commodity prices dominate margins.
    # Volume growth and pricing power in downstream products.
    'materials': CatalystProfile(
        name='materials',
        w_earnings=0.40,
        w_insider=0.25,
        w_news=0.35,
        earnings_threshold=5.0,    # mechanical beats when commodities are up
        insider_min_importance=65,
        guidance_multiplier=1.5,
        revenue_vs_eps=1.0,
        description='Materials: commodity cycle + volume + pricing. Earnings discounted vs macro.',
        sector_keywords=['copper', 'gold', 'commodity price', 'chemical margin',
                        'volume growth', 'capacity', 'pricing', 'supply chain'],
    ),

    # ── Communication Services (T, VZ, CMCSA, NFLX, DIS) ─────────────────
    # Subscriber growth and ARPU for streaming.
    # Wireless for telcos — postpaid additions, churn.
    'communication': CatalystProfile(
        name='communication',
        w_earnings=0.40,
        w_insider=0.20,
        w_news=0.40,
        earnings_threshold=3.0,
        insider_min_importance=65,
        guidance_multiplier=1.5,
        revenue_vs_eps=1.4,        # subscriber/revenue > EPS
        description='Communication: subscriber growth + ARPU + content pipeline.',
        sector_keywords=['subscribers', 'arpu', 'streaming', 'postpaid', 'churn',
                        'content', 'bundling', 'wireless', '5g', 'broadband'],
    ),

    # ── Small Cap Growth (SOFI, RKLB, HOOD, IONQ, ACHR) ──────────────────
    # Analyst coverage is scarce — any new coverage is a catalyst.
    # Revenue growth rate >> margins (not yet profitable).
    # Insider buying is maximum signal.
    'small_cap_growth': CatalystProfile(
        name='small_cap_growth',
        w_earnings=0.30,
        w_insider=0.40,
        w_news=0.30,
        earnings_threshold=10.0,   # often pre-profit — revenue beat matters more
        insider_min_importance=55, # any insider buy is meaningful
        guidance_multiplier=2.0,
        revenue_vs_eps=2.5,        # revenue growth is the whole thesis
        description='Small Cap Growth: insider buying + revenue acceleration + new analyst coverage.',
        sector_keywords=['revenue growth', 'new contract', 'expansion', 'launch',
                        'initiation', 'analyst coverage', 'partnership', 'milestone'],
    ),

    # ── Default (catch-all) ────────────────────────────────────────────────
    'default': CatalystProfile(
        name='default',
        w_earnings=0.45,
        w_insider=0.30,
        w_news=0.25,
        earnings_threshold=3.0,
        insider_min_importance=60,
        guidance_multiplier=1.5,
        revenue_vs_eps=1.0,
        description='Default balanced weights.',
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Sector → profile routing
# ─────────────────────────────────────────────────────────────────────────────

# Map yfinance sector strings → profile keys
_SECTOR_MAP: dict[str, str] = {
    # Technology
    'Technology':                    'growth_tech',    # default tech → growth
    'Information Technology':        'growth_tech',
    'Communication Services':        'communication',

    # Healthcare
    'Healthcare':                    'healthcare_services',
    'Health Care':                   'healthcare_services',

    # Energy
    'Energy':                        'energy',

    # Financials
    'Financial Services':            'financials',
    'Financials':                    'financials',

    # Industrials
    'Industrials':                   'industrials',

    # Consumer
    'Consumer Cyclical':             'consumer_discretionary',
    'Consumer Discretionary':        'consumer_discretionary',
    'Consumer Defensive':            'consumer_staples',
    'Consumer Staples':              'consumer_staples',

    # Real Estate
    'Real Estate':                   'real_estate',

    # Utilities
    'Utilities':                     'utilities',

    # Materials
    'Basic Materials':               'materials',
    'Materials':                     'materials',
}

# Market cap thresholds for large vs growth tech classification (in $)
_LARGE_CAP_TECH_THRESHOLD = 200_000_000_000   # $200B+
_SMALL_CAP_THRESHOLD       =   2_000_000_000  # <$2B


def get_catalyst_profile(
    sector: str | None,
    industry: str | None,
    market_cap: float | None,
    ticker: str = '',
) -> CatalystProfile:
    """
    Select the appropriate catalyst profile for an asset.

    Logic:
      1. Industry-specific overrides (biotech, defense, saas)
      2. Size-aware tech split (large cap vs growth vs small cap)
      3. Sector mapping
      4. Default
    """
    sector = (sector or '').strip()
    industry = (industry or '').lower()
    ticker = ticker.upper()
    mcap = market_cap or 0

    # ── Industry-specific overrides ──────────────────────────────────────────
    biotech_keywords = ['biotechnology', 'biopharmaceutical', 'drug discovery',
                        'genomics', 'gene therapy', 'clinical stage', 'pharmaceutical']
    if any(kw in industry for kw in biotech_keywords):
        return CATALYST_PROFILES['biotech']

    defense_keywords = ['aerospace', 'defense', 'military', 'space launch']
    if any(kw in industry for kw in defense_keywords):
        return CATALYST_PROFILES['defense']

    saas_keywords = ['software', 'saas', 'cloud computing', 'application software',
                     'internet software', 'it services']
    if any(kw in industry for kw in saas_keywords):
        if mcap < _SMALL_CAP_THRESHOLD:
            return CATALYST_PROFILES['small_cap_growth']
        return CATALYST_PROFILES['saas_software']

    # ── Technology split by market cap ───────────────────────────────────────
    tech_sectors = {'Technology', 'Information Technology'}
    if sector in tech_sectors:
        if mcap >= _LARGE_CAP_TECH_THRESHOLD:
            return CATALYST_PROFILES['large_cap_tech']
        if mcap < _SMALL_CAP_THRESHOLD:
            return CATALYST_PROFILES['small_cap_growth']
        return CATALYST_PROFILES['growth_tech']

    # ── Small cap growth catch-all ────────────────────────────────────────────
    if mcap > 0 and mcap < _SMALL_CAP_THRESHOLD:
        if sector not in ('Utilities', 'Consumer Defensive', 'Consumer Staples',
                          'Financial Services', 'Financials', 'Real Estate'):
            return CATALYST_PROFILES['small_cap_growth']

    # ── Sector mapping ───────────────────────────────────────────────────────
    profile_key = _SECTOR_MAP.get(sector, 'default')
    return CATALYST_PROFILES[profile_key]
