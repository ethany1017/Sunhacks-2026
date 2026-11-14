# Hackathon Data

**Autonomous Airport Control · SunHacks 2026 · Source review November 14, 2026**

**Expanded research revision: November 14, 2026 (America/Phoenix).**

> **Edited Nov 14, 2026 (Gus).** This is Ethan's research file, reworded so it matches the project: one fully autonomous agent runs the airport, with no independent checker, no human approval step and no real-world use. Facts, numbers and links are unchanged. The file was written for **KDVT**; the team has since chosen **KPHX** (see `data-catalog.md`), so KDVT items below are fallback material.

**Navigation:** [Demonstration](#recommended-demonstration) · [Sources](#airport-geometry-airspace-and-procedures) · [Implementation](#simulators-and-implementation-order) · [KDVT build checklist](#kdvt-build-checklist) · [Reference records](#concrete-airport-and-aircraft-records) · [Acquisition guide](#download-and-adapter-guide) · [Evaluation evidence](#incident-data-and-evaluation-fixtures) · [Budget](#request-budget-and-acquisition-priorities)

## Recommended demonstration

Use **Phoenix Deer Valley (KDVT/DVT)** as the first airport. The FAA documents two parallel runways, Class D underlying Phoenix Class B, and HS1 marked at B5 on the current diagram. The FAA web briefing also discusses B9, a source/version difference that needs reconciliation. Model one B5 crossing on 7R/25L, its hold points, two fictional stands, one simplified arrival handoff and one departure release. Keep the second runway visible for context. KDVT provides a documented crossing with less model scope than a full Sky Harbor terminal and airline ramp. Use synthetic stand/ramp operations, and treat airport selection as a project recommendation. [FAA Deer Valley briefing](https://www.faa.gov/flight_deck/dvt) · [FAA KDVT airport diagram, October 29 cycle](https://aeronav.faa.gov/d-tpp/2611/06646AD.PDF)

| Mode | Inputs | Output authority |
|---|---|---|
| Autonomous simulation | Versioned airport subset, synthetic traffic and airport operations, simulated pilot audio, frozen weather | The agent issues clearances to simulated pilots on its own. The backend runs without the dashboard. |
| Historical replay | Immutable event logs or licensed samples, original event times, replay clock | Display and hypothetical evaluation. A counterfactual simulation receives a separate run ID. |
| Live observation | Authorized track vendor and available official contextual feeds | Read-only surface and traffic display with hypothetical proposals. The process has no publisher for movement commands. |

The core demonstration should work without internet or provider credentials. Pin graph, rule pack, seed, weather, audio and initial state. Live credentials, rights at the chosen deployment, KDVT reception and real-world latency remain to be verified. The tables distinguish published service characteristics from proposed demo settings.

## Airport geometry, airspace and procedures

| Source and access | Schema and use | Gaps and implementation |
|---|---|---|
| **FAA NASR**, public bulk CSV and selected AIXM downloads on a 28-day cycle. The active cycle on November 14 is October 29, 2026; November 26 is the next effective release. [Cycle list](https://www.faa.gov/air_traffic/flight_info/aeronav/Aero_Data/NASR_Subscription/) · [October 29 release and CSV README](https://www.faa.gov/air_traffic/flight_info/aeronav/Aero_Data/NASR_Subscription/2026-10-29/) | Start with Airports and Other Landing Facilities (APT). Use the release layout to join facility, runway and runway-end records. Keep airport ICAO and FAA IDs, runway width/length, end and threshold coordinates, elevations, declared distances and surface. Keep source units and vertical datum. | Reciprocal runway ends are one occupied pavement. Threshold and pavement end may differ. Cycle data do not capture a closure that occurred after publication. |
| **FAA d-TPP and Chart Supplement**, public search/download. d-TPP digital issues every 28 days, including interim change notice. Chart Supplement is on a 56-day cycle. [d-TPP product](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/dtpp/) · [Airport chart search](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/dtpp/search/) · [Chart Supplement](https://www.faa.gov/air_traffic/flight_info/aeronav/Digital_Products/dafd/) | Match published taxiway designators, crossings, risks, markings, frequency/facility references and procedures. Review the runway and both sides of the selected crossing. Encode a directed graph with hold lines, movement-area boundaries, protected pavement and designated crossing edges. | Diagrams are publication references. Check each graph segment and hold line independently. Exact movement boundaries, stop bars, gate clearances, survey accuracy and routine local control procedures require site confirmation. |
| **FAA Airports GIS / ADIP and FAA AIS ADDS.** Airports GIS documentation and the ADIP public portal provide discovery. ADDS offers layer-specific downloads and feature services. [Airports GIS](https://www.faa.gov/airports/planning_capacity/airports_gis_electronic_alp) · [ADIP public](https://adip.faa.gov/agis/public/) · [ADDS](https://adds-faa.opendata.arcgis.com/) | Ask airport operations for the applicable survey-grade layout, hold lines, taxiway restrictions, apron and stand polygons. Check ADDS layer metadata for date, CRS and available CSV, JSON, KML or Shapefile outputs. | Public availability of complete KDVT GIS and reuse rights are unconfirmed. If using an OpenStreetMap context layer, retain attribution and applicable ODbL obligations and verify any movement features against airport sources. [OSM terms](https://www.openstreetmap.org/copyright) |
| **FAA CIFP**, public download under posted error-notification agreement, ARINC 424-style records on a 28-day cycle. Complement with **FAA JO 7400.11** airspace definitions, ADDS polygons and **FAA Digital Obstacle File**. [CIFP and agreement](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/) · [CIFP format notes](https://aeronav.faa.gov/Upload_313-d/cifp/CIFP%20Readme.pdf) · [Airspace designations](https://www.faa.gov/regulations_policies/orders_notices/index.cfm/go/document.current/documentNumber/7400.11) · [Obstacle data](https://www.faa.gov/air_traffic/flight_info/aeronav/obst_data/) | Store procedure/transition, leg order, path terminator, fix, track, turn, altitude/speed qualifiers and missed approach. Store volume geometry, floor/ceiling, vertical reference and activation. Verify against charts and restrictions. | Course, arc, hold, heading and vectors must retain their specific semantics. A route with a discontinuity needs explicit treatment. Charted boundaries do not describe controller ownership, site-specific releases or local agreement terms. Use a synthetic handoff and approach for the first demo. |

Store URLs, checksums, access terms, cycle and import version for each source. Keep links and a reviewed reduced graph in the demo repository. Commit externally sourced data only where the applicable terms permit.

## Tracks, plans, aircraft and ground state

| Source | Data and schema | Access, latency and limitations |
|---|---|---|
| **Simulator** | Stable flight/vehicle IDs, positions, velocity, phase, taxi edge and occupancy; separate scenario truth, delayed observations and authorization events. | First choice. Inject untracked vehicles, silence, stale samples and wrong readbacks. Only simulator truth should adjudicate the test's actual movements. |
| **OpenSky state vectors and research data** | REST GET /states/all with geographic bounds; icao24, callsign, nullable time_position, last_contact, WGS84 position, barometric/geometric altitude (m), velocity and vertical rate (m/s), true ground track (degrees), on_ground. last_contact can advance while the last position remains stale. | OAuth2 client credentials or anonymous. Documented snapshot resolution is 5 s authenticated and 10 s anonymous, separate from receipt latency. Daily budgets are 400 anonymous, 4,000 standard and 8,000 qualifying feeders. A bounding box of at most 25 square degrees costs one credit. Handle 429 and retry metadata. Verify written licensing for commercial use and live automated operational integrations under provider terms. Prefer explicitly permitted historical data for replay; no actionable clearances or complete surface picture. [REST schema and rate limits](https://openskynetwork.github.io/opensky-api/rest.html) · [Terms](https://opensky-network.org/about/terms-of-use) · [Scientific datasets](https://opensky-network.org/data/scientific) · [Trino access](https://opensky-network.org/data/trino) |
| **ADS-B Exchange / FlightAware** | ADS-B Exchange includes hex, track, gs (kt), alt_baro (ft or ground), lat/lon and seen_pos/seen ages. FlightAware products can add flight status, routes, gates or timing fields where populated. | Check API keys, permitted display, cache and recordings, and plan-specific cost and delivery policy. In the ADS-B Exchange stream, subtract seen_pos from the feed's now timestamp to estimate position receipt time. The underlying aircraft measurement timestamp is unavailable in that example. AeroAPI is usage-based REST; Firehose is licensed push. FlightAware personal terms restrict some display and data combinations. Neither vendor's track data establishes positive runway vacancy or vehicle coverage. [ADS-B Exchange fields](https://www.adsbexchange.com/version-2-api/) · [Stream timing](https://streaming-docs.adsbexchange.com/guides/timestamps) · [Samples](https://www.adsbexchange.com/data-products/sample-data/) · [Commercial access](https://support.adsbexchange.com/hc/en-us/articles/37363886073613-I-m-building-a-project-for-my-company-but-we-aren-t-making-money-off-of-it-can-I-get-a-free-API-key) · [FlightAware products](https://www.flightaware.com/commercial/data) · [AeroAPI personal license](https://www.flightaware.com/commercial/aeroapi/AeroAPI_Personal_License_Jan2025.pdf) |
| **FAA SWIM SFDPS / TFMS TFMData** | SFDPS publishes ERAM en-route flight and track data in SWIM XML, with FIXM/AIXM transformations and GUFI matching. TFMS Flight Data includes filed plans, amendments, cancellations and tracks. Flow Information carries traffic-management initiative revisions. Terminal Flight Data aggregates operator submissions. | FAA describes near-real-time distribution and real-time TFMS publication to its messaging service. Delivery latency, field sensitivity and downstream use are subscription-specific. SCDS uses Solace JMS, requires an annually renewed Service Access Agreement, and is intended for non-operational use. The FAA excludes NAS-impacting use and calls for notice of outages when applicable to downstream users. Check eligible products and filters separately from authorized operational NAS access. A filed route or estimated runway does not indicate a voice clearance. [SFDPS](https://www.faa.gov/air_traffic/technology/swim/sfdps) · [TFMData fields and XML](https://cdm.fly.faa.gov/faq) · [SWIM access and documentation](https://support.swim.faa.gov/hc/en-us) · [SCDS agreement](https://support.swim.faa.gov/hc/en-us/articles/24963265968148-Service-Access-Agreement) · [SCDS standards](https://www.faa.gov/air_traffic/technology/swim/governance/standards) · [FIXM schema](https://www.fixm.aero/) |
| **ASDE-X, ASSC, and FAA STDDS** | Airport surveillance can cover aircraft/vehicles at equipped airports. The Surface Movement Event Service (SMES) includes aircraft/vehicle position reports and derived movement events at select towers. Its catalog identifies schema NAS-JMSDD-4307-003. Distinguish position reports, derived events, and independently confirmed runway occupancy. | FAA lists PHX for ASDE-X, a useful reference at a larger airport. KDVT’s diagram documents SAID. Confirm any KDVT SMES coverage and subscription entitlement separately with FAA. No assumed public access, target accuracy, complete vehicle reporting, cadence, or unrestricted persistence. Airport ramp and obstacles need separately sourced information. [ASDE-X list](https://www.faa.gov/air_traffic/technology/asde-x) · [ASSC](https://www.faa.gov/air_traffic/technology/adsb/atc/assc) · [STDDS product families](https://www.faa.gov/air_traffic/technology/swim/stdds) · [FAA SMES catalog and schema ID](https://aa.data.faa.gov/data/service.jsf?uuid=4ad245f2-038f-47c0-be25-5e3b77ed748b) · [FAA data access portal](https://aa.data.faa.gov/) |
| **Airport/airline AODB, stand resource manager or ramp control; FAA TFDM** | Assignment, occupancy, compatibility, readiness, pushback and jurisdiction are separate fields. TFDM Terminal Flight Data documents Gate Assignment, AOBT (Actual Off-Block Time), ATOT (Actual Takeoff Time), ALDT (Actual Landing Time), AIBT (Actual In-Block Time) and estimated milestone fields. AIDX is an aviation XML exchange standard. | Request the airport/airline feed and permissions. Contracts, coverage, milestone completeness and latency vary. TFDM capabilities depend on the installed configuration and subscribed service. Published PHX passenger gate data are an example of assignment/status context, with automated reuse unconfirmed. For KDVT, simulate stands/ramp, including positive push or movement-area acceptance. [TFDM metering](https://www.faa.gov/air_traffic/technology/tfdm/sm) · [FAA industry data fields and XML examples](https://cdm.fly.faa.gov/faq/submitting-terminal-flight-data-tfms) · [FAA deployment configurations](https://www.faa.gov/air_traffic/technology/tfdm/configurations) · [IATA AIDX](https://www.iata.org/en/publications/info-data-exchange) · [PHX passenger flights](https://www.skyharbor.com/flights/) |
| **BTS historical on-time performance and FAA ASPM** | BTS monthly historical CSV fields include origin/destination, date, gate movements, wheels-off/on, taxi-in/out, delays and cancellations. FAA ASPM uses multiple data feeds and describes actual flight milestones and airport metrics. | Use for aggregate baselines at relevant airline airports. Check coverage and access at required granularity. Local timestamps can cross midnight. Historical movement times do not identify KDVT vehicle tracks, actual stand assignments or premises authority. [BTS fields](https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ) · [BTS downloads](https://www.transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=&gnoyr_VQ=FGJ) · [FAA OOOI definitions](https://www.aspm.faa.gov/aspmhelp/index/OOOI_Data.html) |
| **FAA type data and manufacturer specs; OpenAP / BADA** | Aircraft designator plus type/variant, length, wingspan, design group, wake scheme/category, published performance under reference conditions. OpenAP offers representative airborne kinematics, with type coverage to verify. | Pin FAA designator revision and current notices. Keep ICAO filed wake category, terminal separation category, and design group distinct. C172/Skyhawk figures are reference conditions. Real performance requires variant-specific POH/AFM, mass, wind, density altitude, contaminant and configuration. Use scenario envelopes for taxi speed, stopping and acceleration. The current OpenAP repository declares LGPL-3.0. Verify the license of the pinned package and any bundled datasets separately. BADA requires a EUROCONTROL license. [FAA type designators](https://www.faa.gov/documentLibrary/media/Order/FAA_Order_JO_7360.1K_Aircraft_Type_Designators.pdf) · [FAA characteristics database](https://www.faa.gov/airports/engineering/aircraft_char_database) · [Textron Skyhawk](https://cessna.txtav.com/en/piston/cessna-skyhawk) · [OpenAP](https://github.com/junzis/openap) · [BADA](https://www.eurocontrol.int/model/bada) |

Match stable feed IDs within time windows; callsigns can be reused and tail numbers can change. Store filed and revised intent, requests, predictions, controller ownership and actual clearance independently. In the simulation, only explicit and current handoff acceptance transfers ownership.

## Restrictions, airport status and weather

| Source | Schema, timing and rights | Demo implementation |
|---|---|---|
| **FAA NOTAM Search and NOTAM Management Service (NMS)** | Public human search, separate NMS distribution access request. Store notice ID/accountability, affected airport/facility/runway and operation, issue time, start/end, schedules, original text, geometry and links to its replacement/cancellation. Request current documentation and eligibility from FAA. A machine feed, public redistributable archive and latency SLA are unconfirmed. [NOTAM Search](https://notams.aim.faa.gov/notamSearch/) · [NMS API onboarding](https://nms.aim.faa.gov/) · [FAA NMS FAQ](https://www.faa.gov/about/initiatives/notam/faqs) | Replay a synthetic closure and cancellation; test a late older revision. Preserve ambiguous text for review and keep the affected resource unavailable until the uncertainty has been resolved. |
| **FAA runway condition / TALPA and airport operations** | Store runway direction, observation/issue time, condition codes by runway third where reported, contaminant coverage/depth and raw report. Local inspections, equipment outages, work areas and reopening authorization are separate events from airport operations. [FAA TALPA](https://www.faa.gov/about/initiatives/talpa) · [Movement Area NOTAM policy](https://www.faa.gov/air_traffic/publications/atpubs/notam_html/chap5_section_1.html) | Simulate inspection, closure and affirmative reopening. A METAR or an airport summary does not determine braking or reopening. |
| **FAA ATCSCC OIS, Advisories, EDCT lookup, NAS Status and TFMData Flow** | OIS and NAS Status publish aggregate context and advisory/impact data. NAS Status has a documented XML view, with the guide describing minute updates to the site. EDCT lookup provides flight-specific information where applicable. Store type, scope, program/flight ID, action and revision, time window, rate/spacing and reason. [OIS](https://www.fly.faa.gov/ois/menu_pub) · [Advisory search](https://www.fly.faa.gov/adv/advAdvisoryForm) · [EDCT lookup](https://www.fly.faa.gov/edct/) · [NAS Status guide](https://nasstatus.faa.gov/static/media/NASStatusUserGuide.cccc6d48.pdf) · [XML endpoint](https://nasstatus.faa.gov/api/airport-status-information) · [TFMData Flow](https://cdm.fly.faa.gov/faq) | Distinguish ground stops, traffic flow restrictions, departure releases, downstream acceptance and actual takeoff authorization. Simulate a release window/expiry and a handoff rejected by a downstream actor. Feed subscriptions and rules differ from public summaries. |
| **FAA TFRs** | Capture impacted geometry, altitude bounds/datum, schedules, exceptions and the controlling notice. [FAA TFRs](https://tfr.faa.gov/tfr2/list.html) · [FAA AIP restrictions and notices](https://www.faa.gov/air_traffic/publications/atpubs/aip_html/part2_enr_section_1.10.html) | Use an explicitly synthetic overlay or omit from the first scripted run. A TFR is a different object from a closed runway or flow program. |
| **NOAA/NWS Aviation Weather Center (AWC)** | Keyless HTTPS examples. Start with [METAR JSON for KDVT](https://aviationweather.gov/api/data/metar?ids=KDVT&format=json) and [TAF JSON for KDVT](https://aviationweather.gov/api/data/taf?ids=KDVT&format=json). Store station/product, raw report, observation/issue time, TAF validity and conditional forecast groups, wind/gust and variable directions, visibility, ceiling/clouds and pressure with units. Use SIGMET, G-AIRMET and PIREP endpoint-specific formats and applicability. Docs state 100 requests/min overall, most endpoints cap at 400 records, up to 30 days depending on product, no browser CORS and 204 for valid empty results. The AWC cache table lists a one-minute refresh for its METAR and TAF bulk files. Station reports have their own issuance cadence and ages. [AWC API docs/cache](https://aviationweather.gov/data/api/) · [KDVT weather dashboard](https://aviationweather.gov/impactboard/?id=KDVT) | Fetch/cap requests in the backend and set a descriptive User-Agent. Age reports using observation time, retain TAF as forecast, and keep an explicit stale/missing state. Compare weather and runway bearing in the same true/magnetic frame. Replay an offline synthetic shift in wind or visibility with a configured demo trigger. The API schema and one KDVT METAR/TAF response were checked on November 14, 2026. Availability and latency were not benchmarked. |
| **Archives and runway-local weather** | NOAA ISD and Iowa Environmental Mesonet ASOS for station archives. FAA RVR is a distinct instrumented-runway view subject to terms. FAA Windshear Detection and TDWR describe operational products and coverage. [NCEI ISD](https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database) · [IEM ASOS](https://mesonet.agron.iastate.edu/request/download.phtml?network=IA_ASOS) · [FAA RVR](https://rvr.data.faa.gov/) · [FAA windshear detection](https://www.faa.gov/air_traffic/weather/wsds) · [TDWR](https://www.faa.gov/air_traffic/weather/tdwr) | Airport-specific RVR, LLWAS/windshear coverage, operational feeds and latency require verification. Station visibility cannot fill RVR. Simulate runway-local inputs as distinct events. |

## Communications and rules

- **FAA rule provenance.** Start with JO 7110.65BB Change 3, effective July 9, 2026, plus active notices. Use §2-4-3 for correct acknowledgments, callsigns and runway hold-short readback; §3-7-2 for routing, hold and crossing; Chapter 3 §§9–10 for departure and arrival. Use AIM Chapter 4 for pilot-facing procedures, JO 7210.3 for facility documentation, and 14 CFR §91.123 for compliance with clearances and instructions. Pin versions/sections and re-read the cited paragraph before expanding the rulebook. [JO 7110.65 edition](https://www.faa.gov/air_traffic/publications/atpubs/ATC_html/index.html) · [Directive and notices](https://www.faa.gov/regulations_policies/orders_notices/index.cfm/go/document.current/documentNumber/7110.65%3B) · [Acknowledgments](https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap2_section_4.html) · [Taxi/crossing](https://www.faa.gov/air_traffic/publications/atpubs/ATC_html/chap3_section_7.html) · [Departures](https://www.faa.gov/air_traffic/publications/atpubs/ATC_html/chap3_section_9.html) · [Arrivals](https://www.faa.gov/air_traffic/publications/atpubs/ATC_html/chap3_section_10.html) · [AIM airport operations](https://www.faa.gov/air_traffic/publications/aim_html/chap4_section_3.html) · [Facility agreements](https://www.faa.gov/air_traffic/publications/atpubs/foa_html/chap4_section_3.html) · [eCFR §91.123](https://www.ecfr.gov/current/title-14/section-91.123)
- **Simulated local agreement.** Define which actor owns the movement area, ramp, arrival handoff, departure release and downstream handoff. Site agreements, operating and routing restrictions, exceptions and wake/separation context need local confirmation. Use a one-owner reservation, a separate crossing clearance, an explicit hold point and an observed runway clear. Treat this as the agent's rulebook for the demo.
- **Voice loop.** Record clearance version, timestamped audio, speaker/callsign/channel, transcript, parsed critical fields, confidence and match result. A partial, contradictory or missing required readback makes the agent ask for a correction and hold the movement. The agent keeps the runway protected while delivery or actual position is uncertain. Record acknowledgment independently of observed motion and have the agent re-read current state before speaking a queued instruction. Log conflict, wrong-side runway readback, a dropped token and a blocked channel as separate tests. [FAA Pilot/Controller Glossary](https://www.faa.gov/air_traffic/publications/atpubs/pcg_html/)
- **ASR/TTS and practice audio.** Start with team-generated fixture audio and a simulated microphone/speaker pipeline. Optional offline ASR/TTS candidates are MIT-licensed whisper.cpp and GPL-licensed Piper, with voice-model terms to check separately. TU Graz ATCOSIM is recorded simulation speech and ATCO2 describes recorded operational corpora with separate conditions. Verify each license, retention and public playback before use. Use critical-field error rate and wrong-readback acceptance alongside word error rate. [whisper.cpp](https://github.com/ggml-org/whisper.cpp) · [Piper](https://github.com/OHF-Voice/piper1-gpl) · [TU Graz ATCOSIM](https://www.spsc.tugraz.at/databases-and-tools/atcosim-air-traffic-control-simulation-speech-corpus.html) · [ATCO2 corpus](https://www.atco2.org/data)

## Prototype contracts

Store static GeoJSON and versioned scenario JSON, plus append-only JSONL events. The table specifies internal field names; source schemas and source units remain in adapter documentation and raw payloads. WGS84 geometry uses [longitude, latitude]; transform to local metric coordinates for distance and intersection calculations. UTC ISO 8601 for observed/issued/received times; use explicit simulated milliseconds for deterministic replay. Preserve nullable values and a quality reason. Never substitute a contact timestamp for the observed position timestamp or interpret an absent track as a confirmed clear resource.

| Entity | Minimum fields |
|---|---|
| Event envelope | schema_version, run_id, mode, event_id, airport_icao, kind, entity_id, source_id, source_record_id, source_version, observed_at, issued_at, received_at, valid_from, valid_to, sim_time_ms, source_sequence, quality, quality_reason, raw_ref, data. |
| Airport and procedure | feature_id, effective_cycle, geometry, CRS, provenance. runway_end_id, shared pavement_id, threshold, elevation and datum. Taxi edge from_node/to_node, movement authority, width_m, hold_id, crossing_pavement_id. Procedure ID, transition, leg index, terminator, fix and constraints. |
| Track | track_id, flight_id, target_kind, callsign, icao24, lon/lat, position_time, last_contact_time, altitude value/unit/reference, groundspeed_mps, true_track_deg, heading value/reference, vertical_rate_mps, on_ground, uncertainty and source. |
| Intent and handoff | flight_id, GUFI, origin/destination, filed_route, requested operation, assigned runway, clearance_id and limit, plan revision, controller_id, release window. Coordination ID, authority scope, from/to, expiry, explicit state and accept time. |
| Aircraft | type_icao, variant, length_m, wingspan_m, performance source/revision, wake category/scheme, mass_kg when known, profile limits and taxi constraints. |
| Gate, occupancy, restrictions | Stand ID, responsible ramp, compatibility, assigned flight, occupant, ready and permission. Resource ID, reservation time window, observed occupancy, evidence. Restriction ID/revision, geometry/resource IDs, schedule, affected operations, cancellation and authority. |
| Weather | product, station, raw report, observation/issue/valid times, forecast group, wind true degrees/kt/gust, variable direction, visibility and units, ceiling ft AGL, pressure units, local runway-end ID and RVR where available. |
| Clearance and decision | clearance_id, version, idempotency_key, issuer, flight_id, action, route edges, hold limit, crossing pavement, expires_at, status, supersedes_id, required readback fields. Decision ID, snapshot ID, action, rule/evidence IDs, constraints considered and next review trigger. |
| Communication and rule | message_id, clearance and version, role, channel, audio reference, transcript, parsed values, confidence and result. rule_id, revision, source URL/section, applicability, constraint, input requirements, unknown behavior, effective interval, reviewer, scenario_only. |

For the closure test, this synthetic JSON fixture records an effective interval on the simulation clock. Both runway ends share the affected pavement ID.

```json
{
  "schema_version": 1,
  "run_id": "kdvt-demo-01",
  "mode": "simulation",
  "event_id": "event-0040",
  "airport_icao": "KDVT",
  "kind": "restriction.updated",
  "entity_id": "rw-07R-25L",
  "source_id": "sim-airport-operations",
  "source_record_id": "closure-1",
  "source_version": "1",
  "observed_at": null,
  "issued_at": null,
  "received_at": null,
  "valid_from": null,
  "valid_to": null,
  "sim_time_ms": 40000,
  "source_sequence": 40,
  "quality": "valid",
  "quality_reason": "simulation_clock_only",
  "raw_ref": null,
  "data": {
    "restriction_id": "closure-1",
    "revision": 1,
    "operation_status": "closed",
    "resource_ids": ["rw-07R-25L"],
    "affected_operations": ["land", "takeoff", "cross"],
    "effective_sim_ms": 45000,
    "expires_sim_ms": 90000,
    "authority": "sim-airport-operations"
  }
}
```

Normalize out-of-order updates by source ID, record ID and revision; avoid treating late events as fresh. Keep authorization and occupancy separate. Advance a clearance through issued, acknowledged and observed complete, with amended, canceled, superseded, expired and unresolved outcomes. Restore reservations after restart and reconcile issued but unresolved commands before expiry or any replacement. Have the agent re-read current state before each queued instruction goes out as a command or audio. The agent decides and issues clearances itself through a typed clearance manager. Instrument source-to-receipt, receipt-to-decision and voice loop at p50/p95/p99 separately, plus missing fields, source outages and stale inputs.

## Simulators and implementation order

| Option | Fit |
|---|---|
| **Deterministic surface simulator, first** | Backend-owned tick, directed graph, occupancy/reservations, synthetic actors, typed commands and an event log. Use this for gates, crossing, weather, audio and headless operation. |
| **BlueSky, second** | MIT, time-tagged scenarios and programmable, headless airborne simulation. Test CRE/HDG/ALT/SPD and units on a pinned build. Isolate or disable built-in conflict resolution so it cannot silently take over from our agent. Supply surface and ramp logic separately. [BlueSky repo](https://github.com/TUDelft-CNS-ATM/bluesky) · [Command schema](https://github.com/TUDelft-CNS-ATM/bluesky/wiki/Command-Reference) |
| **BluebirdATC, research alternative** | AGPL-3.0 en-route research environment with REST, HMI and Gymnasium interfaces. Confirm deployment obligations and startup on the pinned version. Dedicated stand/tower mechanics remain separate. [Bluebird repository](https://github.com/project-bluebird/BluebirdATC) · [API quickstart](https://docs.projectbluebird.ai/bluebird-api/getting-started/) |
| **NASA Ames MACS, reference** | Research precedent for networked and human-in-the-loop evaluation. Self-service access and integration time are unverified for hackathon use. [NASA Ames simulation labs](https://www.nasa.gov/ames/air-traffic-control-atc-simulation-laboratories/) |

1. **P0, offline geometry and end-to-end traffic.** Build one reviewed graph and synthetic stand relationships. At t=0 arrival SUN101 is handed to the simulated tower, SUN202 is waiting at stand S2, and OPS1 requests the B5 crossing at 7R/25L. With the arrival holding the runway reservation, the agent instructs the vehicle to hold. After landing and observed clear, the agent clears the crossing and observes vehicle exit; taxi the arrival to S1 after ramp acceptance. Give SUN202 taxi permission after modeled readiness, and departure clearance after a current synthetic release, once the agent sees the pavement clear.
2. **P0, judge-facing explanation and disruptions.** Show the crossing request the agent declined, the protected resource, current owner, weather/restriction freshness and exact event/rule IDs. Inject a wrong runway-side readback and missing runway-clear evidence; expect the agent to withhold movement and keep any issued clearance pending correction. Inject a closure or a weather revision prior to a proposed takeoff; expect the agent to defer the movement and replan once conditions recover. Inject a missing handoff acknowledgment and restart the backend; expect held ownership and no repeated command. Reconnect the dashboard and show it caught up with the running backend.
3. **P1, two-way voice and weather.** Rehearse one simulated audio request and readback, with local fixtures as fallback. Replay a timed weather change and show which evidence invalidated the plan. This targets the requested voice/weather loop while keeping the judging run independent of external services.
4. **P2, authorized observation and replay.** Choose one traffic provider. Confirm coverage, allowed public display, retention/cache/archive rights, cost and quota, and measure PHX-area and KDVT feed position ages/gaps during a busy window. Record API delivery and measurement age separately. Benchmark surface coverage independently from airborne coverage. Use a dated permitted snapshot or synthetic run if live data are unavailable.
5. **Pass criteria.** Identical seed and asset revisions generate the same decisions, excluding real receipt times. The run has zero overlapping runway reservations, zero simultaneous conflicting pavement occupancy in simulator truth, zero unauthorized hold-line crossings, zero wrong required readbacks accepted, zero commands from live/replay mode and zero repeated issue after restart. Report delay per actor, recovery time, stale events, how often the agent declined or replanned, and voice processing latency. Measure against simulation truth.

**Open dependencies.** Confirm physical movement limits and the complete airport/ramp graph with the airport. Confirm local rules, wake/separation, runway suitability and handling of degraded state against the FAA text. Confirm each relevant FAA, airport, or vendor entitlement, fee, coverage, data use, outage behavior and end-to-end delay with the provider. Measure voice quality on the hardware and noise conditions used for the demo. All outside parties are simulated in the proposed fixture.


## KDVT build checklist

### B5/C5 crossing geometry

On FAA diagram 2611, B5 connects taxiway B on the north side of 07R/25L and C5 connects to taxiway C on the south side. Stop the first scenario at those parallel taxiways. The nearby A5 connector crosses 07L/25R, so a route continuing across that pavement would require its own authorization. Use a shared 07R/25L resource and separate north and south holding boundaries. Release the crossing reservation only when the entire actor footprint has passed the far boundary and the evidence is current. Also test an actor holding short while its tail still occupies the parallel taxiway. The FAA KDVT briefing describes this space constraint. [Cycle 2611 diagram](https://aeronav.faa.gov/d-tpp/2611/06646AD.PDF) · [FAA KDVT briefing](https://www.faa.gov/flight_deck/dvt) · [FAA AIM §2-3-5](https://www.faa.gov/AIR_TRAFFIC/PUBLICATIONS/atpubs/aim_html/chap2_section_3.html)

Use diagram-derived connectivity and fictional geometry in the offline fixture. Exact hold-line coordinates were unavailable. For a surveyed import, ask airport operations for the Airports GIS `TaxiwayHoldingPosition` line features at B5 and C5, adjoining taxiway centerlines, runway pavement and movement-area boundaries, with survey date, datum/CRS, revision and permitted use. FAA AC 150/5300-18B Change 1 is the active survey specification. The base specification’s §5.4.14 identifies the holding position as a line feature and lists a restricted information-assurance level. [FAA advisory circular status](https://www.faa.gov/airports/resources/advisory_circulars/index.cfm/go/document.current/documentnumber/150_530) · [AC 150/5300-18B §5.4.14](https://www.faa.gov/documentLibrary/media/advisory_circular/150_5300_18b.pdf) · [Airport operator](https://www.deervalleyairport.com/about-dvt-airport/aviation-department/)

### 07R arrival and missed approach

The inspected FAA cycle 2610 plate, valid October 1–29, 2026, gives the RNAV (GPS) RWY 7R final approach course as 074°, with CEGAP as the final approach fix and RW07R at the approach threshold. The published missed procedure climbs to 5,000 ft MSL via VIFPE, then follows a 069° track to the hold at AVENT and continues climbing in the hold if needed. Record the plate and cycle as a historical seed. The November 14 cycle 2611 procedure could not be retrieved for comparison. [FAA cycle 2610 plate, amendment 1B](https://aeronav.faa.gov/d-tpp/2610/06646R7R.PDF)

For the simulation, seed SUN101 near CEGAP. Accept simulated approach-to-tower ownership with an acknowledgment. If runway protection fails at the scripted decision point, branch to the simulated missed approach and keep the affected runway protected until modeled conflict checks clear it. Present VIFPE and AVENT as historical route labels. Transfer ownership to the simulated approach controller on acknowledgment and end the scenario there. Hold-pattern flight, minima and procedure timing remain outside this fixture.

### Measured traffic and surface-data access

The airport operator reports **490,702 aircraft operations in calendar 2025**, including **394,950 general aviation operations**. For July 2025, its categories sum to **40,379** operations. The annual PDF’s monthly Total row places the 2024 and 2025 monthly values in reverse order relative to its header. For July 2025 the category sum and change in 2025 YTD both give 40,379. Use the operator series to scale test demand or query DVT under OPSNET Airport Operations by date, with local/itinerant and IFR/VFR selections recorded. The counts lack runway assignment, timestamps per operation and crossing or vehicle movements. [Operator annual PDF](https://www.deervalleyairport.com/media/nyvpt4qt/annual-2025-stats-dvt.pdf) · [FAA OPSNET report](https://www.aspm.faa.gov/opsnet/sys/Airport.asp)

For an authorized surface import, use FAA SWIFT onboarding and ask for KDVT coverage under STDDS Surface Movement Event Service (SMES), schema NAS-JMSDD-4307-003. Request the eligible site list, permitted retention/display, surface target classes and a timestamped sample to check gaps and age. The chart’s SAID note establishes display use at KDVT. SMES inclusion, public availability and latency were not established. [SWIFT onboarding and subscriptions](https://support.swim.faa.gov/hc/en-us/categories/360002173272-Guide) · [FAA SMES catalog](https://aa.data.faa.gov/data/service.jsf?uuid=4ad245f2-038f-47c0-be25-5e3b77ed748b)

### Readback and movement tests

FAA JO 7110.65 requires runway hold-short readback and provides distinct crossing phraseology. For the demo, match the callsign, action and runway designator against the issued hold instruction. Use the controller’s spoken runway end for phraseology and normalize it to a physical pavement ID only after checking the spoken suffix. In this test, `7L` and `7R` resolve to different pavement resources. [FAA §2-4-3](https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap2_section_4.html) · [Taxi and ground movement](https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap3_section_7.html)

| Simulated controller instruction | Response | Expected result |
|---|---|---|
| OPS1, hold short of Runway Seven Right | Holding short Seven Right, OPS1 | Confirm hold instruction. Crossing stays blocked. |
| Same hold instruction | Holding short Seven Left, OPS1 | Request a corrected readback and keep the hold in effect. |
| Same hold instruction | Roger, OPS1 | Request runway hold-short readback. |
| OPS1, cross Runway Seven Right at Bravo Five | Crossing Seven Right at Bravo Five, OPS1 | The agent accepts the readback because it matches the crossing clearance it issued. |

Use team-recorded audio for these paired turns. ATCO2’s one-hour English ASR subset offers audio and separate verified XML tags for callsign, command and values under research terms. It can provide a separate recognition evaluation with critical-field accuracy. Use the scripted KDVT scenarios to measure incorrect clearances issued by the agent, and use team audio for public playback. ATCO2 files were not downloaded in this pass. [ATCO2 research subset, audio and tags](https://www.atco2.org/data)

## Concrete airport and aircraft records

**Retrieved KDVT facts.** The operator lists north 07L/25R as 4,500 × 75 ft, south 07R/25L as a rounded 8,200 × 100 ft, field elevation 1,478 ft MSL and approximate coordinates 33°41.3′N, 112°04.9′W. These convert to 1,371.6 × 22.86 m for the north runway, 450.4944 m elevation and approximately 33.688333, −112.081667 for map centering. Conversion adds no survey precision. Display metadata include north tower 120.2, south tower 118.4, ground 121.8, ATIS 126.5 and clearance delivery 123.9 MHz. [Airport operator airfield information](https://www.deervalleyairport.com/pilots/airfield-information/)

**Source reconciliation matters.** Visual inspection of the FAA diagram effective October 29–November 26, 2026 confirms south runway **8,196 × 100 ft**, or 2,498.1408 × 30.48 m, and north runway 4,500 × 75 ft. It marks B5 as HS1, the diagram’s sole marked hot spot. B9 appears as a connector associated with the north runway, whereas the FAA web briefing discusses B5 and B9 as crossing hot spots. Use the effective diagram for the demonstration subset and reconcile the briefing before expanding it. Do not average the operator's rounded runway length with the chart value. Use NASR for surveyed runway-end coordinates and obtain separate survey geometry for hold lines. [Inspected current diagram](https://aeronav.faa.gov/d-tpp/2611/06646AD.PDF) · [FAA briefing](https://www.faa.gov/flight_deck/dvt) · [FAA pilot handbook](https://www.faa.gov/flight_deck/pilot_info/dvt.pdf)

The diagram identifies the Surface Awareness Initiative Display (SAID) as in use and calls for altitude reporting and ADS-B when equipped. FAA Order JO 7110.665A describes the display as situational awareness for ADS-B-equipped, transmitting aircraft and vehicles and prohibits its use for separation. Access to KDVT movement records, coverage and delivery age remain unverified. [FAA airport diagram](https://aeronav.faa.gov/d-tpp/2611/06646AD.PDF) · [FAA SAID order](https://www.faa.gov/documentLibrary/media/Order/JO_7110.665A_Surface_Awareness_Initiative_Display_dtd_11-5-25.pdf)

**Aircraft footprint reference.** The Skyhawk product card gives length 8.28 m and span 11.00 m. The website rounds length to 8.3 m and lists maximum takeoff mass 1,157 kg, takeoff distance 497 m versus ground roll 293 m, landing distance 407 m versus ground roll 175 m, climb 730 ft/min and maximum cruise 124 ktas. The card's reference conditions are standard, zero wind, dry level hard runway. Use dimensions for the first footprint, with explicitly synthetic taxi/braking envelopes. Keep ground roll separate from obstacle-clearance distance and never use brochure values as a Phoenix hot-day dispatch calculation. [Manufacturer specifications](https://cessna.txtav.com/en/piston/cessna-skyhawk) · [Product card](https://cessna.txtav.com/-/media/cessna/files/product-cards/piston/skyhawk_product_card.ashx)


### Actual FAA NASR seed rows

The October 29, 2026 APT ZIP was downloaded and KDVT rows inspected. `APT_BASE.csv` identifies `SITE_NO=00751.4`, `SITE_TYPE_CODE=A`, `ARPT_ID=DVT`, `ICAO_ID=KDVT`, reference coordinates **33.68830555, −112.08255555** and elevation **1,478.1 ft**. Preserve these source values rather than the operator's approximate center. Join base, runway and end tables using the release's site/type keys plus `RWY_ID` and `RWY_END_ID` at the appropriate levels. [Retrieved official APT ZIP](https://nfdc.faa.gov/webContent/28DaySub/extra/29_Oct_2026_APT_CSV.zip)

| End | Pavement end latitude, longitude | Displaced threshold latitude, longitude | Displacement ft | True alignment |
|---|---|---|---|---|
| 07L | 33.68916, −112.08945172 | Blank in source | Blank | 86° |
| 25R | 33.69002022, −112.07469558 | Blank in source | Blank | 266° |
| 07R | 33.68683422, −112.09626891 | 33.687006, −112.09332422 | 898 | 86° |
| 25L | 33.68840083, −112.06939336 | 33.68822619, −112.07239541 | 916 | 266° |

For both southern ends, `TKOF_RUN_AVBL`, `TKOF_DIST_AVBL` and `ACLT_STOP_DIST_AVBL` are 8,196 ft. Published `LNDG_DIST_AVBL` is **7,299 ft for 07R** and **7,281 ft for 25L**. Preserve these published distances. Subtracting the rounded displacement from pavement length differs by one foot and is not a replacement for the declared value. Northern declared-distance fields and runway visual-range equipment codes are blank. Keep blanks unresolved unless another applicable record supplies the answer.

The source distinguishes pavement-end survey dates from FAA-estimated displaced-threshold positions. A current effective cycle is not a new survey. The chart prints 074°/254°, while NASR explicitly labels its 86°/266° values `TRUE_ALIGNMENT`, a concrete reason to store bearing reference frames.

Join remarks too. `APT_RMK.csv` remark A110-17 restricts D1/D13 run-up pads to wingspans no greater than 49 ft, or 14.9352 m. This could become an optional compatibility test after checking current notices and local status. Although base `TPA` is blank, remark E147 gives piston patterns at 2,500 ft MSL and turbine patterns at 3,000 ft MSL. Those remarks supply contextual information that a base-table-only import misses. They do not require expanding the proposed synthetic arrival boundary.

Acquisition fingerprint: ZIP 8,502,128 bytes, SHA-256 `6e1e422f483d44cc8a38a1c97cf98e97588a6fc3fa857b39b5bdcbcfe8331154`. The quoted rows are retrieved reference data. The B5 graph, fictional stands and movement events remain proposed fixtures.

## Download and adapter guide

### Airport catalog fallback

OurAirports provides daily downloads through its creator's repository, with a public-domain dedication/Unlicense. It is useful for search, reference points, runway outlines and frequency labels while FAA data remains the reference for the reviewed surface model. Daily export generation does not establish recent verification of every record. [Official export repository](https://github.com/davidmegginson/ourairports-data) · [License](https://github.com/davidmegginson/ourairports-data/blob/main/LICENSE)

| Concrete download | Documented fields | Proposed mapping |
|---|---|---|
| [airports.csv](https://davidmegginson.github.io/ourairports-data/airports.csv) | `id`, `ident`, `type`, `name`, `latitude_deg`, `longitude_deg`, `elevation_ft`, `iso_country`, `iso_region`, `gps_code`, `iata_code`, `local_code` | Preserve provider ID and identifier namespace. Coordinates are an airport reference, not a threshold. |
| [runways.csv](https://davidmegginson.github.io/ourairports-data/runways.csv) | `id`, `airport_ref`, `airport_ident`, `length_ft`, `width_ft`, `surface`, `lighted`, `closed`, `le_ident`, `he_ident`, end coordinates and displaced-threshold fields | One pavement with two directional ends. Preserve true-heading fields `le_heading_degT` and `he_heading_degT`. |
| [airport-frequencies.csv](https://davidmegginson.github.io/ourairports-data/airport-frequencies.csv) | `id`, `airport_ref`, `airport_ident`, `type`, `description`, `frequency_mhz` | Simulated channel labels. Multiple functions may share the same frequency. |

Join `airport_ident` to `airports.ident`, or numeric `airport_ref` to `airports.id` within one snapshot. `ident` is not universally an ICAO identifier. Low/high-numbered runway ends use `le_*`/`he_*`. Frequency `type` is not a controlled vocabulary. Preserve blanks and leading zeroes in `07R`. [Publisher's data dictionary](https://ourairports.com/help/data-dictionary.html) · [Inspected runway header](https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/runways.csv)

Proposed import checks: unique provider IDs, positive dimensions, valid coordinate ranges and matching airport foreign keys. Store snapshot time with catalog `closed` status. It cannot replace current closures. Missing endpoint coordinates can leave a row eligible for search but ineligible for geometry construction. None of these CSVs supplies validated holding positions or the complete taxi graph.

### Weather schema and captured example

The official OpenAPI v4.0 schema was retrieved during this expansion. Use its endpoint-specific contracts rather than assuming XML, CSV and JSON field names are interchangeable. [Authoritative OpenAPI YAML](https://aviationweather.gov/data/schema/openapi.yaml)

| AWC JSON fields | Documented units/type | Adapter decision |
|---|---|---|
| `icaoId`, `obsTime`, `reportTime`, `receiptTime` | Station string, Unix observation seconds, report and upstream receipt strings | Preserve all clocks. Add local fetch time separately. |
| `wdir`, `wspd`, `wgst` | Direction number or `VRB`, speed/gust knots | Keep variable wind distinct from zero degrees. Convert knots only at an explicit boundary. |
| `visib`, `altim`, `temp`, `dewp` | Statute miles with possible `10+`, hectopascals, Celsius | Keep visibility qualifier. JSON altimeter pressure is not inches Hg. |
| TAF `issueTime`, `validTimeFrom`, `validTimeTo`, `fcsts` | Issue string, Unix validity seconds, forecast-group array | Preserve `timeFrom`, `timeTo`, `timeBec`, `fcstChange`, `probability` and `notDecoded` within each group. |

One successful November 14 capture returned KDVT `obsTime=1794689580`, meaning **20:53 UTC**, `wdir=260`, `wspd=7`, `visib="10+"`, `altim=1013.6` and clouds FEW at 3,100 ft and BKN at 6,000 ft. The raw report contains `A2993`, illustrating the raw-text versus JSON pressure-unit difference. Its station coordinates are 33.6902, −112.0662 and elevation 454 m. Do not overwrite the airport reference point/elevation with weather-site metadata. The fetched TAF contained a later `FM` group with `wdir="VRB"`, demonstrating why direction cannot be an integer-only field. These are captured observations/forecasts, not synthetic fixtures or a latency benchmark. [METAR request](https://aviationweather.gov/api/data/metar?ids=KDVT&format=json) · [TAF request](https://aviationweather.gov/api/data/taf?ids=KDVT&format=json)

A derived illustration combines the captured 260° wind at 7 kt with NASR 25L true alignment 266°: headwind is approximately 6.96 kt and absolute crosswind 0.73 kt. The reciprocal 07R has approximately 6.96 kt tailwind. These are component calculations, not runway authorization. Occupancy, closures, performance limits and coordination still apply.

For bulk initialization, use AWC's compressed [METAR CSV](https://aviationweather.gov/data/cache/metars.cache.csv.gz), [TAF XML](https://aviationweather.gov/data/cache/tafs.cache.xml.gz) and [station JSON catalog](https://aviationweather.gov/data/cache/stations.cache.json.gz). File refresh time does not imply a fresh observation at each station. Keep a valid empty response distinct from a transport failure. [API/cache documentation](https://aviationweather.gov/data/api/)

Wind reports describe the direction **from** which wind blows. Ground track describes motion **toward** a direction. Before calculating crosswind, align reference frames and retain variable-direction and gust qualifiers. A ceiling is not simply the first cloud layer. Preserve the raw observation and derive only fields whose semantics are understood. [AWC weather guide](https://aviationweather.gov/help/data/)

The archived IEM response for the request below contains **315 distinct KDVT timestamps**, from 00:00 through 23:55 UTC on July 1, 2025. The local CSV has SHA-256 `7e702da2eb38c25d49f71013552092dbfa866b2ea692ba560cc4ca4a1ca01e87`. This is a dated station-observation fixture. [IEM station archive](https://mesonet.agron.iastate.edu/request/download.phtml?network=AZ_ASOS)

```text
GET https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?network=AZ_ASOS&station=DVT&data=metar&data=sknt&data=gust&data=vsby&data=alti&sts=2025-07-01T00:00:00Z&ets=2025-07-02T00:00:00Z&tz=UTC&format=onlycomma&missing=null
```

| UTC on July 1, 2025 | Wind direction from report | Sustained (kt) | Gust (kt) | Visibility (SM) | Altimeter (inHg) |
|---|---|---:|---:|---:|---:|
| 00:00 | 310° true | 11 | null | 10 | 29.66 |
| 00:10 | 290° true | 10 | 15 | 10 | 29.66 |
| 08:25 | null | null | 24 | 10 | 29.77 |

Use the early rows to test replay order. At 08:25 the raw wind group is `/////G24KT`. Emit the gust and keep direction and sustained speed unknown. Any derived component that needs direction also remains unknown. Preserve the raw report and its remarks, including the `MADISHF` flag on 288 rows. The archive column `alti` is in inches of mercury, while AWC METAR JSON `altim` is in hPa. [IEM fields and units](https://mesonet.agron.iastate.edu/request/download.phtml?network=AZ_ASOS) · [AWC schema](https://aviationweather.gov/data/schema/openapi.yaml)

IEM supports `station`, `network`, selected `data` columns, ISO `sts`/`ets`, `tz` and a configurable missing marker. Optional columns include `drct`, `tmpf`, `dwpf`, `skyc1`–`skyc4` and `skyl1`–`skyl4`. Its service documents a one-second per-IP throttle, 429/503 responses and a 1,000 station-year aggregate cap. [IEM request contract](https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?help)

IEM describes the archive as an as-is collection with little quality control. Date ranges include the start and exclude the end. Keep special reports and corrections when selecting a transition, and preserve gaps rather than interpolating unseen hazardous weather. [Archive and download portal](https://mesonet.agron.iastate.edu/request/download.phtml?network=AZ_ASOS)

### Historical tracks and lawful replay

OpenSky publishes Monday state-vector archives for 2017–2022 at 10-second intervals, divided into hourly CSV, Avro and JSON files. Its COVID-era flight dataset instead contains derived flight records from 2019 through December 2022. The former can supply sampled trajectories where reception exists, while the latter supports traffic-demand context. Neither establishes taxi clearances or ground-vehicle coverage. [Publisher's scientific datasets](https://opensky-network.org/data/scientific)

The flight-dataset paper documents `callsign`, `number`, `icao24`, `registration`, `typecode`, `origin`, `destination`, `firstseen`, `lastseen`, `day` and first/last coordinates and altitudes. Airport assignments are inferred and may be missing. The authors identify a CC-BY release at [DOI 10.5281/zenodo.3931948](https://doi.org/10.5281/zenodo.3931948). The landing page could not be retrieved here, so verify the exact downloadable version and license before redistribution. [Authors' schema and methodology](https://essd.copernicus.org/articles/13/357/2021/)

For each offline acquisition, record title/version, publisher, download URL, license, attribution, checksum, observation window and retention rights. Keep raw recordings outside a public repository until redistribution is established. If only aggregate demand is usable, generate explicitly synthetic trajectories from it. Do not label invented taxi paths as historical reconstruction.

### Positional track mapping

OpenSky REST state vectors are arrays. The adapter should use the documented indexes and tolerate null values. [REST schema](https://openskynetwork.github.io/opensky-api/rest.html)

| Array indexes | Proposed internal fields | Handling |
|---|---|---|
| `[0]`, `[1]` | `provider_aircraft_id`, `callsign` | Namespace transponder ID and trim callsign padding. |
| `[3]`, `[4]` | `position_time_s`, `contact_time_s` | Preserve both. A recent contact does not refresh position time. |
| `[5]`, `[6]` | `longitude_deg`, `latitude_deg` | Preserve longitude-first ordering in GeoJSON. |
| `[7]`, `[13]` | `baro_altitude_m`, `geometric_altitude_m` | Distinct altitude types, not automatically height above pavement. |
| `[9]`, `[10]`, `[11]` | `ground_speed_mps`, `true_track_deg`, `vertical_rate_mps` | SI values except degrees. Track is not heading. |
| `[8]` | `surface_report` | Observation classification, not movement authority. |

A proposed Phoenix box is 33.3–34.0° latitude and −112.4–−111.7° longitude. This is a project selection, not an airspace boundary. The following request is documentation-derived and **unexecuted**:

```text
GET https://opensky-network.org/api/states/all?lamin=33.3&lomin=-112.4&lamax=34.0&lomax=-111.7
```

At an assumed synthetic taxi speed of 7.5 kt, an actor travels approximately 38.58 m in ten seconds, before accounting for position uncertainty. This illustrative calculation explains why snapshot resolution alone cannot establish safe proximity to a holding line. It is not a measured OpenSky error bound.

### Simulator and dataset licenses

BlueSky supports timestamped `.SCN` commands and airborne performance, flight-management and conflict-resolution functionality under an MIT project license. Pin the release, scenario and configuration. Isolate or record built-in resolution so it cannot silently change the planner comparison. [BlueSky repository](https://github.com/TUDelft-CNS-ATM/bluesky)

Inspect headless initialization on the pinned version. Its source separates server and simulation initialization and conditionally creates traffic objects. A proposed smoke test loads one actor, advances time, issues a command, closes the visualization and confirms time continues advancing. This test was not run during research. [Initialization source](https://github.com/TUDelft-CNS-ATM/bluesky/blob/master/bluesky/__init__.py)

OpenAP identifies separate origins for aircraft literature, engine emissions, derived drag/fuel models and X-Plane navigation data. Inventory code license and upstream data rights separately when bundling assets. No simulation package was installed during this expansion. [OpenAP data provenance and modules](https://github.com/junzis/openap)

## Incident data and evaluation fixtures

NASA ASRS provides searchable narratives with CSV, XLS and DOC export, one incident per row and up to 10,000 records per download. Its reports are voluntary, sanitized and uncorroborated. Use them to discover failure patterns, not to estimate incident prevalence or train labels of factual blame. [ASRS database](https://asrs.arc.nasa.gov/search/database.html)

The runway-incursion report set is a selected **50-report snapshot**, update 39 dated July 9, 2024. Useful references include ACN 2089579, a tire failure delaying runway exit, ACN 2071061, a holding-line depiction problem, ACN 2058147, an aircraft footprint exceeding a holding area, and ACN 2032094, a wrong-frequency problem. The mappings below are our proposed tests, not exact replays or independently established ground truth. [ASRS runway report set](https://asrs.arc.nasa.gov/docs/rpsts/rwy_incur.pdf)

| Narrative seed | Proposed synthetic mutation | Evidence to assert |
|---|---|---|
| Delayed runway exit | Aircraft stops after landing while a crossing request is pending | Runway remains reserved until positive modeled clearance, without a timeout falsely declaring vacancy. |
| Holding-line depiction | Rendered hold line disagrees with graph boundary | The inconsistency is flagged before the run; the agent does not route through unresolved geometry. |
| Footprint exceeds holding area | Center point is legal but tail blocks a parallel taxiway | Swept-footprint checks reject the conflicting taxi route. |
| Wrong frequency | Correct words arrive on an unrelated simulated channel | Readback remains unresolved until actor, instruction and channel association agree. |

NTSB CAROL supports CSV result summaries and JSON downloads of full query results. The bulk release notes distinguish `avall` for 2008 onward, `pre2008` for 1982–2007 and older archives, with differing update packages. They also describe `cm_inPC` replacing the legacy cause/factor representation. Pin the database release and schema together before joining aircraft, event and findings tables. [CAROL help](https://www.ntsb.gov/PAGES/carol.aspx) · [Bulk files and schemas](https://data.ntsb.gov/avdata/FileDirectory) · [Release notes](https://data.ntsb.gov/avdata/FileDirectory/DownloadFile?fileID=C%3A%5Cavdata%5CMDB_Release_Notes.pdf)

Two investigated cases sharpen the test design. The January 13, 2023 JFK event involved crossing runway 4L after an instruction involving runway 31L, with a departure on 4L and an ASDE-X alert/cancellation. Derive a test in which the agent detects the actual route deviation from observed positions, not the planned path, and holds the pending departure. [NTSB JFK DCA23LA125](https://www.ntsb.gov/investigations/Pages/DCA23LA125.aspx)

The February 4, 2023 Austin event involved fog, an assumption about departure progress and a departure delayed by an engine run-up. Derive a visibility-degraded test where the agent's plan assumes motion but observations still show an unresolved runway occupant. Evaluate its response to the evidence, not the prediction. The associated docket includes recorder CSV and weather material for deeper study. [NTSB Austin DCA23FA149](https://www.ntsb.gov/investigations/Pages/DCA23FA149.aspx) · [Austin evidence docket](https://data.ntsb.gov/Docket/TOCPrintable?data=106680)

FAA's Runway Incursion Mitigation program connects historical incursion patterns with airport geometry, making it useful for selecting graph stress points. Its surface-safety portfolio describes SAI's use of ADS-B-equipped aircraft and vehicles. Add an unreported vehicle that exists in simulation truth but not the observation feed, then score detection and conservative behavior separately. [FAA RIM](https://www.faa.gov/airports/engineering/incursions_excursions/rim) · [FAA surface-safety portfolio](https://www.faa.gov/surface-safety-portfolio)

Each test record should hold its source reference, synthetic changes, seed, input timing, expected invariant and actual result. Reserve separate cases for development and evaluation. A successful replay of a narrative-inspired test demonstrates only that particular modeled behavior. Report failure counts with test counts and scenario coverage, and report what actually happened in simulator truth.

## Request budget and acquisition priorities

The following are planning calculations, not bills or vendor quotations. Assume one backend collector, one regional query per interval, an eight-hour session and no failed-request allowance. Dashboard clients read the backend cache.

| Workload | Calculation | Result |
|---|---|---|
| Tracks every 10 seconds | 8 × 3,600 ÷ 10 | 2,880 one-credit requests, leaving 1,120 of a 4,000-credit standard daily state allowance. |
| Tracks every 5 seconds | 8 × 3,600 ÷ 5 | 5,760 credits, exceeding that allowance by 1,760. |
| Anonymous tracks every 10 seconds | 400 × 10 seconds | About 66.7 minutes before exhaustion if no other IP usage consumes credits. |
| Two weather products every minute | 2 × 60 × 8 | 960 requests, averaging two/minute. Polling does not create newer reports. |
| Assumed local track log | 100 actors × 200 bytes × 2,880 samples | 57.6 MB decimal before envelopes, indexes, audio and compression. Actor count and bytes are assumptions. |

OpenSky documents independent states, tracks and flights credit buckets. The proposed box is 0.49 square degrees and falls within the one-credit state tier. Anonymous state history is unavailable, authenticated state lookup reaches only one hour back, and REST excludes MLAT/ADS-C positions. Thus polling REST is not a general historical archive or a complete surface feed. Recheck entitlements and quotas before acquisition. [Current REST limitations and credits](https://openskynetwork.github.io/opensky-api/rest.html)

Separate cash cost, request allowance and reuse rights. Public downloads and keyless weather require no API-key purchase in this plan. Hosting, voice, model inference and preparation are separate. No paid-provider rate was verified for this expansion, so those totals remain unpriced. Calculate model usage as input tokens/1,000,000 × selected input rate plus output tokens/1,000,000 × selected output rate, then add audio in the provider's billing units, storage and minimum subscriptions. Use measured rehearsal usage before buying a plan.

**Acquisition order:** freeze the reviewed KDVT subset and synthetic run first. Obtain one weather sequence and validate its times/units next. Add catalog and footprint references. Then run one short authorized traffic observation to measure missing positions, ground coverage and age distribution. Consider larger historical archives or paid feeds only when they fill a specific demonstration gap. This keeps the demonstration reproducible while live reception, rights and provider cost remain dependencies.
