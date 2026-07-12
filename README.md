# Finding Similar Songs with FastPath

A hands-on lab for Neo4j's **FastPath** algorithm (Graph Data Science), using
a Guitar Hero note-chart dataset as a stand-in for the sequential/temporal
data FastPath is built for — customer journeys, clickstreams, event logs,
patient histories, and so on. Each song's note sequence becomes a FastPath
embedding, and we use that to find similar songs, spot near-duplicate charts,
and dig into what the algorithm is actually doing.

**Start here:** [`similar_song_fast_path.ipynb`](similar_song_fast_path.ipynb)
is the lab. Everything else in this repo supports it.

## FastPath is in preview

FastPath is a new GDS algorithm and doesn't have Aura/Neo4j-native docs
published yet. The closest current public reference is the **Snowflake Graph
Analytics** docs, which cover the same algorithm and parameters:

- [FastPath algorithm reference (Snowflake Graph Analytics docs)](https://neo4j.com/docs/snowflake-graph-analytics/current/algorithms/fastpath/#algorithms-fastpath-syntax)

Because it's still in preview, this repo pins `graphdatascience==2.0a1` (a
release candidate) in `requirements.txt` rather than a stable release —
that's expected, not a typo.

## Setup

1. **Python 3.9+** and a virtual environment:

   **With [uv](https://docs.astral.sh/uv/)** (recommended):

   ```bash
   uv venv
   uv pip install -r requirements.txt --python .venv/bin/python
   source .venv/bin/activate
   ```

   **With plain `venv`:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip3 install -r requirements.txt
   ```

2. **Credentials** — copy `.env.example` to `.env` and fill it in:

   ```bash
   cp .env.example .env
   ```

   You'll be given the shared `NEO4J_URI`/`NEO4J_USERNAME`/`NEO4J_PASSWORD`/
   `AURA_INSTANCEID` for the class's shared Aura instance, plus your own
   personal `AURA_CLIENT_ID`/`AURA_CLIENT_SECRET`/`AURA_PROJECT_ID` for
   opening your own Aura Graph Analytics (GDS) session. See the comments in
   `.env.example` for which is which.

3. **Open the notebook** in Jupyter, select the venv you just created as the
   kernel, and follow along — `similar_song_fast_path.ipynb` has the rest of
   the guidance, including discussion prompts and things to try.

## Repo layout

```
similar_song_fast_path.ipynb   the lab
graph_schema.svg               graph model diagram embedded in the notebook
requirements.txt               lab notebook dependencies
.env.example                   credential template (copy to .env)

data/                          pre-class setup only -- not needed for the lab
  README.md                    how the shared dataset was built
  fetch_charts.py              downloads charts from Chorus Encore
  load_songs.py                loads charts into the shared Aura instance
  charts/                      ~300 downloaded song charts (gitignored, not in the repo)
  requirements.txt             dependencies for the two scripts above
```

The song/note graph in the shared Aura instance is already loaded before
class. You won't need to run anything in `data/` during the lab, but it's
there if you want to see how the dataset was built.
