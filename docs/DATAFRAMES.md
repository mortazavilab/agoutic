# DataFrames In Chat

AGOUTIC can keep tabular results directly inside a conversation as chat
dataframes. These dataframes can be inspected, transformed in memory, plotted,
and saved to memory for reuse.

---

## Inspect Existing DataFrames

These commands are deterministic and bypass the LLM.

| Command | What it does |
|---------|--------------|
| `list dfs` | Lists all visible conversation dataframes with their labels, row counts, and columns |
| `head DF5` | Shows the first 10 rows of DF5 |
| `head DF5 20` | Shows the first 20 rows of DF5 |
| `head c2c12DF` | Shows the first rows of a remembered named dataframe |

Named remembered dataframes saved with `/remember-df` appear in `list dfs` and
can be referenced by name in later prompts.

---

## Transform DataFrames In Memory

AGOUTIC supports common dataframe actions directly against conversation data.
These operations create new chat dataframes rather than mutating the original
one in place.

### Filter And Subset

- `subset DF3 to rows where reads > 100`
- `filter DF2 where sample == C2C12`
- `subset DF4 to columns sample, modification, reads`
- `keep only sample, assay, target in DF7`

### Rename And Sort

- `rename DF2 columns old_reads to reads and old_sample to sample`
- `rename column count to reads in DF5`
- `sort DF4 by reads descending`

### Reshape And Aggregate

- `reshape DF1 into long format with columns sample, modification, reads`
- `melt DF1 with id vars sample and value columns sampleA, sampleB`
- `summarize DF4 by sample and sum reads`
- `group DF6 by condition and mean score`

### Join And Pivot

- `join DF3 and DF5 on sample`
- `left join DF3 and DF5 on sample`
- `pivot DF6 index sample columns modification values reads`

---

## Pending DataFrame Actions

Sometimes AGOUTIC may save a dataframe action for confirmation instead of
running it immediately. In that case a `PENDING_ACTION` block appears in the
chat.

- Use `Apply Action` to execute that exact saved transform.
- Use `Dismiss` to reject it.
- This is block-specific and avoids ambiguity when multiple pending dataframe
  actions exist in the conversation.

---

## Plot DataFrames

AGOUTIC renders inline Plotly charts from conversation dataframes.

This is the interactive dataframe plotting path only. For differential
expression plots that need saved image artifacts or baked-in annotations, use
the DE skill's edgePython `generate_plot` flow instead of dataframe `[[PLOT]]`
tags.

### Common Examples

- `plot DF5 by assay`
- `make a histogram of DF2 using score`
- `make a scatter plot of DF3 with read_length vs alignment_score`
- `make a grouped bar chart of DF4 by modification color by sample`
- `plot this color by sample`

Supported chart families:

- bar
- histogram
- scatter
- box
- heatmap
- pie

Current scope note:

- Dataframe plots are interactive exploration views.
- Publication-quality PNG/SVG artifact generation is currently provided by
  edgePython for specialized DE/enrichment plots such as volcano and MD, not by
  generic dataframe bar/scatter/histogram rendering.

### Wide Sample Tables And Auto-Melt

If your dataframe is wide, for example one column per sample, AGOUTIC can still
render grouped plots when you ask for `color by sample`. The UI renderer can
auto-melt wide numeric sample columns into long form for grouped bar, box, and
histogram-like displays.

Example wide input:

| Modification | sampleA | sampleB | sampleC |
|--------------|---------|---------|---------|
| KNOWN | 10 | 12 | 11 |
| NIC | 5 | 6 | 4 |

Prompt:

- `make a grouped bar chart of this dataframe by modification color by sample`

---

## Save DataFrames To Memory

You can keep a dataframe beyond the current conversation.

- `/remember-df DF5`
- `/remember-df DF5 as c2c12DF`

After saving a named dataframe, you can use it in later prompts such as:

- `head c2c12DF`
- `plot c2c12DF by assay`

---

## Practical Tips

- Treat `DFn` references as immutable snapshots from earlier chat turns.
- If you want a transform to preserve the original table, ask for a new result
  rather than overwriting by name.
- Use explicit column names when possible; AGOUTIC resolves columns
  case-insensitively but exact names reduce ambiguity.
- For grouped sample charts, ask in analysis terms like `color by sample`
  instead of describing implementation details.