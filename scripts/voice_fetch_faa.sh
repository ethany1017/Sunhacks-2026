#!/bin/sh
# Fetch FAA public HTML pages (untrusted data; saved only, never executed).
D="/Users/gusschwiesow/Aug OS/Hackathon/data/voice_and_comms"
B=https://www.faa.gov/air_traffic/publications/atpubs
curl -s -A "Mozilla/5.0" "$B/aim_html/chap4_section_2.html" -o "$D/faa_aim_4_2/chap4_section_2.html"
curl -s -A "Mozilla/5.0" "$B/aim_html/chap4_section_1.html" -o "$D/faa_aim_4_1/chap4_section_1.html"
curl -s -A "Mozilla/5.0" "$B/atc_html/chap2_section_4.html" -o "$D/faa_7110_2_4/chap2_section_4.html"
curl -s -A "Mozilla/5.0" "$B/atc_html/chap3_section_1.html" -o "$D/faa_7110_3/chap3_section_1.html"
curl -s -A "Mozilla/5.0" "$B/atc_html/chap3_section_9.html" -o "$D/faa_7110_4/chap3_section_9.html"
curl -s -A "Mozilla/5.0" "$B/atc_html/chap3_section_7.html" -o "$D/faa_7110_3_7/chap3_section_7.html"
curl -s -A "Mozilla/5.0" "$B/atc_html/chap3_section_10.html" -o "$D/faa_7110_3_10/chap3_section_10.html"
curl -s -A "Mozilla/5.0" "$B/atc_html/chap4_section_2.html" -o "$D/faa_7110_4_2/chap4_section_2.html"
curl -s -A "Mozilla/5.0" "$B/atc_html/chap2_section_1.html" -o "$D/faa_7110_2_1/chap2_section_1.html"
curl -s -A "Mozilla/5.0" "$B/atc_html/chap4_section_3.html" -o "$D/faa_7110_5_6/chap4_section_3.html"
for s in 2_1 5_2 3_8; do c=${s%_*}; n=${s#*_}; mkdir -p "$D/faa_7110_x_$s"; curl -s -A "Mozilla/5.0" "$B/atc_html/chap${c}_section_${n}.html" -o "$D/faa_7110_x_$s/chap${c}_section_${n}.html"; done
