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

## Setup (Google Colab, recommended)

This lab runs in **Google Colab**, so there's nothing to install on your own
machine.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/smithna/clone-hero-fastpath/blob/main/similar_song_fast_path.ipynb)

1. <u>Open the notebook</u>: click the **Open in Colab** badge above (or in
   Colab choose *File > Open notebook > GitHub* and paste this repo's URL).

2. <u>Store your credentials as Colab Secrets</u>: open the **key icon** in the
   left sidebar and add one secret per value, using these exact names:
   `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`,
   `AURA_INSTANCEID`, `AURA_CLIENT_ID`, `AURA_CLIENT_SECRET`, `AURA_PROJECT_ID`.
   Toggle **Notebook access** on for each. You'll be handed the shared
   `NEO4J_*`/`AURA_INSTANCEID` values for the class's Aura instance; the
   `AURA_CLIENT_ID`/`AURA_CLIENT_SECRET`/`AURA_PROJECT_ID` are your own personal
   Aura API credentials, used to open your own Aura Graph Analytics (GDS)
   session. Secrets persist across your Colab sessions, so you only enter them
   once.

3. <u>Run the setup cell</u>: the first cell installs the lab dependencies into
   the Colab runtime. The credentials cell then reads your Colab Secrets
   automatically (and prompts you for anything it can't find). From there, just
   follow the notebook.

## Setup (local Jupyter, fallback)

Prefer to run locally? The notebook detects a local kernel and skips the Colab
install step, reading credentials from a `.env` file instead.

1. <u>Python 3.9+ and a virtual environment</u>:

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

2. <u>Credentials</u>: copy `.env.example` to `.env` and fill it in:

   ```bash
   cp .env.example .env
   ```

   See the comments in `.env.example` for which values are shared and which are
   personal.

3. <u>Open the notebook</u>: launch Jupyter, select the venv you just created as
   the kernel, and follow along. `similar_song_fast_path.ipynb` has the rest of
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
