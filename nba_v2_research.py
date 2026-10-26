#!/usr/bin/env python3
"""Inspect a free upstream NBA game-level historical boxscore dataset."""
from __future__ import annotations
import json
import urllib.request
from io import BytesIO
import pandas as pd

BASE="https://raw.githubusercontent.com/llimllib/nba_data/main/data/espn/"
for filename in ("team_box.parquet","four_factors.parquet"):
    url=BASE+filename
    with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"NBAResearch/1.0"}),timeout=60) as response:
        raw=response.read()
    frame=pd.read_parquet(BytesIO(raw))
    print("DATASET",filename,"BYTES",len(raw),"SHAPE",frame.shape)
    print("COLUMNS",list(frame.columns))
    print("DTYPES",frame.dtypes.astype(str).to_dict())
    print("FIRST_ROWS",frame.head(2).to_json(orient="records",date_format="iso"))
    print("LAST_ROWS",frame.tail(2).to_json(orient="records",date_format="iso"))
    print("NULL_COUNTS",frame.isna().sum().sort_values(ascending=False).head(15).to_dict())
