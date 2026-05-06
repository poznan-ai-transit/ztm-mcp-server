"""
Fetch active service alerts (disruptions, detours, notices) from ZTM Poznań.

Requires: requests, gtfs-realtime-bindings
    pip install requests gtfs-realtime-bindings
"""

import datetime

import requests
from google.transit import gtfs_realtime_pb2

BASE_URL = "https://www.ztm.poznan.pl"
RT_ENDPOINT = f"{BASE_URL}/pl/dla-deweloperow/getGtfsRtFile"

CAUSE_NAMES = {v: k for k, v in gtfs_realtime_pb2.Alert.Cause.items()}
EFFECT_NAMES = {v: k for k, v in gtfs_realtime_pb2.Alert.Effect.items()}


def fetch_alerts() -> gtfs_realtime_pb2.FeedMessage:
    resp = requests.get(RT_ENDPOINT, params={"file": "feeds.pb"}, timeout=10)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


def first_translation(translated: gtfs_realtime_pb2.TranslatedString, lang: str = "pl") -> str:
    for t in translated.translation:
        if t.language == lang:
            return t.text
    if translated.translation:
        return translated.translation[0].text
    return ""


def main():
    feed = fetch_alerts()

    ts = datetime.datetime.fromtimestamp(feed.header.timestamp)
    print(f"Feed timestamp : {ts}")

    alerts = [e for e in feed.entity if e.HasField("alert")]
    print(f"Active alerts  : {len(alerts)}")

    for i, entity in enumerate(alerts, 1):
        alert = entity.alert
        header = first_translation(alert.header_text)
        description = first_translation(alert.description_text)

        affected_routes = [
            ie.route_id for ie in alert.informed_entity if ie.route_id
        ]
        affected_stops = [
            ie.stop_id for ie in alert.informed_entity if ie.stop_id
        ]

        cause = CAUSE_NAMES.get(alert.cause, str(alert.cause))
        effect = EFFECT_NAMES.get(alert.effect, str(alert.effect))

        print(f"\n{'─' * 60}")
        print(f"[{i}] {header}")
        if description:
            # Trim long descriptions for readability
            short_desc = description[:200] + ("…" if len(description) > 200 else "")
            print(f"    {short_desc}")
        print(f"    cause={cause}  effect={effect}")
        if affected_routes:
            print(f"    routes: {', '.join(affected_routes[:10])}")
        if affected_stops:
            print(f"    stops : {', '.join(affected_stops[:10])}")
        for tr in alert.active_period:
            start = datetime.datetime.fromtimestamp(tr.start) if tr.start else None
            end = datetime.datetime.fromtimestamp(tr.end) if tr.end else None
            print(f"    period: {start} → {end}")


if __name__ == "__main__":
    main()
