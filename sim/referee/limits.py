"""Every Referee limit in one place, each with where the number comes from.

[DECIDE: Ethan reviews these before S5 calibration.] "7110.65" is FAA Order JO 7110.65 (Air Traffic Control).
Paragraph letters follow the edition before N JO 7110.806 re-lettered 5-5-4 (Sept 2026).
"""
from __future__ import annotations

from typing import Dict

NM = 1852.0
FT = 0.3048

# -- 1. collision ------------------------------------------------------------------------------------------------------
# Footprint: a rectangle, aircraft length x wingspan from the type CSV, centred on the aircraft and turned to its
# heading (assumption: a rectangle, not the real planform, so it is slightly conservative at the wing tips).
COLLISION_VERTICAL_M = 15.0     # assumption: footprints that overlap only collide within about one tail height (B738 tail 12.5 m)

# -- 2. loss of separation in the air ---------------------------------------------------------------------------------
RADAR_MIN_M = 3.0 * NM          # 7110.65 5-5-4a: 3 NM below 40 NM from the antenna (the whole sim airspace).
# 5-5-4j allows 2.5 NM on final inside 10 NM only if an average runway occupancy of 50 s or less is documented; the sim's
# average landing occupancy is 57-62 s (smoke/busy), so the reduced minimum is not applied.
VERTICAL_MIN_M = 1000 * FT      # 7110.65 4-5-1 / 5-5-4: 1000 ft vertical separation replaces the lateral minimum
NMAC_M = 500 * FT               # AIM 7-6-3 near mid-air collision: closer than 500 ft (horizontal and vertical) always fails
PARALLEL_SINGLE_M = 2500 * FT   # 7110.65 5-5-4g/h, 5-9-6: parallels closer than 2500 ft are one runway (in-trail + wake)
PARALLEL_WIDE_M = 3600 * FT     # 7110.65 5-9-6: dependent approaches, the diagonal minimum grows above 3600 ft
PARALLEL_INDEPENDENT_M = 4300 * FT  # 7110.65 5-9-7: simultaneous independent approaches, no minimum between the finals
DIAGONAL_NARROW_M = 1.0 * NM    # 7110.65 5-9-6: 1.0 NM diagonal, centrelines 2500-3600 ft apart (KPHX 26 / 25R: 3576 ft)
DIAGONAL_WIDE_M = 1.5 * NM      # 7110.65 5-9-6: 1.5 NM diagonal, centrelines over 3600 ft apart
# Assumption: the sim's go-around (straight ahead, then back to final) is a published missed approach the controller
# cannot vector, so a go-around is protected by the same parallel-runway rules as a final; on the same runway it needs
# the 3 NM radar minimum (no wake row: it is not in trail). Airborne departures are not checked (removed 40 s after
# lift-off, see engine).

# Wake turbulence, in trail on approach to the same runway (or parallels < 2500 ft): leader CWT -> follower CWT -> NM.
# 7110.65 5-5-4h TBL 5-5-4 (CWT, "on approach"). Missing pairs: radar minimum only. Transcribed from a secondary copy of
# the table [VERIFY against the official FAA text before S5; rows G and H were not legible and are left empty].
WAKE_NM: Dict[str, Dict[str, float]] = {
    "A": {"B": 5, "C": 6, "D": 6, "E": 7, "F": 7, "G": 7, "H": 8, "I": 8},
    "B": {"B": 3, "C": 4, "D": 4, "E": 5, "F": 5, "G": 5, "H": 5, "I": 6},
    "C": {"E": 3.5, "F": 3.5, "G": 3.5, "H": 5, "I": 6},
    "D": {"B": 3, "C": 4, "D": 4, "E": 5, "F": 5, "G": 5, "H": 6, "I": 6},
    "E": {"I": 4},
    "F": {"I": 4},
}

# -- 3. two aircraft on one runway ---------------------------------------------------------------------------------------
# Fails: a landing roll or takeoff roll while another aircraft is on the same runway pavement (7110.65 3-9-6 / 3-10-3:
# the other aircraft must be clear of the runway). No daylight distance reductions between two landings (simplification).
# A preceding departure that is airborne no longer occupies the runway, but must be this far away (3-9-6 / 3-10-3 table):
AIRBORNE_GAP_M = 6000 * FT      # 7110.65 3-9-6 / 3-10-3: Category III (all others), preceding departure airborne 6000 ft away
AIRBORNE_GAP_LIGHT_M = 3000 * FT  # same table, Category I, when both aircraft are ICAO light (assumption: light = Cat I)

# -- 4. runway incursion -------------------------------------------------------------------------------------------------
# The hold point is the sim's stop point HOLD_OFFSET_M (physics) short of the runway pavement. Passing it needs an
# acknowledged cross / line up / takeoff clearance for that runway (7110.65 3-7-2: each crossing needs its own
# clearance; a taxi clearance to a runway is not one); touching down needs an acknowledged landing clearance.
HOLD_LINE_TOLERANCE_M = 0.5     # assumption: numeric slack past the hold point before it counts as passed

# -- 5. diversion --------------------------------------------------------------------------------------------------------
# Holding = time an arrival is still airborne after its scheduled landing time (the sim has no holding stack: an arrival
# that cannot land flies the approach / go-around loop). At the limit, an arrival not cleared to land diverts.
DIVERSION_HOLDING_S = 600.0     # assumption for the compressed day: real reserves (14 CFR 121.639: 45 min) never trip in a
                                # 5-8 min scenario; the smoke go-around arrival holds about 324 s under the built-in tower

# -- 6. stuck / gridlock -------------------------------------------------------------------------------------------------
STUCK_AT_GATE_S = 300.0         # assumption: a ready departure not pushed back this long after its ready time (tower: <= 15 s)
STUCK_STOPPED_S = 600.0         # assumption: stopped on the movement area this long (tower: up to 477 s, SKW1797 holding
                                # short of 26 in the busy departure queue)
DEADLOCK_S = 60.0               # existing engine constant: a cycle of aircraft blocking each other this long is a gridlock

# -- 7. end of day -------------------------------------------------------------------------------------------------------
END_OF_DAY_GRACE_S = 900.0      # assumption: the day ends at scenario duration_s + this; the last flights need time to land
                                # or push, taxi and queue (smoke finishes 761 s after duration_s, busy 363 s)
