"""Export derived pre-tip research rows into a private repository only."""
import base64
import csv
import io
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

DEST = "bmk3434/cbb-research-data"
BASE = "https://api.github.com/repos/" + DEST
FIELDS = (
    "Run_Timestamp_PT", "Game_Date", "Game_ID", "Start_UTC", "Away_Team", "Home_Team",
    "Neutral_Site", "Venue_Verified", "Provider", "Provider_Status", "Home_Spread",
    "Market_Total", "Home_Moneyline", "Away_Moneyline", "Model_Bet", "Bet_Side",
    "Bet_Spread", "V4_Predicted_ATS_Edge", "V4_Edge", "V4_Confidence", "Tier",
    "Profile_Tag", "Actionable", "QC_Status", "Totals_Bet", "Totals_Model_Total",
    "Totals_Predicted_Edge", "Totals_Edge", "Totals_Qualified", "Totals_Actionable",
    "Totals_QC_Status", "Totals_V2_Bet", "Totals_V2_Model_Total", "Totals_V2_Predicted_Edge",
    "Totals_V2_Edge", "Totals_V2_Qualified_Shadow", "Totals_V2_Trackable_Shadow",
    "Totals_V2_QC_Status", "Totals_V3_Bet", "Totals_V3_Model_Total",
    "Totals_V3_Predicted_Edge", "Totals_V3_Edge", "Totals_V3_Qualified_Shadow",
    "Totals_V3_Trackable_Shadow", "Totals_V3_QC_Status",
)


def parse_time(value):
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("timestamp missing zone")
    return t


def prepare_batch(path, day):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("invalid day")
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=FIELDS)
    writer.writeheader()
    n = 0
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not set(FIELDS).issubset(reader.fieldnames or []):
            raise ValueError("missing required columns")
        for row in reader:
            if row["Game_Date"] != day or not row["Game_ID"].strip():
                raise ValueError("wrong game date or ID")
            if parse_time(row["Run_Timestamp_PT"]) >= parse_time(row["Start_UTC"]):
                raise ValueError("post-tip row; refusing batch")
            writer.writerow({key: row[key] for key in FIELDS})
            n += 1
    data = buf.getvalue().encode("utf-8")
    if len(data) > 900_000:
        raise ValueError("batch exceeds size limit")
    return data, n


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(token, method, path="", data=None):
    req = Request(BASE + path, method=method,
                  data=None if data is None else json.dumps(data).encode(),
                  headers={"Authorization": "Bearer " + token,
                           "Accept": "application/vnd.github+json",
                           "Content-Type": "application/json",
                           "X-GitHub-Api-Version": "2022-11-28",
                           "User-Agent": "cbb-private-research-export"})
    with build_opener(NoRedirect()).open(req, timeout=30) as response:
        return json.load(response)


def export(token, day, run, attempt, data):
    if not re.fullmatch(r"[0-9]+", run) or not re.fullmatch(r"[0-9]+", attempt):
        raise ValueError("invalid workflow run identity")
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("invalid day")
    repo = request(token, "GET")
    if repo.get("full_name") != DEST or repo.get("private") is not True or repo.get("default_branch") != "main":
        raise ValueError("destination identity, privacy or branch check failed")
    path = "/contents/snapshots/" + day + "/" + run + "-" + attempt + ".csv"
    try:
        existing = request(token, "GET", path + "?ref=main")
    except HTTPError as exc:
        if exc.code != 404:
            raise
    else:
        if existing.get("encoding") == "base64" and base64.b64decode(existing["content"]) == data:
            return "already_saved"
        raise ValueError("existing batch differs; refusing overwrite")
    request(token, "PUT", path, {"message": "Archive CBB pre-tip research batch",
                                 "content": base64.b64encode(data).decode("ascii"),
                                 "branch": "main"})
    return "saved"


def main():
    token = os.environ.get("CBB_RESEARCH_TOKEN", "").strip()
    if not token:
        print("RESEARCH_EXPORT=NOT_CONFIGURED")
        return 0
    try:
        day = os.environ["CBB_TARGET_DATE"]
        data, count = prepare_batch(Path("output") / ("all_scored_" + day + ".csv"), day)
        status = export(token, day, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"], data)
        print("RESEARCH_EXPORT=" + status.upper() + " rows=" + str(count))
        return 0
    except HTTPError as exc:
        print("RESEARCH_EXPORT=FAILED http_status=" + str(exc.code))
    except (OSError, ValueError, KeyError, TypeError, URLError):
        print("RESEARCH_EXPORT=FAILED input_or_connection_error")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
