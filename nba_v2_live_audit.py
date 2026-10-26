#!/usr/bin/env python3
import csv, io, urllib.request
u="https://boxscorelab.com/downloads/2026-27-team-game-results.csv"
try:
 with urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0 NBAResearch"}),timeout=35) as r:
  data=r.read()
 print("FREE CURRENT NBA BOX DATA",len(data),"bytes")
 reader=csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
 rows=list(reader)
 print("HEADER",reader.fieldnames)
 print("ROWS",len(rows),"FIRST",rows[:2],"LAST",rows[-2:])
except Exception as e:
 print("OPTIONAL SOURCE UNAVAILABLE",str(e))
