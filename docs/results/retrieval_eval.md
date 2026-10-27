# Retrieval evaluation

22 labelled queries over the generated day (seed 7); relevance from ground-truth entities (data/eval/retrieval_queries.yaml). k = 10. Raw data: `retrieval_eval.json`.

| method | hit@10 (norm.) | MRR | nDCG@10 | nDCG category (n=1) | nDCG exact (n=10) | nDCG paraphrase (n=11) | median latency ms |
|---|---|---|---|---|---|---|---|
| keyword (FTS5/BM25) | 0.795 | 0.909 | 0.822 | 1.0 | 0.863 | 0.768 | 0.12 |
| semantic (tfidf) | 0.742 | 0.864 | 0.768 | 0.564 | 0.928 | 0.641 | 0.11 |
| hybrid (BM25 + tfidf) | 0.852 | 0.886 | 0.846 | 0.852 | 0.969 | 0.734 | 0.28 |

## Per query (nDCG@10)

| query | style | keyword (FTS5/BM25) | semantic (tfidf) | hybrid (BM25 + tfidf) |
|---|---|---|---|---|
| blue Ford F150 | exact | 1.00 | 1.00 | 1.00 |
| F-150 pickup | exact | 0.36 | 0.80 | 0.82 |
| pickup trucks | category | 1.00 | 0.56 | 0.85 |
| Chevy Silverado | exact | 0.87 | 0.87 | 0.87 |
| someone hanging around the entrance late at night | paraphrase | 0.00 | 0.00 | 0.00 |
| person climbing the fence | exact | 1.00 | 0.61 | 1.00 |
| break-in at the warehouse | paraphrase | 0.00 | 0.00 | 0.00 |
| masked intruder | paraphrase | 1.00 | 1.00 | 1.00 |
| parcel left at the front door | paraphrase | 0.47 | 0.47 | 0.47 |
| suspicious bag abandoned near the gate | paraphrase | 0.98 | 0.88 | 0.97 |
| gate left open | exact | 0.64 | 1.00 | 1.00 |
| warehouse door open | exact | 1.00 | 1.00 | 1.00 |
| dark SUV arriving with its lights off | paraphrase | 1.00 | 0.70 | 0.92 |
| delivery van | exact | 1.00 | 1.00 | 1.00 |
| wild animals in the garden | paraphrase | 1.00 | 0.00 | 0.71 |
| security guard | exact | 0.77 | 1.00 | 1.00 |
| man carrying a ladder | exact | 1.00 | 1.00 | 1.00 |
| workers using a forklift | paraphrase | 1.00 | 1.00 | 1.00 |
| people trying to open doors | paraphrase | 1.00 | 1.00 | 1.00 |
| the property manager's white Toyota | paraphrase | 1.00 | 1.00 | 1.00 |
| gardener | exact | 1.00 | 1.00 | 1.00 |
| courier dropping off a parcel | paraphrase | 1.00 | 1.00 | 1.00 |
