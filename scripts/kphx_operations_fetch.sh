#!/bin/bash
# Fetch public KPHX sources; each into its own new subdirectory. Never executes downloaded content.
BASE="/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_operations"
UA="Mozilla/5.0 (research fetch)"
get() { # name url file
  d="$BASE/$1"; mkdir -p "$d"; curl -sSL -m 120 -A "$UA" -o "$d/$3" -w "$1 %{http_code} %{size_download}\n" "$2"; }
get airnav_kphx "https://www.airnav.com/airport/KPHX" kphx.html
get skyharbor_stats_dec2025 "https://www.skyharbor.com/media/ca1p4ojo/monthly-stats-december-2025.pdf" monthly-stats-dec-2025.pdf
get skyharbor_facts "https://www.skyharbor.com/about-phx/news-media/media-kit/facts-numbers/" facts.html
get skyharbor_noise_mar2026 "https://www.skyharbor.com/media/40wi40ah/monthly-noise-report-march2026.pdf" noise-mar2026.pdf
# d-TPP metafile for AIRAC 2611 (effective 2026-10-29 (cycle 2611)): metadata only
get faa_dtpp_2611 "https://aeronav.faa.gov/d-tpp/2611/xml_data/d-TPP_Metafile.xml" d-TPP_Metafile.xml
get faa_cifp_page "https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/download/" cifp.html
get faa_nasr_page "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/" nasr.html
get nws_phx_dust_impacts "https://www.weather.gov/media/psr/Dust/2012/Maxwell_Sky_Harbor_Dust_Impacts.pdf" dust_impacts.pdf
get bts_tarmac_airport "https://www.transtats.bts.gov/TARMAC/tarmacAirportData" tarmac.html
get faa_cifp_2611 "https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip" CIFP_261029.zip
