# ADR-003: Gradient-boosted trees over a deep-learning model for failure prediction

**Status**: Accepted

**Context**: The predictive-maintenance task uses tabular, rolling-window
features (avg speed, max engine temp, battery voltage trend, DTC counts,
etc.) with a small positive class (~2-10% of vehicle-days show a failure
signal in the next 7 days).

**Decision**: Use `GradientBoostingClassifier` (scikit-learn) as the
production model, compared against a majority-class baseline and logistic
regression.

**Consequences**:
- (+) Tree ensembles are the well-established strong baseline for tabular
  data with mixed feature scales and non-linear interactions (e.g. battery
  voltage matters differently depending on vehicle age/odometer).
- (+) `feature_importances_` gives directly explainable "top factors" for
  each alert, which fleet managers need to trust and act on a prediction.
- (+) Trains in seconds on commodity hardware even at fleet scale, unlike a
  deep model that would need GPU infrastructure for marginal gain on a
  dataset this structured.
- (−) Ceiling is lower than a deep sequence model (e.g. an LSTM/transformer
  over raw telemetry) if abundant labeled failure data becomes available;
  revisit once real (not simulated) failure labels accumulate.
