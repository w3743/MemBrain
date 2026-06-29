# EASM — Evidence-Adaptive Spaced Memory

EASM is BrainMemory's evidence-driven memory algorithm. Its implementation is
split across `models.py`, `strength.py`, `evolution.py`, `retrieval.py`, and
`engine.py`.

## State

Each memory keeps activation \(R_0\), stability \(S\), difficulty \(D\),
utility \(U\), Beta trust parameters \(a,b\), retrieval bias \(B\), and
exposure/correction counters. Trust is \(T=a/(a+b)\).

Defaults are \(R_0=0.6\), \(S=\ln(3)/(2\cdot0.02)\), \(D=0.5\),
\(U=0.5\), and \(a=b=2\).

## Continuous decay and interference

\[
R(t)=\frac{2}{1+(2/R_0-1)e^{2d_{\mathrm{eff}}t}}
\]

\[
d_{\mathrm{eff}}=\frac{\ln3}{2S}(1+0.25D)(1+0.6I)
\]

Interference comes from newer, semantically similar conflicting memories:

\[
I_i=\min\left(1,\sum_j sim(i,j)^2p_{\mathrm{conflict}}(i,j)e^{-\Delta t_j/30}\right)
\]

## Evidence feedback

An explicit used-memory ID receives \(p_{\mathrm{use}}=0.98\). The local
fallback combines an entailment proxy, distinctive-token overlap, and Jaccard
overlap:

\[
p_{\mathrm{use}}=\sigma(-2.5+3e+1.5l+j)
\]

A topic-matched correction receives \(p_{\mathrm{correct}}=0.95\):

\[
p_{\mathrm{ignore}}=(1-p_{\mathrm{use}})(1-p_{\mathrm{correct}})
\]

The action is used above 0.75, corrected above 0.7, ignored above 0.75, and
otherwise uncertain. Uncertain evidence changes no learned parameter.
Confidence is one minus normalized three-way entropy. Every observation is
persisted in `memory_feedback_events`.

## Successful recall

\[
R'_0=R+0.35p_{\mathrm{use}}(1-R)
\]

\[
\frac{\Delta S}{S}=
0.45p_{\mathrm{use}}\cdot
[0.15+1.85(1-R)^{1.25}]\cdot
(0.5+D)\cdot
(1-S/730)
\]

The product implements desirable difficulty and saturation. Spaced successful
recall grows stability more than massed repetition, while stability approaches
a two-year ceiling. Difficulty decreases after effortful recall. Beta trust
receives weak positive evidence and utility uses an exponential moving average.

## Ignored and corrected feedback

Ignored memories are penalized only when classification confidence is high.
Their bias, stability, difficulty, and utility receive small
confidence-weighted updates.

Corrections add strong negative Beta evidence, reduce stability and utility,
increase difficulty, and increment correction counts. `SUPERSEDE` creates the
replacement, inherits part of the learned state, and deletes the old row in one
SQLite transaction.

## Retrieval

Candidate generation unions dense vector top-100, FTS5/BM25 top-100, and
utility top-30. Ranking uses:

\[
\sigma(-2+3s+1.2k+1.2R+0.5T+0.8U+0.4B-1.5C)
\]

where \(s\) is semantic similarity, \(k\) is keyword evidence, and \(C\) is
conflict risk. MMR with redundancy weight 0.25 selects the final prompt set.

## Consolidation

An active memory is archived only when:

\[
R<0.2,\quad age>7\text{ days},\quad Ue^{-age/90}<0.4
\]

Legacy superseded rows are physically deleted.

## Migration

Existing databases migrate in place. Legacy `decay_rate` is converted to
stability, scalar trust is converted to Beta evidence, and the new state
columns plus feedback-event table are added automatically.
