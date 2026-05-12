# Decision Matrix endpoints disabled
# 
# Reason: Memory intensive - was loading 60+ assets with multiple sub-score
# calculations simultaneously, causing frequent OOM errors on 512MB Render instances.
#
# Analysis is now distributed across:
# - Scanner (top opportunities by score)
# - Asset Detail (Earnings, Insider activity, Institutional analysis)
# - Signal Tracker (position-specific signals)
#
# If you need Decision Matrix later, restore from git history.

from fastapi import APIRouter

router = APIRouter()

# All Decision Matrix endpoints removed
