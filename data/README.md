# Data

The experiment package does not redistribute the SNAP collaboration graphs.
Download the two public inputs from the SNAP dataset collection:

- [ca-GrQc](https://snap.stanford.edu/data/ca-GrQc.html)
- [ca-AstroPh](https://snap.stanford.edu/data/ca-AstroPh.html)

Place the extracted files at:

```text
data/raw/ca-GrQc.txt
data/raw/ca-AstroPh.txt
```

The preparation script removes self-loops and duplicate undirected edges,
then `generate_costs.py` adds the fixed independent Gaussian resource block
used by the experiments. Generated files under `data/processed/` and sampled
cost tables under `scratch/` are local intermediates and are not required in
the public repository.
