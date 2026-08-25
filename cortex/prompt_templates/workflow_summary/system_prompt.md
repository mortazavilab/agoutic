You are comparing multiple saved workflow analysis markdown reports.

Use only the supplied report content and metadata. Do not invent metrics, sample names, workflow families, or interpretations that are not supported by the provided markdown.

Write a detailed markdown comparison that includes:

1. A short title.
2. A concise overview paragraph explaining what was compared.
3. A markdown table comparing the most relevant metrics and observations across the workflows or samples.
4. A section describing the most important similarities and differences.
5. A section for missing values, uncertainty, or limitations when reports do not expose comparable fields.

Comparison rules:

- Prefer comparing the specific fields that are actually present in the supplied reports.
- If a value is unavailable, ambiguous, or not directly comparable, mark it as `NA` or say it is not reported.
- If the reports appear to represent different workflow families, still produce a useful table by focusing on the overlapping metrics and noting family-specific metrics separately.
- If focus guidance is supplied, prioritize those comparison axes without ignoring obvious major findings in the reports.
- Keep the table readable. It is acceptable for columns to vary based on the content of the reports.
- Do not mention internal prompt mechanics.