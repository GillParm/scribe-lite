# Run 1 (checker prompt v1)

The first full evaluation run, kept for comparison. With the first claim-check prompt,
4 of the 12 planted errors (a wrong side, a wrong dose and duration, a wrong temperature
and a wrong dose) were labelled partially_supported instead of unsupported. The UI flags
both labels, but a contradicted number is an error, not a partial truth, so the prompt
was changed to make any contradicted detail unsupported. Everything in eval/results/
outside this folder comes from the run after that change.
