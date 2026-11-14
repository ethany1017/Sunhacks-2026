# Sort Phoenix build data into three bins

Gus and Ethan can start building the KPHX simulation now, because the data for geometry, procedures, aircraft characteristics, weather and the rule engine is already downloaded and verified. What cannot be had from public sources is the state of the airport itself: gate and ramp occupancy, ground vehicles, flight plans, traffic-management restrictions, NOTAMs, the ZAB/P50 agreements and any KPHX-specific taxi or runway-occupancy baselines. All of that has to be synthetic, and the demo should say so. Of 19 catalog items, 7 are **ready**, 8 are **partial** and 4 are **synthetic-only** (table below). The current FAA order is **JO 7110.65BB with Changes 1-3** (Change 3 dated 7-9-26), SunHacks 9.0 runs **Nov 14-15, 2026** with Devpost submissions due **Sunday 11 AM**, and the OpenAI voice shutdowns are confirmed for **Jan 6, Jan 20 and Feb 26, 2027**. airplanes.live returned HTTP 403 and ADS-B Exchange HTTP 402, so adsb.lol and adsb.fi are the live traffic sources. Several facts that look settled are not: the clearance delivery frequency (118.1 versus 133.7), the north-south order of the runways in one note, and every haboob impact count. Section 4 lists each conflict and section 5 each gap with a stand-in. The notes were written about 14:55-15:06 local on Nov 14, which leaves roughly 20 hours to the Sunday deadline.

Conventions. "Verified" means a note's author fetched and read the file or page; "unverified" means a search excerpt, a blocked page or background knowledge. Paths are under `/Users/gusschwiesow/Aug OS/Hackathon`, written below as `data/...` and `scripts/...`. Notes are in `research_notes/Phoenix airport build data catalog/`. Where I cross-checked a claim against the files on disk myself, the text says so. No credentials appear in this report or in the sample files described.

## 1. Status at a glance

| # | Item | Status | Key file path |
|---|---|---|---|
| 1 | Airport geometry | ready (needs cleanup) | `/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_geometry/osm_overpass_aeroway/kphx_aeroway.json` |
| 2 | Airspace and procedures | partial (procedures ready; sectors and agreements synthetic) | `/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_operations/faa_cifp_2611/kphx_records.txt` |
| 3 | Aircraft tracks | partial (live snapshots only; no history downloaded) | `/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_traffic/adsblol_20261114T215544Z/point.json` |
| 4 | Flight intent | partial (real PHX schedule rows, tails and taxi times from BTS; routes, runways, squawks synthetic) | `/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_traffic/bts_otp_2026_8/derived/phx_flights_slim.csv` |
| 5 | Aircraft characteristics | ready (no runway occupancy by type) | `/Users/gusschwiesow/Aug OS/Hackathon/data/reference_and_rules/derived_kphx_aircraft_normalized/kphx_aircraft_normalized.csv` |
| 6 | Surface traffic | partial (real ADS-B ground targets; vehicles and ASDE-X synthetic) | `/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_traffic/adsblol_poll_20261114T215617Z/p1.json` |
| 7 | Gate and ramp state | synthetic-only (stand geometry is real) | `/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_geometry/osm_overpass_aeroway/kphx_aeroway.json` |
| 8 | Airport status | partial (D-ATIS and NAS Status real; NOTAMs synthetic) | `/Users/gusschwiesow/Aug OS/Hackathon/data/weather_and_status/datis_clowd/kphx.json` |
| 9 | Traffic restrictions | synthetic-only | `/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_traffic/faa_nasstatus_20261114T215544Z/phx.xml` (event driver only) |
| 10 | Weather | ready | `/Users/gusschwiesow/Aug OS/Hackathon/data/weather_and_status/awc_metar/metar_5stn_3h.json` |
| 11 | Communications | partial (phraseology ready; KPHX frequencies in conflict; no US audio) | `/Users/gusschwiesow/Aug OS/Hackathon/data/voice_and_comms/faa_7110_3_7/chap3_section_7.txt` |
| 12 | Rules and agreements | partial (7110.65BB ready; local agreements synthetic) | `/Users/gusschwiesow/Aug OS/Hackathon/data/reference_and_rules/faa_7110_65BB_chg3_consolidated/order.pdf` |
| 13 | Scenario data | partial (two real haboob replays; scenario scripts not written) | `/Users/gusschwiesow/Aug OS/Hackathon/data/weather_and_status/haboob_clean_2025-08-25/kphx_haboob_20250825_clean.csv` |
| 14 | Evaluation baselines | partial (BTS taxi-out/taxi-in and haboob-day delay curve now sourced; separation and throughput benchmarks still unsourced) | `/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_traffic/bts_otp_2026_8/derived/phx_hourly_summary.csv` |
| 15 | Explainability and decision-record schema | partial (draft in notes, not yet a file) | `/Users/gusschwiesow/Aug OS/Hackathon/research_notes/Phoenix airport build data catalog/build_logistics.md` |
| 16 | Map assets | ready | `/Users/gusschwiesow/Aug OS/Hackathon/data/build_logistics/openfreemap_liberty_style/liberty.json` |
| 17 | Costs and limits | ready (some vendors unverified) | `/Users/gusschwiesow/Aug OS/Hackathon/research_notes/Phoenix airport build data catalog/build_logistics.md` |
| 18 | Hackathon logistics | partial (rubric, prizes, code policy unpublished) | `/Users/gusschwiesow/Aug OS/Hackathon/research_notes/Phoenix airport build data catalog/build_logistics.md` |
| 19 | Repo and tooling | partial (repo not accessed; layout proposed) | `/Users/gusschwiesow/Aug OS/Hackathon/scripts/` |

One caution on provenance. The fetch scripts were written after or alongside hand-run curl commands. The geometry script was only syntax-checked, the traffic scripts and the weather script were not run end to end, and the aircraft and rules scripts (`rules_*.py`) and `weather_build_haboob_csv.py` were run. Re-running a script is therefore a test, not a guaranteed refresh.

## 2. The model of KPHX that the data supports

KPHX has three parallel east-west runways: 08/26 at 11,489 x 150 ft, 07L/25R at 10,300 x 150 ft and 07R/25L at 7,800 x 150 ft, all grooved concrete ([AirNav KPHX](https://www.airnav.com/airport/KPHX), matched by the FAA ArcGIS Runways layer). I checked the order on disk: the FAA polygons in `data/kphx_geometry/faa_arcgis_Runways/runways_phx_bbox.geojson` put 08/26 at about 33.4408 N, 07L/25R at about 33.4310 N and 07R/25L at about 33.4288 N. So **08/26 is the north runway, 07L/25R the middle one and 07R/25L the south one**, and the two south runways are only about 800 ft apart by my centroid arithmetic (not a surveyed figure), while 08/26 sits about 3,500 ft north of them. That matters for the rules: parallels under 2,500 ft apart are treated together for wake timing, and runways under 700 ft apart count as one runway. The tower split follows the same north-south line, with 118.7 for 08/26 and 120.9 for 07L/25R and 07R/25L, and ground at 119.75 (north) and 132.55 (south) ([AirNav KPHX](https://www.airnav.com/airport/KPHX), saved at `data/kphx_operations/airnav_kphx/kphx.html`). A two-position local and two-position ground model is therefore a data-supported choice, not a guess. Operations are large and steady: 487,143 total operations in 2025 per the airport's December 2025 statistics PDF (487,142 on its facts page), with 465,903 air carrier and air taxi movements ([Sky Harbor Dec 2025 stats](https://www.skyharbor.com/media/ca1p4ojo/monthly-stats-december-2025.pdf); [facts](https://www.skyharbor.com/about-phx/news-media/media-kit/facts-numbers/)).

## 3. Per-item catalog

### 3.1 Airport geometry (ready, needs cleanup)

**Source and verification.** OpenStreetMap aeroway data via Overpass, verified: the query `[out:json][timeout:90]; (nwr["aeroway"](33.40,-112.05,33.46,-111.97);); out geom;` returned 933 KB with 5 runway ways (08/26 split across 3), 486 taxiway ways, 70 taxilanes, 231 holding positions, 111 gate nodes, about 253 parking positions, 22 jet bridges and 6 terminal polygons ([Overpass API](https://overpass-api.de/api/interpreter)). The taxiway network is one connected component (485 of 486 ways) and shares 89 nodes with runways. The FAA ArcGIS feature services give runway, taxiway (319 polygons) and apron (28 polygons) outlines, free and keyless, verified by query ([FAA Runways layer](https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/Runways/FeatureServer/0), [AM_Taxiway](https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/AM_Taxiway/FeatureServer/0)). The cycle 2611 airport diagram and hot spot list were downloaded ([diagram](https://aeronav.faa.gov/d-tpp/2611/00322AD.PDF), [hot spots](https://www.faa.gov/aero_docs/dtpp/2611/SW4HOTSPOT.PDF)). Field elevation 1134.8 ft and runway-end elevations come from AirNav's copy of FAA data, effective 2026-10-29 (verified, secondary mirror).

**On disk.** `data/kphx_geometry/` has `osm_overpass_aeroway/` (JSON plus the query file), `faa_arcgis_Runways/`, `faa_arcgis_AM_Taxiway/`, `faa_arcgis_AM_Apron/`, `faa_airport_diagram/00322AD_2611.pdf`, `faa_hotspot_sw/hs_2611.pdf` and `hs_2610.pdf`, and `faa_dtpp_cycle_xml/phx_excerpt.xml`. Script: `scripts/kphx_geometry_fetch.sh` (syntax-checked only).

**Limits.** OSM has 82 free taxiway endpoints, 57 taxiway ways with no name or ref, 28 hold positions off the network and no closure tags, so closed taxiways are not represented. Connectivity was computed on shared nodes only, so crossings drawn without a node would be missed, and nobody has paired holds with each taxiway-runway junction. FAA polygons are outlines, not centerlines, and the meanings of the SURFACE and TWY_OPER codes were not looked up. Declared distances are not in hand because the NASR zip returned HTTP 503; AirNav gives only runway 8's displaced threshold (898 ft) and its landing distance (10,591 ft). The hot spot list is PDF only: HS 1 says pilots mistake Taxiway F for 07L or 07R, and HS 2 says pilots cross 07L-25R at F8, F9 or F10 without authorization (identical in cycles 2610 and 2611). AirNav remarks add the wingspan closures (Taxiway A between A3 and A10 above 125 ft, H between H4 and H7 above 171 ft) and the train bridges over R and Y3, and say parts of R, S and T are not visible from the tower. The FAA kneecard and the Sky Harbor movement-area rules were seen only as search snippets, so they are unverified.

**Synthetic.** Closure state, standard taxi routes and ramp handoff spots (no source found), stand-to-taxilane links (snap by geometry), and declared distances until NASR is fetched.

### 3.2 Airspace and procedures (partial)

**Source and verification.** The FAA CIFP ARINC 424 file for cycle 2611 (effective 2026-10-29 to 2026-11-26) and the d-TPP metafile are verified ([CIFP zip](https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip), [d-TPP metafile](https://aeronav.faa.gov/d-tpp/2611/xml_data/d-TPP_Metafile.xml)). The extract holds 1,427 KPHX lines: 495 SID, 316 STAR, 208 approach and 138 terminal waypoint records. Current SIDs include BALDY THREE, BROAK ONE, BUCKEYE FOUR, CHILY FOUR, ECLPS ONE, FORPE ONE, FYRBD ONE, KEENS THREE, MOBIE FOUR, MRBIL ONE, QUAKY ONE, ST. JOHNS NINE, STANFIELD FOUR, STRRM ONE and ZEPER TWO. Current STARs are ARLIN FOUR, BLYTHE FIVE, BRUSR ONE, BUNTR THREE, COYOT FIVE, DSERT TWO, EAGUL SIX, HYDRR ONE, JESSE THREE, PINNG ONE and SUNSS EIGHT. Approaches are ILS or LOC for 07R, 08, 25L and 26, ILS or LOC/DME for 07L, and RNAV (GPS) Y for all six runway ends. Runway 25R has no ILS chart in this cycle. The approach and departure sector table (frequency by azimuth and altitude) and the procedure-to-frequency map come from AirNav ([AirNav KPHX](https://www.airnav.com/airport/KPHX)), verified as a secondary copy.

**On disk.** `data/kphx_operations/faa_cifp_2611/` (`kphx_records.txt`, the zip), `faa_cifp_page/cifp.html`, `faa_dtpp_2611/d-TPP_Metafile.xml`, `faa_nasr_page/nasr.html` (page only), `airnav_kphx/kphx.html`. Script: `scripts/kphx_operations_fetch.sh` (curl downloads only) and `scripts/kphx_operations_pdftext.py` (crude PDF dump that failed on the noise reports).

**Limits.** CIFP needs an ARINC 424 parser, which does not exist in the repo yet. CIFP idents differ from chart names for some SIDs (BXK4 is BUCKEYE FOUR, TFD4 is STANFIELD FOUR, SJN9 is ST. JOHNS NINE). AirNav's frequency list names ZIDOG and WETAL departures that are not in the current chart list, so the mapping may lag. Not verified: Class B limits (a 2007 final rule lowered the ceiling to 9,000 ft MSL per a search excerpt), the east-west flow pattern, the 4 DME east-departure noise rule and the equalization statistics, all from search excerpts or PDFs whose text extraction failed. The FAA procedure boards PDF returned 403. Heat or density-altitude rules at PHX: nothing sourced.

**Synthetic.** P50 internal sector boundaries, runway-use thresholds (make the tailwind switch a configurable parameter), and any KPHX letters of agreement (section 3.12).

### 3.3 Aircraft tracks (partial)

**Source and verification.** adsb.lol (`https://api.adsb.lol/v2/point/33.4343/-112.0116/15`) and adsb.fi (`https://opendata.adsb.fi/api/v2/lat/33.4343/lon/-112.0116/dist/15`) answer anonymous GETs with 62 aircraft in the same 15 nm circle, verified. adsb.lol publishes daily global history under ODbL on GitHub, about 3.5 GB per day for the 2026-11-13 prod release, verified by the releases API only ([adsb.lol historical data](https://www.adsb.lol/docs/open-data/historical/)). OpenSky anonymous access works (29 vectors in a smaller box, header showed 399 credits remaining, 400 per day anonymous) ([OpenSky REST docs](https://openskynetwork.github.io/opensky-api/rest.html)). airplanes.live returned HTTP 403 asking for an emailed project description, and ADS-B Exchange returned HTTP 402 ("purchase a key"), so neither is usable.

**On disk.** `data/kphx_traffic/adsblol_20261114T215544Z/point.json`, `adsbfi_...`, `opensky_...`, `airplaneslive_...` and `adsbexchange_...` (error bodies and headers), `adsblol_poll_20261114T215617Z/p1.json` to `p5.json`, `adsblol_releases_meta_...`. Scripts: `scripts/kphx_traffic_fetch_samples.sh`, `kphx_traffic_poll_rate.sh`, `kphx_traffic_fetch_meta.sh` (not run end to end).

**Limits.** One Saturday-afternoon snapshot cannot characterize gate or ramp coverage. The adsb.lol and adsb.fi rate limits and terms were not read, and no history day was downloaded, so the in-file point cadence is unknown. OpenSky returned stale and sparse ground positions, carries no registration or type fields, and its terms were not read. Deep OpenSky history needs an approved account, unverified.

**Synthetic.** Nothing for airborne traffic if live data is used; for any future or scripted scenario, tracks are generated.

### 3.4 Flight intent (synthetic-only)

**Source and verification.** No free source of filed flight plans exists in the notes. SWIM flight data (SFDPS) needs an FAA Data Access account and a signed agreement, with no published approval timeline ([FAA SWIM support](https://support.swim.faa.gov/hc/en-us/article_attachments/360096864051)). The closest free substitute is BTS On-Time Performance: tail number, origin, destination, scheduled and actual times, TaxiOut, TaxiIn, 111 fields, anonymous download. Sizes were read from HEAD requests only (September 2025 file 28.2 MB; August 2026 file 32.0 MB, matching "Latest Available Data: August 2026") ([BTS fields](https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ)). Nothing was downloaded because both exceed the 20 MB rule and need the user's go-ahead.

**On disk.** Headers only: `data/kphx_traffic/bts_headers_20261114T215544Z/head.txt` and `bts_head_2026_20261114T215705Z/head.txt`. The registry HEAD (`faa_registry_head_...`) returned 503.

**Limits.** BTS covers only reporting carriers (at least 1% of domestic revenue), gives gate-to-wheels taxi times rather than tracks, and lags about two months. Whether regional flights appear under the operating carrier was not confirmed. The two notes disagree on whether the PREZIP URL pattern works (section 4).

**Synthetic.** Everything about intent beyond origin, destination, times and tail: filed route, assigned SID and STAR, runway, squawk and altitude. The recipe from the traffic note is to draw flights from a chosen BTS day, assign procedures from clustered adsb.lol tracks, approximate the route and add filed-versus-actual deviations.

### 3.5 Aircraft characteristics (ready)

**Source and verification.** One normalized CSV of 39 types combines the FAA Aircraft Characteristics Database (388 types: dimensions, approach speed, MTOW, ADG, TDG), FAA JO 7360.1K (2,557 designators with wake category A to I, SRS category, effective 6-12-2025), ICAO Doc 8643 (7,261 records, POST-only endpoint) and OpenAP kinematics ([FAA ACD](https://www.faa.gov/airports/engineering/aircraft_char_database/aircraft_data), [JO 7360.1K](https://www.faa.gov/documentLibrary/media/Order/FAA_Order_JO_7360.1K_Aircraft_Type_Designators.pdf), [OpenAP](https://github.com/junzis/openap)). Verified fields: wake category, SRS, dimensions, approach speed and MTOW. Spot values: B738 approach speed 144 kt, span 112.6 ft, MTOW 174,200 lb; E75L 126 kt; C172 62 kt. CWT mapping for KPHX types: A319, A320, A321, A20N, A21N, B737, B738, B739, B38M, B39M, MD82 and DH8D are F; B752 is E; B763 is C; A332, A333, A359, B772, B77W, B788 and B789 are B; E75L, E75S, E170, E145 and CRJ2/7/9 are G; C56X and C680 are H; the single-engine and light twin types are I.

**On disk.** `data/reference_and_rules/derived_kphx_aircraft_normalized/kphx_aircraft_normalized.csv`, `faa_aircraft_characteristics_database/ACD_data.xlsx`, `derived_faa_7360_1K_designators/faa_7360_1K_types.csv`, `icao_doc8643_json/types.json`, `openap_aircraft_yml/` (24 files) and `openap_wrap_txt/` (13 files). Scripts: `rules_build_kphx_aircraft_csv.py`, `rules_parse_7360.py`, `rules_pdf_to_text.py`.

**Limits.** The 39-type list was not checked against KPHX traffic, so relevance is unverified. By my comparison, the live sample showed SR20 and S22T, which are not in the CSV (SR22 is). ACD approach speed is at maximum landing weight, a conservative proxy for Vref, so scale it down for lighter weights as an engineering choice. Only 22 of 39 types have OpenAP data, and OpenAP's units are undocumented (probably km and m/s, an inference to spot-check). A change notice JO 7360.7 (effective 2025-10-14) exists per a secondary result and was not checked for per-type changes. No per-type runway occupancy time, taxi speed or required runway length was obtained.

**Synthetic.** Runway occupancy by type, taxi speed distributions, takeoff-roll timing and runway length requirements, until calibrated (section 5).

### 3.6 Surface traffic (partial)

**Source and verification.** The adsb.lol sample shows 40 of 62 aircraft within 15 nm on the ground, all with position, but only 7 of those 40 were moving above 1 kt, and 14 had stale positions over 5 s old. Taxiing aircraft updated every 1.5 to 5.5 s versus under 0.3 s for airborne jets. No MLAT positions appeared, so tugs, fuel trucks and fire vehicles are invisible. ASDE-X is in use at KPHX (AirNav remark) but no public ASDE-X or STDDS feed exists.

**On disk.** The `adsblol_poll_20261114T215617Z/` series and the single snapshot above.

**Limits.** Calibration parameters are thin. Verified: pushback at Charlotte averaged 4.72 min (median 4.33, sd 2.27) and ramp transit 4.98 min (median 3.60, sd 4.45) ([NASA NTRS 20190027149](https://ntrs.nasa.gov/api/citations/20190027149/downloads/20190027149.pdf)); Zurich's average arrival runway occupancy was about 55 s ([J. Open Aviation Science](https://journals.open.tudelft.nl/joas/article/download/8465/6575/36676)). Both are other airports. The 15 kt unimpeded taxi speed and the 6 kt delay threshold come from search snippets and are unverified. JO 7110.65 contains no numeric taxi separation, only a 1,300 ft centerline distance for multiple crossings in one clearance.

**Synthetic.** Ground vehicles, the ASDE-X stream (about 1 Hz with position noise, dropouts and ghost targets, which the notes label engineering assumptions), taxi speeds, separation buffers and deadlock logic (wait-for-graph cycle detection, an engineering inference).

### 3.7 Gate and ramp state (synthetic-only)

**Source and verification.** Stand geometry is real: OSM has 111 gate nodes, about 253 parking positions and 22 jet bridges, and the airport claims 114 airline gates (marketing figure). Terminal polygons exist for Terminal 3, Terminal 4 and Gates D11-D18; no Terminal 2 polygon exists, and the closure itself was never sourced. Passenger split by terminal comes from the airport statistics. No source says who staffs ramp control at the terminals; the only ramp position found is "Copper Control" 311.0 and 132.0 for the Air National Guard ramp, and airside operations provides follow-me for wingspans of 215 ft or more ([AirNav KPHX](https://www.airnav.com/airport/KPHX)). 7110.65 puts movement on non-movement areas under the pilot, operator or airport.

**On disk.** Geometry as in 3.1. Terminal and carrier splits are in `data/kphx_operations/skyharbor_stats_dec2025/extracted.txt`.

**Limits.** Airline-to-concourse assignment is not sourced (a photography wiki says American faces the north side and Southwest the south side, unverified). Stand-level restrictions were not checked.

**Synthetic.** Gate assignment, occupancy, turnaround timers, pushback and tug state, and the ramp-to-ground handoff (model the ramp as unmanaged, with ATC authority starting at the movement-area boundary).

### 3.8 Airport status (partial)

**Source and verification.** The third-party D-ATIS feed (`https://datis.clowd.io/api/KPHX`, redirects to atis.info, use `-L`) returned ATIS information G at 2051Z on Nov 14 with simultaneous parallel approaches, "EXPECT ILS OR VISUAL APCH RWY 26, ILS OR VISUAL APCH RWY 25L. DEPARTING RWY 25R", and a NOTICES TO AIRMEN section: "INT, E13 INT H3 RWSL OTS, E13 RWSL STUCK ON. MULT CRANES INVOF ARPT. BIRD ACTIVITY INVOF ARPT. 118.1 OTS NEW CD FREQ 133.7." (verified fetch, unofficial service, terms unknown). The FAA NAS Status XML (`https://nasstatus.faa.gov/api/airport-status-information`) works without a key but had no PHX entry at fetch time. The FAA NOTAM API returned 401 without credentials, and the new NOTAM Management Service needs a portal request, with conflicting timeline claims in secondary sources.

**On disk.** `data/weather_and_status/datis_clowd/kphx.json`, `faa_nas_status/airport_status.xml`, `faa_notam_probe/external_api_nokey.txt`, `data/kphx_traffic/faa_nasstatus_20261114T215544Z/`.

**Limits.** No real NOTAM list, no runway condition feed (RCAM and FICON travel as NOTAMs; PHX has no snow or ice, so wet runway is the case that matters), no construction-closure feed. Whether PHX supports CAT II or III on any runway end was not confirmed.

**Synthetic.** A NOTAM generator. The weather note's specification: NOTAM id and series, type, Q-line, location KPHX, effective times, E-text such as "RWY 7R/25L CLSD" or "TWY E13 CLSD", affected ids, and links to scenario triggers, seeded with the ATIS notices above.

### 3.9 Traffic restrictions (synthetic-only)

**Source and verification.** No public TFMS, TBFM, EDCT or departure-release data was found, and ASPM and OPSNET were not retrieved. The only live hook is the NAS Status ground stop and delay-program list.

**On disk.** The NAS Status samples in 3.8.

**Limits.** The only capacity figures found are from the 2014 FAA Airport Capacity Profile (96-101 operations per hour for 25L/26 arrivals with 25R/26 departures in IMC), seen only as a search excerpt because the PDF returned 403, so it is unverified and 12 years old ([FAA profile](https://www.faa.gov/sites/faa.gov/files/airports/planning_capacity/profiles/PHX-Airport-Capacity-Profile-2014.pdf)). An undated traffic-management tips page (IFR arrival rate 48-52 for dependent parallel approaches) now redirects to NAS Status, unverified.

**Synthetic.** A per-flight ground stop, delay program and EDCT feed; a P50-to-tower release message; miles-in-trail on SID and STAR fixes; ZAB handoff and point-out messages; and an AAR/ADR table keyed by runway configuration and VMC or IMC, using the 2014 range only as a labelled placeholder.

### 3.10 Weather (ready)

**Source and verification.** NOAA Aviation Weather Center needs no key: METAR, TAF, PIREP, SIGMET, G-AIRMET and station info for KPHX and neighbors, all fetched, plus the gzip cache `metars.cache.csv.gz` (about 4,999 rows, refreshed each minute). Limits are 100 requests per minute, 400 entries per query, about 30 days of history and no CORS, so a backend proxy is required ([AWC API](https://aviationweather.gov/data/api/)). IEM's ASOS archive gives KPHX history back through at least 2011 ([IEM ASOS](https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py)). api.weather.gov alerts, observations and points work with an identifying User-Agent, and Open-Meteo forecast works but its reanalysis badly underestimates haboob extremes (13.6 kt gust modeled at 00Z against 61 kt observed at 00:45Z). NEXRAD Level II (KIWA) and MRMS listings work on the Unidata and NOAA S3 buckets (`noaa-nexrad-level2` returned 403; use `unidata-nexrad-level2`).

**On disk.** `data/weather_and_status/` has `awc_metar/`, `awc_taf/`, `awc_pirep/`, `awc_airsigmet/`, `awc_gairmet/`, `awc_stationinfo/`, `awc_cache/`, `iem_asos_2025-08-25/`, `iem_asos_2011-07-05/`, `iem_alerts_dsw/`, `nws_alerts_api/`, `nws_obs_api/`, `nws_points/`, `open_meteo_forecast/`, `open_meteo_archive/`, `nexrad_s3_listing/`, `mrms_listing/`. Scripts: `scripts/weather_fetch_phx.sh` (not run end to end) and `scripts/weather_build_haboob_csv.py` (run).

**Limits.** AWC METAR altimeter is in hPa and the cache CSV is in inHg. `wxString` was not seen in the quiet METAR JSON sample and is unconfirmed live. AWC station elevation (338 m, about 1,109 ft) differs from the FAA field elevation of 1,134.8 ft. No lightning feed was tested. The api.weather.gov alert archive returns zero features for the 2025 event; use IEM VTEC events. Derived formulas (density altitude, crosswind, flight category thresholds, CAT I minimums) are standard knowledge and were not re-verified. Heat limits (CRJ about 118 F, Airbus and Boeing 126-127 F, sources disagree on order) come from news ([CNBC](https://www.cnbc.com/2017/06/20/theres-a-scientific-reason-why-hot-weather-has-grounded-planes-at-phoenix-airport.html)).

**Synthetic.** Forecast-style future weather and any lightning-based ramp closure rule (airline-specific, unsourced).

### 3.11 Communications (partial)

**Source and verification.** Controller phraseology is verified from FAA JO 7110.65BB and the AIM: taxi, hold short, cross, line up and wait, takeoff, landing, go-around, squawk and frequency change, with group form for airline numbers and the AIM pronunciation table ("niner", "fife", "tree") ([AIM Ch4 Sec2](https://www.faa.gov/air_traffic/publications/atpubs/aim_html/chap4_section_2.html), [7110.65 3-7](https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap3_section_7.html)). Pilot readback wording is not spelled out in 7110.65 and is unverified, except that hold-short readback is required. Verified frequencies from the Chart Supplement copy: D-ATIS 127.575, tower 118.7 (08/26) and 120.9 (07L/25R, 07R/25L), ground 119.75 north and 132.55 south; clearance delivery is in conflict (section 4). The OpenAI deprecations page confirms shutdowns of tts-1, tts-1-hd and gpt-4o-mini-tts on Jan 6, 2027, the realtime family on Jan 20, 2027, and whisper-1, gpt-4o-transcribe and gpt-4o-mini-transcribe on Feb 26, 2027 ([OpenAI deprecations](https://developers.openai.com/api/docs/deprecations)). Deepgram, AssemblyAI, ElevenLabs and AWS prices were read from official pages ([Deepgram](https://deepgram.com/pricing), [AssemblyAI](https://www.assemblyai.com/pricing)); Azure and Google prices were not. Local options: faster-whisper (MIT), Whisper large-v3-turbo (MIT), Kokoro-82M (Apache-2.0) and Piper (GPL-3.0).

**On disk.** `data/voice_and_comms/faa_7110_*`, `faa_aim_4_1`, `faa_aim_4_2` (each with `.html` and `.txt`). Script: `scripts/voice_fetch_faa.sh`. AIM 4-1 is saved but not analyzed.

**Limits.** Free ATC audio is non-US and restricted: ATCO2 (Europe, commercial and non-commercial for the 4 h and training sets via ELRA, research only for the free 1 h subset), UWB-ATCC (Czech, CC BY-NC-SA 4.0) and ATCOSIM (license unconfirmed). Every file exceeds 20 MB and none was downloaded. LiveATC's terms returned 403 and were not read; treat it as unusable. The radio-noise recipe (TTS, 300-3000 Hz bandpass, compression, noise at 5-20 dB SNR, PTT clicks) and the noise corpora are unverified. Airline telephony names ("American", "Southwest", "SkyWest") are from general knowledge; FAA JO 7340.2 holds the authoritative table and was not read. Taxi "follow" wording and KPHX ramp-transfer phrases were not found.

**Synthetic.** All pilot speech and radio noise, generated from the template catalog; all ASR test audio.

### 3.12 Rules and agreements (partial)

**Source and verification.** JO 7110.65BB (with Changes 1, 2 and 3) is the current order, verified in the atpubs HTML and the 927-page consolidated PDF ([consolidated PDF](https://www.faa.gov/documentLibrary/media/Order/7110.65BB_Bsc_w_Chg_1_2_and_3_dtd_7-9-26_Final.pdf)). Change 2 moved the contents of JO 7110.126 into 7110.65 for terminal facilities and cancelled 7110.126B, while en route facilities keep the legacy standards. The aircraft-and-rules note catalogs about 60 implementable rules with numeric parameters: departure and arrival SRS separation (3,000, 4,500 and 6,000 ft), departure wake intervals (3, 2 and 2 minutes, plus 1 minute for opposite direction and intersection cases), LUAW limits (90 seconds advisory, 6 NM traffic call), radar departure-arrival checks (2 mi at roll start), parallel runway minima, taxi and crossing rules (one crossing per clearance, 1,300 ft multi-cross limit), and the ground-local coordination rule that ground must get local's approval before any active-runway crossing. The wake distance matrices (A leader gives 5, 6, 6, 7, 7, 7, 8, 8 NM to followers B to I) were extracted from the HTML tables.

**On disk.** `data/reference_and_rules/faa_7110_65BB_chg3_consolidated/order.pdf`, `faa_7110_65_chap{2_section_1, 3_section_1, 3_section_7, 3_section_8, 3_section_9, 3_section_10, 5_section_5, 5_section_8}/page.html`, `faa_jo_7110_126A/o.pdf` (comparison only), `faa_jo_7360_1K/order.pdf`. Scripts: `rules_html_to_text.py`, `rules_extract_tables.py`, `rules_pdf_to_text.py`.

**Limits.** The table numbering mismatch in 5-5 is unresolved (section 4). JO 7210.3 (facility administration) and JO 7110.118 (LAHSO) were not downloaded, so rules that delegate to them are cited by pointer only. Military carve-outs in 3-9-4 were omitted. No source covers the tower safety-logic alert thresholds. The only agreement text found is an unofficial ZAB document hosted by a VATSIM ARTCC (ZAB handles point-outs to P50; 20 degree turns; 5 nm radar separation), unverified and possibly dated ([LA ARTCC ZAB doc](https://laartcc.org/document/albuquerque-artcc-zab)). P50 is the TRACON for PHX, FFZ and DVT per an aggregator ([123atc](https://123atc.com/facility/P50)), unverified.

**Synthetic.** P50/ZAB letters of agreement, KPHX standard taxi routes, local LUAW and runway-use practice, and tower safety-logic behavior.

### 3.13 Scenario data (partial)

**Source and verification.** Two real haboob replays exist as clean CSVs built from IEM METARs: 25 Aug 2025 (71 rows, 2025-08-25 23:00Z to 2025-08-26 03:30Z) and 5 Jul 2011 (13 rows). The 2025 sequence, in UTC: a Dust Storm Warning at 22:50Z, a wind shift at 00:19Z, 28G42 with 2 SM and thunderstorm at 00:33Z, visibility 0 SM with vertical visibility 0 ft and gusts to 59-61 kt from 00:35Z, vis back to 3/4 SM by 00:53Z, dust code gone by 01:15Z, and rain tapering by about 03:15Z. By my subtraction the warning came about 90 minutes before the shift. Low visibility lasted about 18 minutes and dust about 35 minutes. The 2011 event was slower and deeper (1/8 SM, RVR 600-800 ft, vertical visibility 200-300 ft, visibility under 1 SM for roughly 40 minutes). Both show RVR groups for 07L, so the airport was in east flow. The 2025 PSR warning list has 17 Dust Storm Warnings usable for other scenarios ([IEM VTEC events](https://mesonet.agron.iastate.edu/json/vtec_events.py?wfo=PSR&year=2025)). Operational impact: the airport stopped all flights for about an hour, with 200+ delays at the peak and Terminal 4 roof leakage ([azfamily](https://www.azfamily.com/2025/08/26/major-delays-phoenix-sky-harbor-after-severe-storms-temporarily-grounded-flights/)).

**On disk.** `data/weather_and_status/haboob_clean_2025-08-25/`, `haboob_clean_2011-07-05/`, `iem_asos_*`, `iem_alerts_dsw/psr_vtec_events_2025.json`. The NWS "Maxwell Sky Harbor Dust Impacts" PDF is at `data/kphx_operations/nws_phx_dust_impacts/dust_impacts.pdf` with unread content.

**Limits.** No FAA-sourced ground stop times or diversion counts, no second-resolution wind trace, and 5-minute AUTO rows often lack wind direction. The 2011 impact counts and a 6 Aug 2026 dust event ([WSMV](https://www.wsmv.com/2026/08/06/rare-category-5-dust-storm-slams-phoenix-arizona-triggers-airport-delays/)) are thin. No official visibility threshold for suspending operations was found.

**Synthetic.** The scenario script itself, the traffic state at storm onset, the ground stop times, the diversion and delay counts, and the recovery profile. Use the METAR timeline as truth for weather and label the operational response as modeled.

### 3.14 Evaluation baselines (synthetic-only)

**Source and verification.** No benchmark values were sourced. The metrics are the team's own design as pure functions over an append-only event log: separation violations, runway conflicts (overlapping occupancy windows), clearance inconsistencies, taxi deadlocks (wait-for-graph cycle for over a set time), communication errors (readback mismatch rate and ASR word error rate), recovery time, delay (mean, P95, total), taxi delay against unimpeded time, and throughput per hour and per runway. The honest baseline is first-come-first-served sequencing. Real reference points available: 487,143 operations in 2025 (about 1,335 per day averaged) and the airport's claim of about one movement per minute.

**On disk.** None. The formulas live only in the notes.

**Limits.** The single-runway throughput range quoted in the notes is from memory and unverified. The airborne 3 NM or 1,000 ft separation and the 50 m taxi gap are placeholders, not sourced KPHX thresholds. BTS taxi-out averages for PHX were not obtained.

**Synthetic.** Taxi-time and delay baselines until BTS PHX rows are pulled; use "unimpeded taxi time" computed from the model's own graph.

### 3.15 Explainability and decision-record schema (partial)

**Source and verification.** A draft JSON Schema (2020-12) for an immutable decision record is in the build-logistics note: decision id, sequence number, simulation and wall time, agent kind (rule engine, solver, LLM, human), trigger, state snapshot with per-source timestamps, age and quality (live, stale, recorded, synthetic), rules fired with versions, binding constraints and slack, chosen action, rejected alternatives with reasons, clearance state machine (proposed through executed, amended, cancelled), voice fields (ASR confidence, readback match) and explanation text. The W3C PROV overview supports the entity, activity and agent framing ([W3C PROV](https://www.w3.org/TR/prov-overview/)); the specific relation mapping is the author's, from memory.

**On disk.** No `.schema.json` file exists. Copy it from `research_notes/Phoenix airport build data catalog/build_logistics.md` into `schemas/` in the repo.

**Limits.** No SESAR or EASA explainability source was fetched, and no ATC audit-trail precedent was researched. The `quality` field with a "synthetic" value is the schema's way of keeping the data provenance honest in every record.

**Synthetic.** The whole record is generated by the build.

### 3.16 Map assets (ready)

**Source and verification.** OpenFreeMap Liberty style (`https://tiles.openfreemap.org/styles/liberty`): no key, parses as a MapLibre version-8 style; limits are 4 parallel requests, a unique User-Agent and 3,000 tiles or 50 MB per offline download ([OpenFreeMap quick start](https://openfreemap.org/quick_start/)). USGS Imagery Only (NAIP) raster tiles: no key, a real KPHX tile at z16 was fetched (256x256 JPEG, 15 KB), practical maximum zoom 16 for sharp imagery, public domain with the credit "USDA, USGS The National Map: Orthoimagery" ([USGS service](https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer)). Protomaps PMTiles is the offline fallback, with the extract syntax unverified ([Protomaps docs](https://docs.protomaps.com/basemaps/downloads)).

**On disk.** `data/build_logistics/openfreemap_liberty_style/liberty.json`, `usgs_naip_tile_kphx/tile.jpg`. Script: `scripts/build_fetch_logistics_samples.sh`.

**Limits.** deck.gl TripsLayer and IconLayer, icon-set licenses and Mapbox pricing were not fetched (unverified). The OpenFreeMap terms page and USGS tile usage limits were not read. Suggested attribution string: "OpenFreeMap © OpenMapTiles | © OpenStreetMap contributors | USDA, USGS The National Map: Orthoimagery". Draw your own plane silhouette to avoid any icon license question. Query Overpass once and commit the GeoJSON; never hit it at demo time.

**Synthetic.** Nothing.

### 3.17 Costs and limits (ready)

**Source and verification.** Claude prices were read from the official pricing page today: Sonnet 5.5 at $2 input and $10 output per million tokens, Opus 5.5 at $4 and $20, Haiku 5.5 at $0.10 and $0.50 for prompts up to 100k, Fable 5.2 at $8 and $40 ([Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)). OpenAI audio prices are from the official pages: whisper-1 and gpt-4o-transcribe $0.006 per minute, gpt-4o-mini-transcribe $0.003 per minute, gpt-4o-mini-tts $0.60 per million text tokens plus $12 per million audio tokens ([OpenAI pricing](https://developers.openai.com/api/docs/pricing)). Deepgram Nova-3 streaming $0.0048 per minute with $200 free credit; AssemblyAI streaming from $0.15 per hour with $50 free credit; AWC, OpenFreeMap, USGS and Protomaps are free. Vercel Hobby allows 100 deployments per day, 100 MB static upload via CLI and cannot connect to repos owned by a Git organization ([Vercel limits](https://vercel.com/docs/limits)). The build-logistics note's own arithmetic puts a weekend at roughly $15 to $55, dominated by coding-assistant usage.

**On disk.** Notes only.

**Limits.** OpenAI text-model prices, the replacement transcription prices, Azure and Google prices, Fly, Render and Railway free tiers, OpenSky's terms and Mapbox were not verified. The airplanes.live 1 request per second figure is from a search summary and moot given the 403. Ask sponsors (Gemini, Vultr, MongoDB) about credits before paying.

**Synthetic.** Nothing.

### 3.18 Hackathon logistics (partial)

**Source and verification.** [sunhacks.io](https://sunhacks.io) (verified, fetched through a summarizing tool): SunHacks 9.0, Nov 14-15, 2026, Student Pavilion, ASU Tempe, in person. Check-in Saturday 9 AM, hacking from 11:10 AM, projects due Sunday 11 AM on Devpost, expo and judging 12-2 PM, close 4:30 PM, about 24 hours of hacking. No required tracks, teams of one to four with every member registered, students over 18 and graduates within one year (the State Press reports the same), free. Sponsors listed include MongoDB, Gemini, Vultr, TinyFish, Supermemory, JFFVentures, Amazon, General Dynamics, MATLAB, Maritime, GoDaddy Registry, SpaceXSI, Tiger Data and Solana. The MLH code of conduct applies.

**Limits.** Prizes, judging rubric, sponsor credits, the pre-existing-code policy, video requirement and AI-disclosure rule are not published; sunhacks.vercel.app returned HTTP 402; the Devpost page was not found. Contact from a search result: team@sunhacks.io. MLH guidance (public code link, keys removed, frameworks listed, AI use disclosed) is general, from a snippet. Work done before 11:10 AM today sits in a gray zone, so keep research as planning, start the main commits after kickoff, and disclose pre-written helper code and AI assistance.

**Synthetic.** Nothing.

### 3.19 Repo and tooling (partial)

**Source and verification.** The repo `ethany1017/Sunhacks-2026` was not accessed, so its contents and branch rules are unknown. The proposed layout is a Python `sim/` package (src layout, simpy or a custom tick loop, pydantic v2, shapely, networkx, fastapi with a WebSocket, pytest, ruff), a Vite and TypeScript `web/` app (MapLibre, deck.gl) with `public/data/kphx_aeroways.geojson`, a shared `schemas/` folder, git-ignored `data/` and `logs/`, `.env.example`, and CI with ruff, pytest, `npm ci && npm run build` and a gitleaks step. Collaboration: Sim and web in separate directories, one file per scenario, JSON Lines logs, `schemas/` changed only with both approving, one person regenerates lockfiles. Secrets stay in a git-ignored `.env` and host environment variables, the browser never gets LLM keys, and a secret scan runs before the repo goes public.

**Limits.** No dependency version was verified; pin with lockfiles on day one. Do not commit the 16 MB d-TPP metafile, the CIFP zip, PDFs or the large downloads; commit small fixtures only.

**Synthetic.** Nothing.

## 4. Cross-source conflicts the notes flag, plus those the cross-check surfaced

| # | Conflict | Sources | Handling |
|---|---|---|---|
| 1 | Clearance delivery frequency. 118.1 (UHF 269.2) appears in two notes. The voice note says AirNav lists it, the operations note says AirNav lists no clearance delivery frequency. I confirmed on disk that the AirNav page (`kphx_operations/airnav_kphx/kphx.html`) does read "CLEARANCE DELIVERY: 118.1 269.2", so the operations note is wrong on that point. A search snippet of the FAA kneecard also says 118.1/269.2 (the PDF returned 403). But the D-ATIS fetched at 2051Z on Nov 14 says "118.1 OTS NEW CD FREQ 133.7". The geometry note's airport-diagram extraction shows "Clearance Delivery 132.55, ATIS 118.1", which contradicts AirNav and looks scrambled. | AirNav (FAA data effective 10/29), kneecard snippet, D-ATIS, diagram text | Unverified. 118.1 is likely stale; 133.7 rests on one third-party feed. Put the frequency in config, mark both, and render the airport diagram as an image to settle it. |
| 2 | Diagram frequency text versus AirNav: the diagram extraction lists Tower 127.575 and Ground 118.7/120.9, while AirNav gives D-ATIS 127.575, tower 118.7/120.9 and ground 119.75/132.55. | geometry note, AirNav | Use AirNav; treat the diagram text extraction as unreliable. |
| 3 | Tower-to-runway mapping: the voice note calls AirNav's mapping ambiguous. The saved AirNav text reads tower 118.7 for RWY 08/26 and 120.9 for RWY 07L/25R and 07R/25L, with UHF 278.8 and 254.3. | voice note, operations note, AirNav | Use the AirNav mapping, flagged until checked against the Chart Supplement. |
| 4 | Runway order: the geometry note calls 08/26 the south runway and the "north pair" 07L/07R; the operations note and AirNav put 08/26 north. FAA polygons on disk put 08/26 northernmost (my check). | geometry note, operations note, FAA ArcGIS | North 08/26, middle 07L/25R, south 07R/25L. Fix the geometry note's wording. The weather note adds that some sources swap the lengths of 07L/25R and 07R/25L. |
| 5 | Runway headings: the operations note gives parallel runways at true heading 090/270; the weather note gives approximate magnetic 070/250 and 080/260. Magnetic variation is 12 deg E from a 2000 epoch (about 9.4 E in 2026 by simple extrapolation, unverified). | operations, weather, geometry notes | Compute headings from the FAA geometry; use a current magnetic model. |
| 6 | Field elevation: 1,134.8 ft (FAA, AirNav) versus AWC station elevation 338 m (about 1,109 ft). | AirNav, AWC | Use the FAA value. |
| 7 | Haboob impact counts, 25 Aug 2025: 200+ delays at peak (azfamily); over 300 delayed (FlightAware, search excerpt); 177 delayed (another outlet citing FlightAware); 104 delayed, 3 cancelled, 1 diverted; about 40 or more diverted; 43 diversions (Flightradar24 as cited); 55,000 customers without power. | azfamily, KVIA, View from the Wing, AVSN, CNN and others | Do not state a count as fact. Cite "about an hour ground stop" and "200+ delays" with the azfamily attribution, or show a range with sources. |
| 8 | Haboob dates and timing: one outlet dates the storm 26 Aug (UTC versus local confusion); ground stop "about an hour" (azfamily) versus the earlier report's 30-60 minutes; 2011 shutdown "8:00-8:45 pm" (news excerpts) versus METAR dust visibility under 1 SM from about 02:53Z to 03:35Z. | news, IEM METARs | Use METARs for weather timing; ground stop timing stays "reported" and unverified. |
| 9 | Passengers: 51,620,420 (monthly PDF) versus the facts page's terminal split summing to 51,618,649 (a 1,771 difference). The terminal split differs by source: facts page T3 12,079,085, T4 36,725,589 and international 2,813,975; PDF T3 12,439,195 and T4 39,181,225 with international inside T4. Also 487,143 versus 487,142 operations, and cargo 322,741 tons (facts) versus 327,017 (PDF). The traffic note called the statistics "search snippets only" while the operations note read the PDF. | Dec 2025 PDF, facts page | Prefer the PDF; treat the facts page as marketing. Round on slides. |
| 10 | Chandler and other counts (from the earlier notes): KCHD operations over 228,000 (city), 234,300 (2023 listing), 161,750 (older), and 19,536 for one month; KDVT 490,702 for 2025 on the airport facts page versus a partial 257,280 year-to-date sheet; neighbors' counts conflict across sources and none is ATADS-verified. | earlier notes and report | Not KPHX build inputs. Do not quote on stage. |
| 11 | JO 7110.65BB distance tables: the text refers to TBL 5-5-1 and 5-5-2 while the captions read 5-5-3 and 5-5-4. Tables were mapped by document order, so the differences for follower category I (leaders B, C, D and F) are unverified. The glossary also calls categories H and I "under 41,000 lb," looser than JO 7110.126A's split at 15,400 lb. | 7110.65BB HTML, JO 7110.126A | Check against the PDF page images before hard-coding; use the per-type JO 7360.1K mapping. |
| 12 | BTS PREZIP access: the traffic note got HEAD responses for the September 2025 and August 2026 files; the operations note got 404 for 2024_8, 2025_1, 2025_6, 2025_8 and 2026_1 on both hosts. | traffic and operations notes | Resolved after the report was drafted: `https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_YYYY_M.zip` returns 200 for 2026_8, 2026_7 and 2025_8 (the 404s came from a different URL pattern). Files are 30-33 MB zipped and about 275 MB unzipped. |
| 13 | OpenSky: the build note says it needs an account; the traffic note shows anonymous access works at 400 credits per day. | build and traffic notes | Anonymous works for live current data. |
| 14 | airplanes.live: build note lists it as free at 1 request per second (search summary); the traffic note observed HTTP 403. | build and traffic notes | Blocked; do not rely on it. |
| 15 | OSM sample: the build note says OSM was not fetched; the geometry note fetched it and the file is on disk. | build and geometry notes | The file exists. |
| 16 | Named SIDs ZIDOG and WETAL are in AirNav's frequency list but not in the current d-TPP or CIFP lists. | AirNav, d-TPP | Use CIFP for procedures. |
| 17 | FlightAware AeroAPI free credit: about $5 versus $10 or $20 for feeders, sources conflict. | geekflare and forum | Moot; no key available. |
| 18 | Misreading corrected: a search summary read adjacent columns on a Virginia Tech slide as runway occupancy seconds (44.9, 44.7, 44.1); the slide text does not label them. OpenAP wrap-file units are also unstated. | aircraft-and-rules note | Do not use those figures as ROT. |
| 19 | NOTAM Management Service timeline: mid-April 2026, later in 2026, or Oct 1, 2026, depending on the secondary source. | FAA briefing and blogs | Irrelevant to the build; NOTAMs are synthetic. |
| 20 | Terminal 2: OSM has no polygon, which is consistent with its closure, but the closure was not sourced. | geometry note | Do not assert it. |

## 5. Gaps and what to do about each

| Gap | Proposed stand-in or next step |
|---|---|
| Taxi-time baselines for PHX | **Closed (verified).** BTS August 2026 (31 days, 29,524 PHX flights): taxi-out mean 16.6 min, median 14, p90 26; taxi-in mean 7.2, median 6, p90 11. August 2025 is similar (taxi-out mean 15.3). Haboob day 2025-08-25 (local time): 976 flights, 40 diversions, taxi-out mean 17.9 and, for flights with wheels-off in the 19:00 hour, mean 70 min with p90 124; the 20:00 hour averaged 42. Files: `data/kphx_traffic/bts_otp_2026_8/derived/` and `bts_otp_2025_8/derived/`, script `scripts/kphx_traffic_bts_phx.py` (run). Caveat: BTS covers only large US reporting carriers, so it excludes GA, cargo, military and foreign carriers, and real movements are higher. BTS taxi-out is gate pushback to wheels-off, so it includes ramp time. |
| Hourly arrival and departure banks | **Mostly closed (verified for reporting carriers).** August 2026 scheduled departures average about 476 a day (busiest local hours: 08:00 at about 57 a day, 10:00 at about 43, 15:00 at about 35), arrivals about 476 a day; per-hour table in `phx_hourly_summary.csv`. Use 15-minute bins if finer detail is needed. The airport's 1,300 daily movements claim includes traffic BTS does not. |
| Aircraft mix | Weight by 2025 passenger shares from the Dec 2025 PDF (American 20,893,285; Southwest 17,201,906; Delta 3,657,003; United 3,216,812; Frontier 2,304,078; Alaska 1,731,389) and the types seen in the live sample, plus a GA tail. Validate against the BTS tail numbers later. |
| Runway occupancy by type | Use a placeholder (about 55 s mean from Zurich, flagged) scaled by touchdown speed and landing roll (B738 touchdown distance about 390 m, sd 65 m, from the NEXTOR slide). Better source leads: ACRP Report 79 and the VT ASDE-X work, not retrieved. |
| P50 and ZAB agreements | Synthetic message set with the unofficial defaults (20 degree turns, 5 nm) flagged unverified. |
| Ramp control | Model the ramp as unmanaged with ATC authority at the movement-area boundary (7110.65 3-7-2). Do not claim a KPHX ramp procedure. |
| NOTAMs | Synthetic generator seeded from the ATIS notices (RWSL out of service at E13 and H3, cranes, bird activity). |
| Runway condition reports | Wet-runway flag tied to the weather scenario; no feed exists. |
| CAT II and III at KPHX | Assume CAT I minimums only; mark any lower minimums as unsourced. |
| KPHX tower-to-runway frequency mapping | Use AirNav's mapping (118.7 for 08/26; 120.9 for 07L/25R and 07R/25L), then confirm against the Chart Supplement or a rendered diagram. |
| Declared distances | Fetch NASR (retry the 503) or use the Chart Supplement; interim use the runway lengths and the verified runway 8 displaced threshold and 10,591 ft landing distance. |
| Standard taxi routes and ramp spots | Generate routes by shortest path on the OSM graph with crossing edges flagged as requiring clearance, particularly the F8 to F10 crossings of 07L/25R. |
| Runway-use and flow thresholds | Default east flow early and west later, with a configurable tailwind switch; ATIS today shows west flow. |
| AAR and ADR | Placeholder table, keyed by configuration, flagged as the 2014 range. |
| Taxi speed, separation and deadlock | 15 kt straight (unverified), wingtip clearance from ADG plus a time buffer, wait-for-graph detection. Read AC 150/5300-13B if time allows. |
| Pilot readback wording and errors | Templates from the controller phrases; inject wrong runway, wrong digit and omitted hold-short errors for ground truth. |
| Real KPHX voice audio | None legally downloadable; use TTS plus radio filter. |
| OSM fidelity | Run the junction check: pair each runway-taxiway junction with holds, review the 82 free endpoints and 28 unsnapped holds. |

## 6. Verified changes from the earlier report

| Earlier statement | Now verified |
|---|---|
| Wake-separation values to be copied from JO 7110.126 | The current order is JO 7110.65BB with Changes 1 (8-7-25), 2 (1-22-26) and 3 (7-9-26). Change 2 incorporated 7110.126 into 7110.65 for terminal facilities and cancelled 7110.126B. The A-to-I matrices were extracted (caveat in conflict 11). |
| SunHacks 2026 date unknown and possibly passed | SunHacks 9.0 is Nov 14-15, 2026, in person at the ASU Tempe Student Pavilion, with hacking from 11:10 AM Saturday and Devpost due Sunday 11 AM. About 24 hours, not 23 or 48. |
| OpenAI TTS shutdown "reported" Jan 6, 2027 | Confirmed on OpenAI's deprecations page: Jan 6 (tts-1, tts-1-hd, gpt-4o-mini-tts), Jan 20 (realtime and gpt-audio families) and Feb 26, 2027 (whisper-1, gpt-4o-transcribe and mini). The earlier recommended gpt-4o-mini-transcribe is on the Feb 26 list. Replacement prices were not extracted. |
| airplanes.live listed as a free non-commercial source | Returned HTTP 403 requiring an emailed request; ADS-B Exchange returned HTTP 402 (paid). adsb.lol and adsb.fi work keyless. |
| OpenSky needs a free account | Anonymous current-state access works at 400 credits per day. |
| FAA pages returned 403 | Most FAA atpubs HTML and documentLibrary PDFs are readable with curl and a browser User-Agent. Still blocked: the kneecard, procedure boards and the 2014 capacity profile. |
| FAA hot spot list from a June 2025 listing with unconfirmed currency | Cycle 2611 hot spots HS 1 and HS 2 downloaded and identical to cycle 2610. |
| No national FAA taxiway dataset found; runway layer endpoint-based | FAA ArcGIS provides runway, taxiway (319 polygons) and apron (28 polygons) outlines for PHX. |
| Aug 25, 2025 ground stop of 30-60 minutes depending on the report | About one hour per the airport spokesperson article; weather timeline from METARs is verified. |
| KPHX operations counts conflict | 487,143 from the airport's PDF and 487,142 from its facts page; 2024 was 485,745. |
| Primary airport KDVT, KIWA fallback | The assignment is now KPHX. This is a decision change, not a data finding. The earlier report called KPHX the hardest to model in 24 hours, and the geometry data supports it but the state data does not. |
| No runway-occupancy or taxi-speed figures researched | Partially researched: KCLT pushback and ramp transit and Zurich arrival occupancy are verified for those airports. Nothing exists for KPHX. |

## 7. What must be synthetic, in one place

Ground vehicles and every non-ADS-B target; gate and stand assignment and occupancy; pushback, taxi and crossing clearances; all controller and pilot communications and the radio audio; filed flight plans, SIDs, STARs and squawks for scripted traffic; SFDPS, TFMS and ASDE-X-equivalent streams including noise and dropouts; ground stops, delay programs, EDCTs and miles-in-trail; NOTAMs, closures and runway condition; P50, ZAB and tower agreements; ramp control; schedules for any future date; and the operational response to the haboob (diversions, delay counts, ground stop times). The honest framing is that the geometry, procedures, aircraft performance, rules and weather are real, and the airport's state and traffic management are modeled.

## 8. Conclusion

The data picture is lopsided in a useful way. The static and rule-bearing layers (geometry, procedures, aircraft categories, 7110.65BB rules, weather history) are better sourced than most of the plan assumed, and the real risks are small and fixable: an unresolved wake-table numbering, a clearance delivery frequency that two notes repeat as 118.1 while the live ATIS says it is out of service, and a note that misplaces the runways. The dynamic layers that make an airport interesting (who is where on the ramp, what the TMI desk is doing, what NOTAMs are active) have no public feed and cannot acquire one in 20 hours, so the demo's credibility comes from labelling them synthetic and letting the decision records carry a `synthetic` quality tag.

The most valuable single data action is the BTS download. It would convert flight intent, hourly banks, aircraft mix and taxi baselines from placeholders to sourced numbers in one pull, and it is blocked only by a file-size rule and one unresolved URL pattern.

## 9. Do these first (next few hours)

1. Ask the organizers at the venue (or at team@sunhacks.io) about pre-existing code, the rubric, whether a video is required, and sponsor credits; write the AI-use disclosure now.
2. Start a background poll of adsb.lol near KPHX (`scripts/kphx_traffic_poll_rate.sh`, polite interval of 5 s or more, since its rate limits were not read) to collect real Saturday-evening taxi tracks for calibration.
3. Freeze the geometry: load `kphx_aeroway.json`, merge the three 08/26 ways by ref, snap the 28 off-network holds, review the 82 free endpoints, confirm the north-middle-south runway order against the FAA polygons, flag runway crossings (F8 to F10) as needing clearance, and commit a small GeoJSON to `web/public/data/`.
4. Put frequencies in a config file with flags (clearance delivery 118.1 versus 133.7, tower and ground per AirNav) and render the airport diagram PDF to an image to settle the conflicts.
5. Hard-code the wake tables from 7110.65BB, but first check tables 5-5-3 and 5-5-4 for the follower-I differences against `faa_7110_65BB_chg3_consolidated/order.pdf`.
6. Decide on the BTS file: get the user's go-ahead for the roughly 32 MB download, HEAD `2026_8` and `2025_9` to find the working URL, and filter to PHX. If declined, set banks, mix and taxi times as labelled parameters.
7. Wire AWC through a backend proxy, load the 2025 haboob CSV as the scripted weather, and poll D-ATIS for runway configuration and notices.
8. Load the aircraft CSV, add SR20 and S22T, scale approach speed down for landing weight, and skip OpenAP unless its units are spot-checked against a known B738 number.
9. Choose the voice stack with the shutdown dates in mind (OpenAI audio models retire between Jan 6 and Feb 26, 2027), run the 20-utterance bench on your own TTS audio, and score callsign, runway and digit exact-match.
10. Commit the decision-record schema to `schemas/`, implement the event log and the metrics as pure functions, and build the first-come-first-served baseline before any smarter planner.
11. Run a secret scan, keep keys in `.env`, and make the repo public-safe before the Sunday 11 AM submission.
