"""In-house model-training harnesses for AuralPrimer.

Subpackages here build and train our OWN weights on permissively licensed
corpora so the shipped product does not depend on external research checkpoints
whose weights, data, or ShareAlike obligations are unsuitable for defaults.
Nothing in here is imported by the runtime transcription pipeline; it is
offline tooling invoked by hand (or CI) to produce exportable checkpoints.
"""
