#!/usr/bin/env python3
"""Pull Garmin activities and wellness data into plain-English markdown notes.

Built on the open-source python-garminconnect library:
https://github.com/cyberjunky/python-garminconnect
"""

import argparse
import json
import os
import re
import stat
import sys
from datetime import date, timedelta

import garth
from garminconnect import Garmin

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKENSTORE = os.path.join(SCRIPT_DIR, "token")


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def _secure(path):
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


# --- login ---

MFA_CODE_FILE = os.path.join(SCRIPT_DIR, ".mfa_code")
MFA_WAIT_SECONDS = 600


def _wait_for_mfa_code():
    """Poll for a 2FA code dropped into MFA_CODE_FILE (keeps login in one process)."""
    import time

    print("MFA_WAITING", flush=True)
    deadline = time.time() + MFA_WAIT_SECONDS
    while time.time() < deadline:
        if os.path.exists(MFA_CODE_FILE):
            with open(MFA_CODE_FILE) as f:
                code = f.read().strip()
            os.remove(MFA_CODE_FILE)
            if code:
                return code
        time.sleep(2)
    die("Timed out waiting for the 2FA code.")


def cmd_login_start():
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        die("Set GARMIN_EMAIL and GARMIN_PASSWORD environment variables first.")

    if os.path.exists(MFA_CODE_FILE):
        os.remove(MFA_CODE_FILE)

    client = garth.Client()
    client.login(email, password, prompt_mfa=_wait_for_mfa_code)
    client.dump(TOKENSTORE)
    for fname in ("oauth1_token.json", "oauth2_token.json"):
        _secure(os.path.join(TOKENSTORE, fname))
    print("Login successful. Session saved — you won't need to log in again for a while.")


# --- data pull ---

def _login_client():
    if not os.path.isdir(TOKENSTORE):
        die("No saved login found. Run: python sync_garmin.py --login")
    garmin = Garmin()
    garmin.login(TOKENSTORE)
    return garmin


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def fetch_wellness(garmin, day):
    data = {}

    try:
        summary = garmin.get_user_summary(day) or {}
    except Exception:
        summary = {}
    data["restingHeartRate"] = summary.get("restingHeartRate")
    data["minHeartRate"] = summary.get("minHeartRate")
    data["maxHeartRate"] = summary.get("maxHeartRate")
    data["totalSteps"] = summary.get("totalSteps")
    data["dailyStepGoal"] = summary.get("dailyStepGoal")
    data["totalKilocalories"] = summary.get("totalKilocalories")
    data["activeKilocalories"] = summary.get("activeKilocalories")
    data["floorsAscended"] = summary.get("floorsAscended")
    data["averageStressLevel"] = summary.get("averageStressLevel")
    data["maxStressLevel"] = summary.get("maxStressLevel")
    data["bodyBatteryLowestValue"] = summary.get("bodyBatteryLowestValue")
    data["bodyBatteryHighestValue"] = summary.get("bodyBatteryHighestValue")
    data["bodyBatteryMostRecentValue"] = summary.get("bodyBatteryMostRecentValue")
    data["bodyBatteryChargedValue"] = summary.get("bodyBatteryChargedValue")
    data["bodyBatteryDrainedValue"] = summary.get("bodyBatteryDrainedValue")
    data["moderateIntensityMinutes"] = summary.get("moderateIntensityMinutes")
    data["vigorousIntensityMinutes"] = summary.get("vigorousIntensityMinutes")
    data["averageSpo2"] = summary.get("averageSpo2")
    data["avgWakingRespirationValue"] = summary.get("avgWakingRespirationValue")

    try:
        sleep = garmin.get_sleep_data(day) or {}
        dto = sleep.get("dailySleepDTO") or {}
        data["sleepSeconds"] = dto.get("sleepTimeSeconds")
        data["deepSleepSeconds"] = dto.get("deepSleepSeconds")
        data["lightSleepSeconds"] = dto.get("lightSleepSeconds")
        data["remSleepSeconds"] = dto.get("remSleepSeconds")
        data["awakeSleepSeconds"] = dto.get("awakeSleepSeconds")
        overall = (dto.get("sleepScores") or {}).get("overall") or {}
        data["sleepScore"] = overall.get("value")
        data["sleepScoreQualifier"] = overall.get("qualifierKey")
    except Exception:
        pass

    try:
        hrv = garmin.get_hrv_data(day) or {}
        hrv_summary = hrv.get("hrvSummary") or {}
        data["hrvLastNightAvg"] = hrv_summary.get("lastNightAvg")
        data["hrvWeeklyAvg"] = hrv_summary.get("weeklyAvg")
        data["hrvStatus"] = hrv_summary.get("status")
        baseline = hrv_summary.get("baseline") or {}
        data["hrvBaselineLow"] = baseline.get("balancedLow")
        data["hrvBaselineHigh"] = baseline.get("balancedUpper")
    except Exception:
        pass

    try:
        tr = garmin.get_training_readiness(day)
        if isinstance(tr, list) and tr:
            data["trainingReadiness"] = tr[0].get("score")
            data["trainingReadinessLevel"] = tr[0].get("level")
    except Exception:
        pass

    try:
        mm = garmin.get_max_metrics(day)
        if isinstance(mm, list) and mm:
            generic = (mm[0] or {}).get("generic") or {}
            data["vo2Max"] = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
    except Exception:
        pass

    return data


def fetch_activities(garmin, start, end):
    raw = garmin.get_activities(0, 50)
    result = []
    for a in raw:
        start_local = a.get("startTimeLocal", "")
        try:
            a_date = date.fromisoformat(start_local[:10])
        except ValueError:
            continue
        if start <= a_date <= end:
            result.append(a)
    return result


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "activity"


def wellness_markdown(day, w):
    lines = [f"# Garmin wellness {day}"]
    if w.get("restingHeartRate") is not None:
        lines.append(f"- Resting HR: {w['restingHeartRate']} bpm")
    if w.get("hrvLastNightAvg") is not None:
        lines.append(f"- HRV (overnight): {w['hrvLastNightAvg']} ms")
    if w.get("sleepSeconds") is not None:
        hours = w["sleepSeconds"] / 3600
        score = w.get("sleepScore")
        score_str = f" (score {score})" if score is not None else ""
        lines.append(f"- Sleep: {hours:.1f} h{score_str}")
    if w.get("bodyBatteryLowestValue") is not None and w.get("bodyBatteryHighestValue") is not None:
        lines.append(f"- Body battery: {w['bodyBatteryLowestValue']} -> {w['bodyBatteryHighestValue']}")
    if w.get("averageStressLevel") is not None:
        lines.append(f"- Stress (avg): {w['averageStressLevel']}")
    if w.get("totalSteps") is not None:
        lines.append(f"- Steps: {w['totalSteps']}")
    if w.get("trainingReadiness") is not None:
        lines.append(f"- Training readiness: {w['trainingReadiness']}")
    return "\n".join(lines) + "\n"


def activity_markdown(a):
    name = a.get("activityName", "Activity")
    atype = (a.get("activityType") or {}).get("typeKey", "unknown")
    lines = [f"# {name}", f"- Type: {atype}", f"- Start: {a.get('startTimeLocal', '')}"]
    if a.get("duration"):
        lines.append(f"- Duration: {a['duration'] / 60:.0f} min")
    if a.get("distance"):
        lines.append(f"- Distance: {a['distance'] / 1000:.2f} km")
    if a.get("averageHR"):
        lines.append(f"- Avg HR: {a['averageHR']} bpm")
    if a.get("maxHR"):
        lines.append(f"- Max HR: {a['maxHR']} bpm")
    if a.get("calories"):
        lines.append(f"- Calories: {a['calories']}")
    return "\n".join(lines) + "\n"


def print_summary(activities, wellness_by_day):
    print(f"\n{len(activities)} activit(y/ies):")
    for a in activities:
        print(f"  - {a.get('startTimeLocal', '')}: {a.get('activityName', 'Activity')}")
    print(f"\nWellness for {len(wellness_by_day)} day(s):")
    for day, w in sorted(wellness_by_day.items()):
        print(f"\n{wellness_markdown(day, w)}")


def write_files(out_dir, activities, wellness_by_day):
    daily_dir = os.path.join(out_dir, "daily")
    activities_dir = os.path.join(out_dir, "activities")
    os.makedirs(daily_dir, exist_ok=True)
    os.makedirs(activities_dir, exist_ok=True)

    data_path = os.path.join(out_dir, "data.json")
    if os.path.exists(data_path):
        with open(data_path) as f:
            store = json.load(f)
    else:
        store = {"activities": {}, "wellness": {}}

    for day, w in wellness_by_day.items():
        with open(os.path.join(daily_dir, f"{day}.md"), "w") as f:
            f.write(wellness_markdown(day, w))
        store["wellness"][day] = w

    for a in activities:
        activity_id = str(a.get("activityId"))
        day = a.get("startTimeLocal", "")[:10]
        slug = slugify(a.get("activityName", "activity"))
        fname = f"{day}-{activity_id}-{slug}.md"
        with open(os.path.join(activities_dir, fname), "w") as f:
            f.write(activity_markdown(a))
        store["activities"][activity_id] = a

    with open(data_path, "w") as f:
        json.dump(store, f, indent=2, default=str)

    print(f"Wrote {len(wellness_by_day)} daily note(s) and {len(activities)} activity note(s) to {out_dir}/")


def post_to_supabase(activities, wellness_by_day):
    import requests

    url = os.environ.get("GARMIN_INGEST_URL")
    secret = os.environ.get("GARMIN_INGEST_SECRET")
    if not url:
        die("Set GARMIN_INGEST_URL (and GARMIN_INGEST_SECRET) to use --sink supabase.")
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    resp = requests.post(url, json={"activities": activities, "wellness": wellness_by_day}, headers=headers)
    resp.raise_for_status()
    print(f"Posted {len(activities)} activities and {len(wellness_by_day)} wellness days to {url}")


def write_web_json(path, activities, wellness_by_day):
    """Merge into the compact JSON the web app reads (keeps history across runs)."""
    from datetime import datetime

    if os.path.exists(path):
        with open(path) as f:
            store = json.load(f)
    else:
        store = {"wellness": {}, "activities": {}}

    for day, w in wellness_by_day.items():
        # Don't wipe an already-filled day with an empty pull (watch not synced yet)
        if any(v is not None for v in w.values()) or day not in store["wellness"]:
            store["wellness"][day] = w

    keep = (
        "activityId", "activityName", "startTimeLocal", "activityType", "duration",
        "distance", "averageHR", "maxHR", "calories", "averageSpeed", "elevationGain",
        "aerobicTrainingEffect", "anaerobicTrainingEffect", "avgStrokeDistance",
        "poolLength", "activeLengths",
    )
    for a in activities:
        slim = {k: a.get(k) for k in keep if a.get(k) is not None}
        if isinstance(slim.get("activityType"), dict):
            slim["activityType"] = slim["activityType"].get("typeKey")
        store["activities"][str(a.get("activityId"))] = slim

    store["generated"] = datetime.now().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(store, f, separators=(",", ":"), default=str)
    print(f"Web-JSON aktualisiert: {path}")


def cmd_sync(args):
    garmin = _login_client()
    end = date.today()
    start = end - timedelta(days=args.days - 1)

    activities = fetch_activities(garmin, start, end)
    wellness_by_day = {d.isoformat(): fetch_wellness(garmin, d.isoformat()) for d in daterange(start, end)}

    if args.dry_run:
        print_summary(activities, wellness_by_day)
        return

    if args.sink == "files":
        write_files(args.out, activities, wellness_by_day)
    elif args.sink == "supabase":
        post_to_supabase(activities, wellness_by_day)

    if args.web_json:
        write_web_json(args.web_json, activities, wellness_by_day)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="Log in (reads GARMIN_EMAIL/GARMIN_PASSWORD; if 2FA is on, waits for the code to appear in .mfa_code)")
    parser.add_argument("--login-mfa", metavar="CODE", help="Supply the 2FA code to a waiting --login process")
    parser.add_argument("--days", type=int, default=3, help="How many recent days to pull")
    parser.add_argument("--dry-run", action="store_true", help="Print results instead of writing files")
    parser.add_argument("--sink", choices=["files", "supabase"], default="files")
    parser.add_argument("--out", default=os.path.join(SCRIPT_DIR, "garmin"), help="Output folder for --sink files")
    parser.add_argument("--web-json", metavar="PATH", help="Also merge data into this JSON file for the web app")
    args = parser.parse_args()

    if args.login:
        cmd_login_start()
    elif args.login_mfa:
        with open(MFA_CODE_FILE, "w") as f:
            f.write(args.login_mfa.strip())
        _secure(MFA_CODE_FILE)
        print("Code handed to the waiting login process.")
    else:
        cmd_sync(args)


if __name__ == "__main__":
    main()
