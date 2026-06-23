Build a website that can show the opportunitis of the ads customers to our sales, and they can manage the opportunity based on these information.

Supported features:
1. You need to render every opportunity as a table with the following columns: customer, industry, product, current spend, est. uplift, confidence.
2. Header summary: visible count + total est. uplift; currency = USD, no decimals ($42,000), this applies to the row amounts too.
3. Support search with customer name + rationale, case-insensitive. Support multi-term search, the search box is space-separated terms, a row matches only if all terms appear.
4. Support filters in these columns: industry/product/confidence/status; status uses the per-row state.
5. Sort by these columns: uplift & spend descending, confidence High>Medium>Low; default = uplift desc; sort options labelled so "uplift"/"spend"/"confidence". When two opportunites have the same confidence, break the tie by estimated uplift, highest first.
6. Status include: New, Contacted, Won and Lost -- default is New.
7. Assignee is a dropdown with the sales name (come from the provided reps data src/data/reps.ts), default is "unassigned".
8. Any change should be persisted and reload automatically after refresh.
9. Support priority sort option: if the sort option contains priority. Priority = round( (estUpliftMonthly / currentSpendMonthly) × confidenceWeight × 1000 ), where confidenceWeight is High = 1.0, Medium = 0.7, Low = 0.4. Sort descending; ties broken by estimated uplift, highest first.

Notes:
1. Stable data-testid hooks: search, filter-industry, filter-product, filter-confidence, filter-status, sort, summary-count, summary-uplift, opp-row, row-status, row-assignee
2. Opportunities come from the src/data/opportunities.ts.
3. The feed may have thousands of rows, so render the table efficiently.
